# Generated from src/forward_sdk/_async/services/nqe.py by scripts/unasync.py -- do not edit.
# Edit the async source and re-run: uv run python scripts/unasync.py

"""Running NQE queries.

Forward offers two ways to run a query, and the difference matters:

* :meth:`NqeService.run` posts the query and waits for the result in one
  request. Simple, but the request is held open for the whole run.
* :meth:`NqeService.execute` starts the query in the background and returns
  a handle to poll and then read. This is what long-running or large queries
  need, and it is the only path that can stream results.

:meth:`NqeService.query` wraps the second into a single call for the common
case of "run this and give me every row".
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator, Mapping, Sequence
from typing import Any

from forward_sdk._generated.models import (
    NqeDiffEntry,
    NqeDiffResult,
    NqeExecutionStatus,
    NqeQuery,
    NqeRunResult,
)
from forward_sdk._http import parse_retry_after
from forward_sdk._ops import nqe as ops
from forward_sdk._sync.services._base import Service
from forward_sdk._sync.services.nqe_repo import NqeRepository
from forward_sdk._sync.transport import Transport
from forward_sdk.errors import (
    ForwardConfigurationError,
    ForwardExecutionError,
    ForwardTimeoutError,
)
from forward_sdk.nqe.pagination import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    Decision,
    PageGuards,
    PageTracker,
)
from forward_sdk.nqe.query_ref import QueryRef, SortKey
from forward_sdk.nqe.telemetry import ExecutionReport, ExecutionReports

__all__ = ["NqeExecution", "NqeService"]

Row = dict[str, Any]

# Forward asks clients not to poll execution status more than once every five
# seconds. Early polls are faster, because short queries are common and waiting
# five seconds for one that finished in 200ms is a poor default.
POLL_INTERVAL = 5.0
INITIAL_POLL_INTERVAL = 0.5

TERMINAL_STATUS = "COMPLETED"
SUCCESS_OUTCOME = "OK"

# Forward reports its own budget for an execution as `timeoutMinutes`. Polling
# past that only confirms a timeout the server has already decided, so waiting
# stops there, with a small grace so the client does not give up first on a
# clock that runs slightly ahead.
SERVER_DEADLINE_GRACE = 60.0


def _rows_from_payload(payload: Any) -> tuple[list[Row], int | None]:
    """Extract rows and the reported total from a result page.

    Rows arrive exactly as the query selected them. Forward's serializer holds a
    row in a field it calls ``fields`` internally but writes the row's own
    entries at the top level, so no envelope reaches the wire and none is
    stripped here. Stripping one would corrupt the query
    ``select {fields: {...}}``, whose rows have a single key named ``fields``
    and are indistinguishable from the envelope they were mistaken for.
    """
    if not isinstance(payload, dict):
        return [], None
    items = payload.get("items") or []
    return list(items), payload.get("totalNumItems")


class NqeExecution:
    """A query running in the background on Forward.

    Obtained from :meth:`NqeService.execute`. Call :meth:`wait` to block
    until it finishes, then :meth:`rows` or :meth:`stream` to read the results.
    """

    def __init__(
        self,
        service: NqeService,
        *,
        key: str,
        network_id: str,
        status: Mapping[str, Any] | None = None,
        query_label: str | None = None,
        reports: ExecutionReports | None = None,
    ) -> None:
        self._service = service
        self.key = key
        self.network_id = network_id
        self._status: dict[str, Any] = dict(status or {})
        self._started = time.monotonic()
        self._retry_after: float | None = None
        # Retained separately from the value used for the next sleep: that one
        # is cleared when a response arrives without the header, which would
        # otherwise erase the evidence that Forward ever asked us to slow down.
        self._last_retry_after: float | None = None
        self._polls = 0
        self._poll_sleep = 0.0
        self._terminal_reason: str | None = None
        self._query_label = query_label
        self._reports = reports

    def __repr__(self) -> str:
        return f"<NqeExecution key={self.key!r} status={self.last_status!r}>"

    @property
    def last_status(self) -> str | None:
        """The most recent status seen, without making a request."""
        value = self._status.get("status")
        return str(value) if value is not None else None

    @property
    def rows_produced(self) -> int | None:
        value = self._status.get("rowsProduced")
        return int(value) if value is not None else None

    @property
    def millis_executing(self) -> int | None:
        """How long Forward reports it has spent on this query."""
        value = self._status.get("millisExecuting")
        return int(value) if value is not None else None

    @property
    def is_finished(self) -> bool:
        """Whether the last status seen was terminal, without making a request."""
        return self._status.get("status") == TERMINAL_STATUS

    @property
    def timeout_minutes(self) -> int | None:
        """The budget Forward allows this execution, in minutes."""
        value = self._status.get("timeoutMinutes")
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    @property
    def server_deadline(self) -> float | None:
        """When Forward's own budget for this execution runs out.

        A ``time.monotonic()`` value, or ``None`` if Forward has not said.
        """
        minutes = self.timeout_minutes
        if minutes is None:
            return None
        return self._started + minutes * 60.0 + SERVER_DEADLINE_GRACE

    def status(self) -> NqeExecutionStatus:
        """Fetch the execution's current status."""
        response = self._service._transport.send(
            ops.execution_status(network_id=self.network_id, execution_key=self.key)
        )
        # Forward may ask to be polled less often; obeying that is better than
        # the client's own schedule, and the transport already honours the same
        # header on retries.
        self._retry_after = parse_retry_after(response.headers.get("retry-after"))
        if self._retry_after is not None:
            self._last_retry_after = self._retry_after
        payload = response.json() if response.content else {}
        self._status = dict(payload or {})
        self._polls += 1
        self._service._transport.counters.increment("nqe_polls")
        return NqeExecutionStatus.model_validate(self._status)

    def wait(
        self,
        *,
        poll_interval: float = POLL_INTERVAL,
        timeout: float | None = 1800.0,
    ) -> NqeExecutionStatus:
        """Poll until the execution finishes.

        Returns:
            The final status.

        Raises:
            ForwardExecutionError: The query finished without producing results,
                because it failed, timed out on the server, or was canceled.
            ForwardTimeoutError: ``timeout`` elapsed first. The query keeps
                running on Forward; the handle stays usable.
        """
        local_deadline = None if timeout is None else time.monotonic() + timeout
        interval = min(INITIAL_POLL_INTERVAL, poll_interval)

        # A short query can already be finished in the response that started it,
        # in which case there is nothing to poll for.
        if not self.is_finished:
            self.status()
        status = self._status

        while True:
            if status.get("status") == TERMINAL_STATUS:
                outcome = status.get("outcome")
                if outcome and outcome != SUCCESS_OUTCOME:
                    self._record(str(outcome))
                    raise ForwardExecutionError(
                        f"NQE execution finished with outcome {outcome}: "
                        f"{_execution_error_detail(status)}",
                        execution_key=self.key,
                        status=str(status.get("status")),
                        outcome=str(outcome),
                    )
                self._record(str(outcome or SUCCESS_OUTCOME))
                return NqeExecutionStatus.model_validate(status)

            self._check_deadlines(local_deadline, timeout, status)

            # Forward's own pacing request wins over the local schedule.
            delay = self._retry_after or interval
            self._poll_sleep += delay
            time.sleep(delay)
            interval = min(poll_interval, interval * 2)
            self.status()
            status = self._status

    def _check_deadlines(
        self,
        local_deadline: float | None,
        timeout: float | None,
        status: Mapping[str, Any],
    ) -> None:
        """Stop waiting once either the caller's or Forward's budget is spent.

        Forward reports its own budget for the query, and once that is gone the
        execution can only end in a timeout, so continuing to poll would just
        confirm it slowly. Reporting which budget ran out matters: one means
        raise the caller's timeout, the other means make the query cheaper.
        """
        now = time.monotonic()
        last = status.get("status")

        if local_deadline is not None and now >= local_deadline:
            self._record("CLIENT_TIMEOUT")
            raise ForwardTimeoutError(
                f"NQE execution {self.key} did not finish within {timeout}s "
                f"(last status {last!r}); it is still running on Forward"
            )

        server_deadline = self.server_deadline
        if server_deadline is not None and now >= server_deadline:
            budget = status.get("timeoutMinutes")
            self._record("SERVER_BUDGET_EXPIRED")
            raise ForwardTimeoutError(
                f"NQE execution {self.key} passed the {budget}-minute budget Forward "
                f"allows it (last status {last!r}); the query needs to do less work"
            )

    @property
    def report(self) -> ExecutionReport:
        """What happened to this execution, so far."""
        return ExecutionReport(
            execution_key=self.key,
            network_id=self.network_id,
            snapshot_id=_optional_str(self._status.get("snapshotId")),
            query=self._query_label,
            millis_executing=self.millis_executing,
            rows_produced=self.rows_produced,
            timeout_minutes=self.timeout_minutes,
            poll_count=self._polls,
            poll_sleep_seconds=round(self._poll_sleep, 3),
            retry_after_seconds=self._last_retry_after,
            terminal_reason=self._terminal_reason,
            wall_seconds=round(time.monotonic() - self._started, 3),
        )

    def _record(self, reason: str) -> None:
        """Retain this execution's record on the client.

        Called once, on whichever path ends the wait, so a caller using the
        convenience wrapper still gets telemetry without holding the handle.
        """
        if self._terminal_reason is not None:
            return
        self._terminal_reason = reason
        if self._reports is not None:
            self._reports.record(self.report)

    def result_page(self, *, offset: int = 0, limit: int | None = None) -> NqeRunResult:
        """Fetch one page of results as the API returns it."""
        payload = self._service._send_json(
            ops.execution_result(
                network_id=self.network_id,
                execution_key=self.key,
                offset=offset,
                limit=limit,
            )
        )
        return NqeRunResult.model_validate(payload or {})

    def rows(
        self,
        *,
        page_size: int = MAX_PAGE_SIZE,
        guards: PageGuards | None = None,
    ) -> Iterator[Row]:
        """Iterate every result row, a page at a time.

        Prefer this to :meth:`stream` when a transient failure should be
        retried: each page is a separate request, so one can be re-sent, whereas
        an interrupted stream cannot be resumed.
        """
        tracker = PageTracker(guards, page_size=page_size)
        counters = self._service._transport.counters
        while True:
            payload = self._service._send_json(
                ops.execution_result(
                    network_id=self.network_id,
                    execution_key=self.key,
                    offset=tracker.offset,
                    limit=tracker.page_size,
                )
            )
            page, total = _rows_from_payload(payload)
            counters.increment("nqe_pages")
            counters.increment("nqe_rows", len(page))
            for row in page:
                yield row
            if tracker.observe(page, total) is Decision.DONE:
                return

    def stream(self) -> Iterator[Row]:
        """Stream every result row as newline-delimited JSON.

        Lower latency and lower memory than :meth:`rows` because rows are
        yielded as they arrive, but the request cannot be resumed if the
        connection drops partway.
        """
        spec = ops.execution_stream(network_id=self.network_id, execution_key=self.key)
        counters = self._service._transport.counters
        with self._service._transport.stream(spec) as response:
            if "json" in response.headers.get("content-type", "") and not _is_ndjson(response):
                # An older deployment answered with a single JSON document.
                rows, _ = _rows_from_payload(json.loads(response.read()))
                for row in rows:
                    counters.increment("nqe_rows")
                    yield row
                return
            for line in response.iter_lines():
                if not line.strip():
                    continue
                row = json.loads(line)
                counters.increment("nqe_rows")
                yield row


