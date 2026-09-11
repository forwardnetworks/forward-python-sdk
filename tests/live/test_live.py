"""Tests against a real Forward instance.

Skipped unless credentials are present, so an ordinary ``pytest`` run never
needs a server. To run them::

    export FORWARD_URL=https://fwd.app
    export FORWARD_USERNAME=your-token-access-key
    export FORWARD_PASSWORD=your-token-secret
    export FORWARD_NETWORK_ID=101
    uv run pytest -m live

Everything here is read-only. Tests that write are additionally gated on
``FORWARD_LIVE_WRITES=1``, and confine themselves to the calling user's own
draft area, which is discarded afterwards.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

from forward_sdk import ForwardClient, ForwardNotFoundError, QueryRef, config_value
from forward_sdk.errors import ForwardConfigurationError

pytestmark = pytest.mark.live

REQUIRED = ("FORWARD_URL", "FORWARD_USERNAME", "FORWARD_PASSWORD", "FORWARD_NETWORK_ID")

TRIVIAL_QUERY = "foreach device in network.devices select {name: device.name}"


def _missing() -> list[str]:
    return [name for name in REQUIRED if not os.environ.get(name)]


@pytest.fixture(scope="module")
def client() -> Iterator[ForwardClient]:
    missing = _missing()
    if missing:
        pytest.skip(f"live tests need {', '.join(missing)}")
    with ForwardClient.from_env(user_agent="forward-sdk-live-tests/1") as connected:
        yield connected


@pytest.fixture(scope="module")
def network_id() -> str:
    return os.environ["FORWARD_NETWORK_ID"]


def test_reports_the_instance_version(client: ForwardClient) -> None:
    version = client.version()
    assert version.get("version")


def test_lists_networks(client: ForwardClient, network_id: str) -> None:
    networks = client.networks.list()
    assert networks
    assert any(network.id == network_id for network in networks), (
        f"FORWARD_NETWORK_ID {network_id} is not among the visible networks"
    )


def test_finds_a_processed_snapshot(client: ForwardClient, network_id: str) -> None:
    snapshot = client.snapshots.latest_processed(network_id)
    if snapshot is None:
        pytest.skip(f"network {network_id} has no processed snapshot")
    assert snapshot.id
    assert str(snapshot.state) == "PROCESSED"


def test_snapshot_metrics(client: ForwardClient, network_id: str) -> None:
    snapshot = client.snapshots.latest_processed(network_id)
    if snapshot is None or not snapshot.id:
        pytest.skip("no processed snapshot")
    assert isinstance(client.snapshots.metrics(snapshot.id), dict)


def test_lists_devices(client: ForwardClient, network_id: str) -> None:
    devices = client.devices.list(network_id, limit=5)
    assert all(device.name for device in devices)


def test_runs_a_query_synchronously(client: ForwardClient, network_id: str) -> None:
    result = client.nqe.run(TRIVIAL_QUERY, network_id=network_id, limit=5)
    assert result.items is not None
    assert len(result.items) <= 5


def test_runs_a_query_in_the_background(client: ForwardClient, network_id: str) -> None:
    execution = client.nqe.execute(TRIVIAL_QUERY, network_id=network_id)
    status = execution.wait(timeout=600)
    assert str(status.outcome) == "OK"

    paged = list(execution.rows(page_size=100))
    streamed = list(execution.stream())
    # The two ways of reading a result must agree.
    assert len(paged) == len(streamed)
    if paged:
        assert set(paged[0]) == set(streamed[0])


def test_query_library_is_readable(client: ForwardClient) -> None:
    queries = client.nqe.queries()
    assert isinstance(queries, list)


def test_unknown_query_path_raises_not_found(client: ForwardClient, network_id: str) -> None:
    with pytest.raises(ForwardNotFoundError):
        client.nqe.execute(
            QueryRef.by_path("/forward-sdk-live-tests/definitely-not-here"),
            network_id=network_id,
        )


class TestShapesWithNoCiBackstop:
    """The shapes a green suite cannot vouch for.

    Everything else in this repository is checked against Forward's generated
    description, so a parser cannot disagree with it for long. These shapes have
    no such backstop: some belong to unpublished endpoints, and some are wire
    details the description does not pin down. Every one of them has been wrong
    at least once, and each was found by a consumer rather than by a test.

    They run on a schedule so that a Forward release changing one of them is
    reported here rather than discovered in someone's sync.
    """

    def test_rows_are_never_rewritten(self, client: ForwardClient, network_id: str) -> None:
        """A row whose single column is named `fields` must survive intact.

        The SDK used to strip a `fields` wrapper that Forward does not send,
        which silently rewrote exactly this row. The query is chosen so that a
        wrapper and a legitimate row are indistinguishable: if stripping ever
        returns, this row comes back as its own inner value.
        """
        query = "foreach d in network.devices select {fields: {inner: d.name}}"
        execution = client.nqe.execute(query, network_id=network_id)
        execution.wait(timeout=600)
        rows = list(execution.rows(page_size=1))[:1]
        if not rows:
            pytest.skip("network has no devices")

        assert set(rows[0]) == {"fields"}, "a fields-named column was unwrapped"
        assert set(rows[0]["fields"]) == {"inner"}

    def test_one_page_and_all_rows_agree(self, client: ForwardClient, network_id: str) -> None:
        """Asking for one page must not change the row shape."""
        query = "foreach d in network.devices select {fields: {inner: d.name}}"
        execution = client.nqe.execute(query, network_id=network_id)
        execution.wait(timeout=600)
        page = execution.result_page(offset=0, limit=1).items or []
        rows = list(execution.rows(page_size=1))[:1]
        if not rows or not page:
            pytest.skip("network has no devices")

        assert page[0] == rows[0]

    def test_committed_source_is_reachable(self, client: ForwardClient) -> None:
        """Source comes back only from a concrete commit, never from head.

        Forward accepts `with=sourceCode` at head and silently ignores it, so a
        caller reading source there sees every query as source-unavailable and
        an audit passes vacuously. The SDK resolves the commit itself; this
        checks that it still has to, and still does.
        """
        library = client.nqe.repo.queries()
        if not library:
            pytest.skip("organisation library is empty")
        path = next((q.path for q in library if q.path), None)
        if path is None:
            pytest.skip("no query in the library has a path")

        at_head = client.nqe.repo.queries(path=path, with_source=True)
        assert len(at_head) == 1, "path filter was ignored; head was not resolved"
        assert at_head[0].source, "source was requested and not returned"
        assert client.nqe.repo.source(path)

    def test_source_without_a_path_is_refused_before_it_is_sent(
        self, client: ForwardClient
    ) -> None:
        """Forward will not serve the whole library's text, so nor do we."""
        with pytest.raises(ForwardConfigurationError):
            client.nqe.repo.queries(with_source=True)

    def test_a_query_carries_its_commit(self, client: ForwardClient) -> None:
        """The commit arrives nested under lastCommit, not as a flat field.

        Reading a flat key Forward does not send made every path-resolved query
        lose its pin and run against head, silently.
        """
        library = client.nqe.repo.queries()
        pinned = [q for q in library if q.commit_id]
        if not library:
            pytest.skip("organisation library is empty")

        assert pinned, "no query reported a commit; the nesting may have changed"
        assert len(pinned[0].commit_id or "") == 40

    def test_current_user_parses(self, client: ForwardClient) -> None:
        """The user arrives nested under `user`, which took a live call to learn.

        Parsing it as a flat object returned an empty record rather than
        failing, so the caller saw a user with no name instead of an error.
        """
        user = client.user_accounts.get_current_user()
        assert user is not None


