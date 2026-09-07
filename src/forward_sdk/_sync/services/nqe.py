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

from forward_sdk._generated.models import NqeQuery, NqeRunResult
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
from forward_sdk.nqe.query_ref import QueryRef

__all__ = ["NqeExecution", "NqeService"]

Row = dict[str, Any]

# Forward asks clients not to poll execution status more than once every five
# seconds. Early polls are faster, because short queries are common and waiting
# five seconds for one that finished in 200ms is a poor default.
POLL_INTERVAL = 5.0
INITIAL_POLL_INTERVAL = 0.5

TERMINAL_STATUS = "COMPLETED"
SUCCESS_OUTCOME = "OK"


def _rows_from_payload(payload: Any) -> tuple[list[Row], int | None]:
    """Extract rows and the reported total from a result page.

    Forward has returned rows both bare and wrapped in a ``fields`` object, so
    both are unwrapped here.
    """
    if not isinstance(payload, dict):
        return [], None
    items = payload.get("items") or []
    rows = [
        item["fields"] if isinstance(item, dict) and "fields" in item else item for item in items
    ]
    return rows, payload.get("totalNumItems")


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
    ) -> None:
        self._service = service
        self.key = key
        self.network_id = network_id
        self._status: dict[str, Any] = dict(status or {})

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

    def status(self) -> dict[str, Any]:
        """Fetch the execution's current status."""
        payload = self._service._send_json(
            ops.execution_status(network_id=self.network_id, execution_key=self.key)
        )
        self._status = dict(payload or {})
        self._service._transport.counters.increment("nqe_polls")
        return self._status

    def wait(
        self,
        *,
        poll_interval: float = POLL_INTERVAL,
        timeout: float | None = 1800.0,
    ) -> dict[str, Any]:
        """Poll until the execution finishes.

        Returns:
            The final status.

        Raises:
            ForwardExecutionError: The query finished without producing results,
                because it failed, timed out on the server, or was canceled.
            ForwardTimeoutError: ``timeout`` elapsed first. The query keeps
                running on Forward; the handle stays usable.
        """
        deadline = None if timeout is None else time.monotonic() + timeout
        interval = min(INITIAL_POLL_INTERVAL, poll_interval)

        while True:
            status = self.status()
            if status.get("status") == TERMINAL_STATUS:
                outcome = status.get("outcome")
                if outcome and outcome != SUCCESS_OUTCOME:
                    raise ForwardExecutionError(
                        f"NQE execution finished with outcome {outcome}: "
                        f"{_execution_error_detail(status)}",
                        execution_key=self.key,
                        status=str(status.get("status")),
                        outcome=str(outcome),
                    )
                return status

            if deadline is not None and time.monotonic() >= deadline:
                raise ForwardTimeoutError(
                    f"NQE execution {self.key} did not finish within {timeout}s "
                    f"(last status {status.get('status')!r}); it is still running on Forward"
                )

            time.sleep(interval)
            interval = min(poll_interval, interval * 2)

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
                yield row["fields"] if isinstance(row, dict) and "fields" in row else row


def _is_ndjson(response: Any) -> bool:
    content_type = response.headers.get("content-type", "")
    return "ndjson" in content_type or "jsonl" in content_type


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
    ) -> NqeExecution:
        """Start a query in the background and return a handle to it."""
        ref = _as_ref(query)
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
        return NqeExecution(self, key=str(key), network_id=resolved_network, status=data)

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
        before_snapshot_id: str,
        after_snapshot_id: str,
        query: QueryRef | str,
        *,
        page_size: int = DEFAULT_PAGE_SIZE,
        guards: PageGuards | None = None,
    ) -> list[dict[str, Any]]:
        """Compare a query's results between two snapshots.

        Only a committed query can be diffed; Forward has no way to diff query
        source it has never seen.
        """
        ref = _as_ref(query)
        if ref.mode == "text":
            raise ForwardConfigurationError(
                "a snapshot diff needs a committed query: use QueryRef.by_id() or "
                "by_path(), not inline query source"
            )
        if not ref.is_runnable:
            ref = self.resolve(ref)

        query_id = ref.effective_query_id
        assert query_id is not None  # guaranteed by is_runnable for non-text refs
        tracker = PageTracker(guards, page_size=page_size)
        entries: list[dict[str, Any]] = []
        while True:
            payload = self._send_json(
                ops.diff(
                    before_snapshot_id=before_snapshot_id,
                    after_snapshot_id=after_snapshot_id,
                    query_id=query_id,
                    commit_id=ref.effective_commit_id,
                    offset=tracker.offset,
                    limit=tracker.page_size,
                    parameters=ref.parameters or None,
                )
            )
            data = payload or {}
            page = list(data.get("rows") or [])
            entries.extend(page)
            if tracker.observe(page, data.get("totalNumRows")) is Decision.DONE:
                return entries

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