def _is_ndjson(response: Any) -> bool:
    content_type = response.headers.get("content-type", "")
    return "ndjson" in content_type or "jsonl" in content_type


def _optional_str(value: Any) -> str | None:
    return str(value) if value not in (None, "") else None


def _execution_error_detail(status: Mapping[str, Any]) -> str:
    error = status.get("error")
    if isinstance(error, Mapping):
        return str(error.get("message") or error.get("reason") or error)
    return str(error) if error else "no detail reported"


class NqeService(Service):
    """Run queries and read the query library."""

    def __init__(self, transport: Transport) -> None:
        super().__init__(transport)
        self.repo = NqeRepository(transport)
        self._reports = ExecutionReports()

    def run(
        self,
        query: QueryRef | str,
        *,
        network_id: str | None = None,
        snapshot_id: str | None = None,
        offset: int = 0,
        limit: int | None = DEFAULT_PAGE_SIZE,
        column_filters: Sequence[Mapping[str, Any]] | None = None,
        sort_by: Mapping[str, Any] | None = None,
    ) -> NqeRunResult:
        """Run a query and return one page of results.

        Args:
            query: A :class:`QueryRef`, or query source as a string.
            network_id: Defaults to the client's network.
            snapshot_id: Defaults to the network's latest processed snapshot.
            limit: Rows per page, at most 10,000.
        """
        ref = _as_ref(query)
        if not ref.is_runnable:
            ref = self.resolve(ref)
        spec = ops.run_query(
            ref,
            network_id=self._network(network_id),
            snapshot_id=self._snapshot(snapshot_id),
            offset=offset,
            limit=limit,
            column_filters=column_filters,
            sort_by=sort_by,
        )
        payload = self._send_json(spec)
        self._transport.counters.increment("nqe_runs")
        return NqeRunResult.model_validate(payload or {})

    def execute(
        self,
        query: QueryRef | str,
        *,
        network_id: str | None = None,
        snapshot_id: str | None = None,
        column_filters: Sequence[Mapping[str, Any]] | None = None,
        parameters: Mapping[str, Any] | None = None,
        sort_keys: Sequence[SortKey | str] | None = None,
    ) -> NqeExecution:
        """Start a query in the background and return a handle to it.

        ``parameters`` and ``sort_keys`` are conveniences for the common case;
        :class:`QueryRef` carries the same things when a reference is built once
        and reused.
        """
        ref = _as_ref(query)
        if parameters:
            ref = ref.with_parameters(**parameters)
        if sort_keys:
            ref = ref.with_sort(*sort_keys)
        if not ref.is_runnable:
            ref = self.resolve(ref)
        resolved_network = self._network(network_id)
        payload = self._send_json(
            ops.start_execution(
                ref,
                network_id=resolved_network,
                snapshot_id=self._snapshot(snapshot_id),
                column_filters=column_filters,
            )
        )
        data = dict(payload or {})
        key = data.get("executionKey")
        if not key:
            raise ForwardExecutionError(f"Forward accepted {ref} but returned no execution key")
        self._transport.counters.increment("nqe_executions")
        return NqeExecution(
            self,
            key=str(key),
            network_id=resolved_network,
            status=data,
            query_label=str(ref),
            reports=self._reports,
        )

    def query(
        self,
        query: QueryRef | str,
        *,
        network_id: str | None = None,
        snapshot_id: str | None = None,
        page_size: int = MAX_PAGE_SIZE,
        guards: PageGuards | None = None,
        timeout: float | None = 1800.0,
        stream: bool = False,
    ) -> list[Row]:
        """Run a query in the background and return every row.

        The common case: start the query, wait for it, and collect the results.

        Args:
            stream: Read results as a single stream rather than paging. Faster
                for large result sets, but a dropped connection cannot be
                resumed.
        """
        execution = self.execute(query, network_id=network_id, snapshot_id=snapshot_id)
        execution.wait(timeout=timeout)
        if stream:
            return [row for row in execution.stream()]
        return [row for row in execution.rows(page_size=page_size, guards=guards)]

    def diff(
        self,
        query: QueryRef | str,
        *,
        before: str,
        after: str,
        page_size: int = DEFAULT_PAGE_SIZE,
        guards: PageGuards | None = None,
    ) -> list[NqeDiffEntry]:
        """Compare a query's results between two snapshots.

        The query comes first, matching :meth:`run` and :meth:`execute`, and the
        snapshots are keyword-only because nothing in a pair of ids says which
        is which.

        Only a committed query can be diffed; Forward has no way to diff query
        source it has never seen.

        Args:
            query: A committed query, by id or library path.
            before: The snapshot to compare from.
            after: The snapshot to compare to.

        Returns:
            One entry per changed row. ``before`` and ``after`` are independent
            and either may be absent: a row added between the snapshots has no
            ``before``, a removed one has no ``after``.
        """
        ref = self._diffable(query)
        self._transport.counters.increment("nqe_diff_calls")
        tracker = PageTracker(guards, page_size=page_size)
        entries: list[NqeDiffEntry] = []
        while True:
            data = self._diff_page(
                ref, before=before, after=after, offset=tracker.offset, limit=tracker.page_size
            )
            page = list(data.get("rows") or [])
            entries.extend(NqeDiffEntry.model_validate(row) for row in page)
            if tracker.observe(page, data.get("totalNumRows")) is Decision.DONE:
                return entries

    def diff_page(
        self,
        query: QueryRef | str,
        *,
        before: str,
        after: str,
        offset: int = 0,
        limit: int | None = DEFAULT_PAGE_SIZE,
    ) -> NqeDiffResult:
        """Compare two snapshots and return one page of changes.

        The counterpart to :meth:`run` for diffs. :meth:`diff` pages to
        completion, which is the wrong shape for showing an operator the first
        rows that changed: that would fetch the whole diff to display fifty rows.
        Page guards cannot stand in for this, because a page ceiling raises
        rather than stopping, which is right for a ceiling and wrong for a limit.

        ``totalNumRows`` reports the size of the whole diff, so a caller can show
        how much was not fetched.
        """
        ref = self._diffable(query)
        self._transport.counters.increment("nqe_diff_calls")
        data = self._diff_page(ref, before=before, after=after, offset=offset, limit=limit)
        return NqeDiffResult.model_validate(data)

    def _diffable(self, query: QueryRef | str) -> QueryRef:
        """Resolve a reference to something Forward can diff."""
        ref = _as_ref(query)
        if ref.mode == "text":
            raise ForwardConfigurationError(
                "a snapshot diff needs a committed query: use QueryRef.by_id() or "
                "by_path(), not inline query source"
            )
        if not ref.is_runnable:
            ref = self.resolve(ref)
        return ref

    def _diff_page(
        self,
        ref: QueryRef,
        *,
        before: str,
        after: str,
        offset: int,
        limit: int | None,
    ) -> Mapping[str, Any]:
        query_id = ref.effective_query_id
        assert query_id is not None  # guaranteed by is_runnable for non-text refs
        payload = self._send_json(
            ops.diff(
                before_snapshot_id=before,
                after_snapshot_id=after,
                query_id=query_id,
                commit_id=ref.effective_commit_id,
                offset=offset,
                limit=limit,
                parameters=ref.parameters or None,
            )
        )
        self._transport.counters.increment("nqe_diff_pages")
        return payload or {}

    def execution_reports(self) -> list[ExecutionReport]:
        """What happened to each execution this client has run.

        Retained on the client rather than only on the handle, because the
        convenience wrapper :meth:`query` does not hand the handle back, and a
        sync fanning many queries across threads asks once at the end rather
        than holding each one.

        Bounded and thread-safe; reading it never raises.
        """
        return self._reports.all()

    def queries(self, directory: str | None = None) -> list[NqeQuery]:
        """List queries in the library."""
        payload = self._send_json(ops.list_queries(directory=directory))
        rows = payload if isinstance(payload, list) else (payload or {}).get("queries", [])
        return [NqeQuery.model_validate(row) for row in rows]

    def resolve(self, query: QueryRef | str) -> QueryRef:
        """Look up a library path and return a reference that can be run."""
        ref = _as_ref(query)
        if ref.is_runnable:
            return ref
        assert ref.path is not None
        entry = self.repo.find(ref.path, repository=ref.repository)
        return ref.resolved(entry.query_id, ref.commit_id or entry.commit_id)


def _as_ref(query: QueryRef | str) -> QueryRef:
    return query if isinstance(query, QueryRef) else QueryRef.inline(query)