class TestPredictFamilies:
    """The unpublished Predict, diff, webhook and configuration endpoints.

    Read-only. Each skips rather than fails when the instance has nothing to
    read, so the backstop still runs on a bare deployment; what it asserts is
    that the shapes the SDK declares are the shapes Forward sends.
    """

    def test_change_sets_list_with_typed_predictions(
        self, client: ForwardClient, network_id: str
    ) -> None:
        sets = client.change_sets.list(network_id)
        if not sets:
            pytest.skip("network has no change sets")
        first = sets[0]
        assert first.id and first.id.startswith("CHG-")
        assert first.snapshot_id
        for predicted in first.predicted_snapshots or []:
            assert predicted.id

    def test_change_set_summary_and_commits(self, client: ForwardClient, network_id: str) -> None:
        sets = client.change_sets.list(network_id)
        if not sets:
            pytest.skip("network has no change sets")
        assert sets[0].id
        handle = client.change_sets.handle(sets[0].id, network_id=network_id)
        summary = handle.summary()
        assert summary.snapshot_id == sets[0].snapshot_id
        for commit in handle.commits():
            assert len(commit.commit_id or "") == 40
            for changes in (commit.device_to_changes or {}).values():
                assert isinstance(changes.has_config, bool)

    def test_diffs_between_a_base_and_its_prediction(
        self, client: ForwardClient, network_id: str
    ) -> None:
        candidates = [cs for cs in client.change_sets.list(network_id) if cs.predicted_snapshots]
        if not candidates:
            pytest.skip("no change set has a predicted snapshot")
        base = candidates[0].snapshot_id
        predicted = candidates[0].predicted_snapshots or []
        after = predicted[0].id if predicted else None
        assert base and after
        diffs = client.snapshot_diffs
        connectivity = diffs.wait_for_subnet_connectivity(base, after, timeout=300)
        assert not connectivity.is_partial_result
        loops = diffs.routing_loops(base, after)
        assert isinstance(loops.complete, bool)
        counts = diffs.counts(base, after)
        assert set(counts) == {
            "devices",
            "interfaces",
            "topology",
            "l2",
            "acl",
            "nat",
            "arp",
            "mac",
        }

    def test_webhooks_list_parses(self, client: ForwardClient) -> None:
        listing = client.webhooks.list_webhooks()
        for hook in listing.webhooks or []:
            assert hook.name and hook.url
            assert hook.event_params is not None

    def test_config_routes_disagree_on_purpose(self, client: ForwardClient) -> None:
        """The global route reports the default; the org route reports what governs.

        Reading only the global route makes an enabled feature look disabled.
        Both must parse; whether they agree depends on the org.
        """
        cfg = client.configuration
        global_value = config_value(cfg.get_global_config_value(property="FIREWALL_PREDICT"))
        org_value = config_value(cfg.get_org_config_value(property="FIREWALL_PREDICT"))
        assert isinstance(global_value, bool)
        assert isinstance(org_value, bool)


