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
        # A prediction that has not finished processing is refused with 409
        # SNAPSHOT_UNAVAILABLE, correctly, so pick one that has. Compare the
        # state exactly: "UNPROCESSED" ends with "PROCESSED".
        base = after = None
        for info in client.change_sets.list(network_id):
            if not info.predicted_snapshots or not info.id:
                continue
            handle = client.change_sets.handle(info.id, network_id=network_id)
            processed = [
                snap
                for snap in handle.predicted_snapshots()
                if str(snap.state) == "PROCESSED" and snap.id
            ]
            if processed:
                base, after = info.snapshot_id, processed[0].id
                break
        if not (base and after):
            pytest.skip("no change set has a processed predicted snapshot")
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

    def test_diff_details_and_directories_parse(
        self, client: ForwardClient, network_id: str
    ) -> None:
        tree = client.change_set_directories.list_change_set_directories(network_id=network_id)
        for ids in tree.model_dump(by_alias=True).values():
            assert isinstance(ids, list)
        base = after = None
        for info in client.change_sets.list(network_id):
            if not info.predicted_snapshots or not info.id:
                continue
            handle = client.change_sets.handle(info.id, network_id=network_id)
            processed = [
                snap
                for snap in handle.predicted_snapshots()
                if str(snap.state) == "PROCESSED" and snap.id
            ]
            if processed:
                base, after = info.snapshot_id, processed[0].id
                break
        if not (base and after):
            pytest.skip("no change set has a processed predicted snapshot")
        details = client.snapshot_diff_details
        stats = details.get_interface_diff_stats(before_snapshot_id=base, after_snapshot_id=after)
        for device in stats.device_infos or []:
            assert device.stats is not None
        files = details.get_file_diff_count(before_snapshot_id=base, after_snapshot_id=after)
        assert isinstance(files.complete, bool)

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