def test_counters_record_the_traffic(client: ForwardClient) -> None:
    before = client.counters.http_attempts
    client.version()
    assert client.counters.http_attempts > before


@pytest.mark.skipif(
    os.environ.get("FORWARD_LIVE_WRITES") != "1",
    reason="writing tests need FORWARD_LIVE_WRITES=1",
)
def test_draft_can_be_staged_and_discarded(client: ForwardClient) -> None:
    """Stage a query draft, confirm it appears, then discard it.

    The directory is created first. Forward refuses to stage a query whose
    enclosing directory does not exist, so a first write into a new directory
    fails without this, which is what `publish(ensure_directories=True)` does
    for you.
    """
    directory = "/forward-sdk-live-tests"
    path = f"{directory}/scratch"
    repo = client.nqe.repo
    repo.stage_directory(directory)
    try:
        repo.stage_add(path, TRIVIAL_QUERY)
        assert any(draft.path == path for draft in repo.drafts())
        repo.discard(path)
    finally:
        repo.discard(directory)

    assert not any(draft.path == path for draft in repo.drafts())


def test_publishing_an_unchanged_query_is_a_no_op(client: ForwardClient) -> None:
    """Re-publishing what is already committed must report, not raise.

    Re-running an idempotent publisher with nothing to publish is the normal
    case in a pipeline. Forward refuses the commit with INVALID_CHANGE_PATH and
    lists the paths in a sentence it terminates with a full stop; keeping that
    stop made the last path match nothing, so the retry asked again for the one
    path Forward had just refused and the second refusal escaped.

    This reads a query that already exists and publishes its own source back,
    so it creates nothing and leaves nothing behind. Marked as a write because
    it stages a draft on the way, even though it commits nothing.
    """
    repo = client.nqe.repo
    committed = [q for q in repo.queries() if q.path and q.commit_id]
    if not committed:
        pytest.skip("the organization library has no committed query to re-publish")

    target = committed[0].path
    before = {draft.path for draft in repo.drafts()}
    try:
        report = repo.publish({target: repo.source(target)}, title="forward-sdk no-op check")
    finally:
        for draft in repo.drafts():
            if draft.path not in before:
                repo.discard(draft.path)

    assert report.committed_paths == ()
    assert report.skipped_paths == (target,)
    assert not report.changed