class TestSyntheticDevicesAndDashboards:
    """Adjacent networks, T-API containers, NQE panels and dashboards.

    All unpublished. Reads run everywhere; the writes need
    ``FORWARD_LIVE_WRITES=1`` and clean up after themselves: the synthetic
    devices go into a scratch network that is deleted at the end, and the
    dashboard and panel are deleted by name.
    """

    def test_reads_have_the_declared_envelopes(
        self, client: ForwardClient, network_id: str
    ) -> None:
        adjacent = client.adjacent_networks.get_adjacent_networks(network_id=network_id)
        assert isinstance(adjacent.adjacent_networks, list)
        assert isinstance(client.tapi_network_containers.list(network_id), list)
        for dashboard in client.dashboards.list_dashboards(network_id=network_id):
            assert dashboard.type == "COMPOSED"
        defaults = client.dashboards.list_default_dashboards(network_id=network_id)
        assert defaults and all(int(d.id or "0") < 0 for d in defaults)
        for panel in client.nqe_panels.list_nqe_panels().panels or []:
            assert panel.config is not None and panel.config.type in ("TABULAR", "METRIC")
            assert panel.usage_count is not None

    @pytest.mark.skipif(
        os.environ.get("FORWARD_LIVE_WRITES") != "1",
        reason="writing tests need FORWARD_LIVE_WRITES=1",
    )
    def test_synthetic_devices_round_trip_in_a_scratch_network(self, client: ForwardClient) -> None:
        scratch = client.networks.create("forward-sdk-live-synthetic")
        nid = str(scratch.id)
        try:
            an = {
                "name": "partner-net",
                "connections": [
                    {
                        "name": "partner-link",
                        "uplinkPort": {"device": "edge-01", "port": "Ethernet1"},
                        "subnetAutoDiscovery": "NONE",
                        "subnets": ["192.0.2.0/24"],
                    }
                ],
                "ownedSubnets": ["192.0.2.0/24"],
            }
            client.adjacent_networks.put_adjacent_network(
                network_id=nid, device_name="partner-net", body=an
            )
            patched = client.adjacent_networks.patch_adjacent_network(
                network_id=nid,
                device_name="partner-net",
                body={"ownedSubnets": ["198.51.100.0/24"]},
            )
            assert patched.owned_subnets == ["198.51.100.0/24"]
            removed = client.adjacent_networks.delete_adjacent_network_connections(
                network_id=nid, device_name="partner-net", uplink_device="edge-01"
            )
            assert [c.name for c in removed.connections] == ["partner-link"]
            client.adjacent_networks.delete_adjacent_network(
                network_id=nid, device_name="partner-net"
            )
            assert (
                client.adjacent_networks.get_adjacent_networks(network_id=nid).adjacent_networks
                == []
            )

            client.l2vpns.add_l2_vpns(
                network_id=nid,
                body=[
                    {
                        "name": "metro-a",
                        "connections": [
                            {"name": "a", "device": "sw1", "port": "Ethernet1", "vlan": 200}
                        ],
                    }
                ],
            )
            assert [v.name for v in client.l2vpns.get_l2_vpns(network_id=nid).l2_vpns] == [
                "metro-a"
            ]

            client.tapi_network_containers.put("optical-core", [("t.json", b"{}")], network_id=nid)
            assert client.tapi_network_containers.get("optical-core", network_id=nid).model == {}
            client.tapi_network_containers.delete_all(network_id=nid)
            assert client.tapi_network_containers.list(network_id=nid) == []
        finally:
            client.networks.delete(nid)

    @pytest.mark.skipif(
        os.environ.get("FORWARD_LIVE_WRITES") != "1",
        reason="writing tests need FORWARD_LIVE_WRITES=1",
    )
    def test_dashboard_and_panel_round_trip(self, client: ForwardClient, network_id: str) -> None:
        queries = [q for q in client.nqe.repo.queries() if (q.query_id or "").startswith("Q_")]
        if not queries:
            pytest.skip("org library has no committed queries")
        query_id = queries[0].query_id
        assert query_id
        execution = client.nqe.execute(QueryRef.by_id(query_id), network_id=network_id)
        execution.wait(timeout=300)
        result_key = execution.result_key()
        assert result_key and result_key.startswith("R_") and len(result_key) == 22
        rows = list(execution.rows())
        columns = list(rows[0]) if rows else ["name"]

        panel_name = "forward-sdk-live-panel"
        dashboard_name = "forward-sdk-live-dashboard"
        for stale in client.nqe_panels.list_nqe_panels().panels or []:
            if stale.name == panel_name:
                client.nqe_panels.delete_nqe_panel(panel_id=str(stale.id))
        for stale_dashboard in client.dashboards.list_dashboards(network_id=network_id):
            if stale_dashboard.name == dashboard_name:
                client.dashboards.delete_dashboard(
                    network_id=network_id, dashboard_id=str(stale_dashboard.id)
                )

        panel = client.nqe_panels.create_nqe_panel(
            body={
                "name": panel_name,
                "queryId": query_id,
                "config": {
                    "type": "TABULAR",
                    "columnOrder": columns,
                    "visibleColumns": [{"name": columns[0], "width": 150}],
                },
            }
        )
        dashboard_id = None
        try:
            assert panel.id
            renamed = client.nqe_panels.update_nqe_panel(
                panel_id=str(panel.id), body={"displayName": "Live test"}
            )
            assert renamed.display_name == "Live test"
            dashboard_id = client.dashboards.create_dashboard(
                network_id=network_id, body={"name": dashboard_name}
            )
            assert isinstance(dashboard_id, str)
            client.dashboards.update_dashboard(
                network_id=network_id,
                dashboard_id=dashboard_id,
                body={
                    "layout": [
                        {
                            "type": "PANEL",
                            "x": 0,
                            "y": 0,
                            "w": 6,
                            "h": 4,
                            "panelId": f"NQE_PANEL_{panel.id}",
                        }
                    ]
                },
            )
            saved = client.dashboards.get_dashboard(
                network_id=network_id, dashboard_id=dashboard_id
            )
            assert saved.layout and saved.layout[0].panel_id == f"NQE_PANEL_{panel.id}"
            assert saved.layout[0].id is not None, "Forward assigns each embedding an id"
            client.dashboards.remove_panels_from_dashboards(
                network_id=network_id, body={"panelIds": [str(panel.id)]}
            )
            cleared = client.dashboards.get_dashboard(
                network_id=network_id, dashboard_id=dashboard_id
            )
            assert not cleared.layout
        finally:
            if dashboard_id:
                client.dashboards.delete_dashboard(network_id=network_id, dashboard_id=dashboard_id)
            client.nqe_panels.delete_nqe_panels(body={"panelIds": [str(panel.id)]})
