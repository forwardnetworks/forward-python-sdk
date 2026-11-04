# Generated from tests/_async/test_gap_routes.py by scripts/unasync.py -- do not edit.
# Edit the async source and re-run: uv run python scripts/unasync.py

"""Routes brought over from the Go SDK: the ones whose wire shape is a decision.

Most of the new surface is derived from the spec and covered by the generated
conformance and smoke tests. These are the ones with a trap: bytes that must not
become JSON, a path segment with a slash in it, and a query key that selects the
operation.
"""

from __future__ import annotations

import pytest

from forward_sdk._sync.client import ForwardClient
from forward_sdk.errors import ForwardConfigurationError
from tests.conftest import Recorder, json_response

pytestmark = pytest.mark.anyio


def make_client(recorder: Recorder) -> ForwardClient:
    return ForwardClient(
        "https://forward.test",
        username="key",
        password="secret",
        rate_limit_rpm=None,
        network_id="101",
        transport=recorder.transport,
    )


class TestPerformanceUpload:
    def test_the_payload_goes_out_as_raw_bytes(self, recorder: Recorder) -> None:
        recorder.add("POST", "/api/networks/101/performance", json_response(None, status=204))
        payload = b"\x08\x01\xff\x00protobuf"
        with make_client(recorder) as client:
            client.performance_collection.upload_performance_data(body=payload)

        request = recorder.requests[-1]
        assert request.content == payload
        assert request.headers["content-type"] == "application/octet-stream"

    def test_a_non_bytes_body_is_refused_before_sending(self, recorder: Recorder) -> None:
        with make_client(recorder) as client:
            with pytest.raises(TypeError, match="bytes"):
                client.performance_collection.upload_performance_data(body={"a": 1})

        assert recorder.requests == []


class TestInterfaces:
    def test_a_slash_in_the_interface_name_is_encoded(self, recorder: Recorder) -> None:
        recorder.add(
            "GET",
            "/api/networks/101/devices/sw1/interfaces/Ethernet1%2F1",
            json_response({"name": "Ethernet1/1", "type": "L3"}),
        )
        with make_client(recorder) as client:
            interface = client.device_interfaces.get_device_interface(
                device_name="sw1", interface_name="Ethernet1/1", snapshot_id="900"
            )

        assert interface.name == "Ethernet1/1"
        assert recorder.query_for() == {"snapshotId": ["900"]}


class TestChangeSetCheckPatch:
    def test_patch_sends_the_body_to_the_check(self, recorder: Recorder) -> None:
        recorder.add(
            "PATCH",
            "/api/networks/101/change-sets/CHG-1/checks/CSC-7",
            json_response(None, status=204),
        )
        with make_client(recorder) as client:
            client.change_set_checks.update_change_set_check(
                change_set_id="CHG-1", check_id="CSC-7", body={"name": "renamed"}
            )

        assert recorder.body_for() == {"name": "renamed"}


class TestProtocolModeling:
    def test_the_view_key_selects_affected_devices(self, recorder: Recorder) -> None:
        recorder.add(
            "GET",
            "/api/internal/snapshots/900/protocol-modeling",
            json_response({"error": "No affected devices since this is a base snapshot"}),
        )
        with make_client(recorder) as client:
            client.protocol_modeling.list_predict_affected_devices(snapshot_id="900")

        assert recorder.query_for() == {"view": ["affected-devices"]}


class TestChangeSetHandle:
    def test_commit_sends_the_analysis_mode_only_when_given(self, recorder: Recorder) -> None:
        path = "/api/networks/101/change-sets/CHG-1/commits"
        recorder.add("POST", path, json_response({"commitId": "c1"}))
        with make_client(recorder) as client:
            handle = client.change_sets.handle("CHG-1")
            handle.commit("plain")
            assert recorder.query_for() == {"note": ["plain"]}
            handle.commit("config", analysis_mode="CONFIG_ONLY")

        assert recorder.query_for() == {"note": ["config"], "analysisMode": ["CONFIG_ONLY"]}

    def test_discard_device_changes_is_its_own_route(self, recorder: Recorder) -> None:
        path = "/api/networks/101/change-sets/CHG-1/devices/edge-1"
        recorder.add("POST", path, json_response(None, status=204))
        with make_client(recorder) as client:
            client.change_sets.handle("CHG-1").discard_device_changes("edge-1")

        assert recorder.query_for() == {"action": ["discardChanges"]}


class TestSnapshotFileExcerpts:
    PATH = "/api/snapshots/s1/files/edge-1,show_run.txt"
    BODY = {
        "lineCount": 120,
        "excerpts": [{"start": 0, "end": 1, "lines": ["hostname edge-1", "!"]}],
        "predictedFileInfo": {"numModifiedLines": 2, "changeSetId": "CHG-1"},
    }

    def test_a_few_ranges_go_in_one_comma_separated_value(self, recorder: Recorder) -> None:
        recorder.add("GET", self.PATH, json_response(self.BODY))
        with make_client(recorder) as client:
            result = client.snapshots.file_excerpts("s1", "edge-1,show_run.txt", [(0, 1), (40, 49)])

        assert recorder.query_for() == {"lines": ["0-1,40-49"]}
        assert result.line_count == 120
        assert result.excerpts is not None
        assert result.excerpts[0].lines == ["hostname edge-1", "!"]
        assert result.predicted_file_info is not None
        assert result.predicted_file_info.num_modified_lines == 2

    def test_many_ranges_move_to_the_body(self, recorder: Recorder) -> None:
        recorder.add("POST", self.PATH, json_response(self.BODY))
        ranges = [(i * 10, i * 10 + 4) for i in range(25)]
        with make_client(recorder) as client:
            client.snapshots.file_excerpts("s1", "edge-1,show_run.txt", ranges)

        assert recorder.body_for()[:2] == [{"start": 0, "end": 4}, {"start": 10, "end": 14}]
        assert len(recorder.body_for()) == 25

    def test_search_returns_line_indexes(self, recorder: Recorder) -> None:
        recorder.add("GET", self.PATH + "/search", json_response([3, 17, 17]))
        with make_client(recorder) as client:
            lines = client.snapshots.search_file("s1", "edge-1,show_run.txt", "ospf")

        assert lines == [3, 17, 17]
        assert recorder.query_for() == {"q": ["ospf"]}


class TestCallAndFromEnv:
    def test_call_runs_an_operation_by_id_with_the_clients_defaults(
        self, recorder: Recorder
    ) -> None:
        recorder.add("GET", "/api/networks/101/snapshots", json_response({"snapshots": []}))
        with make_client(recorder) as client:
            result = client.call("listNetworkSnapshots")

        assert result == {"snapshots": []}

    def test_call_names_the_nearest_operation_when_the_id_is_wrong(
        self, recorder: Recorder
    ) -> None:
        with make_client(recorder) as client:
            with pytest.raises(ForwardConfigurationError, match="listNetworkSnapshots"):
                client.call("listNetworkSnapshot")

    def test_call_refuses_a_streaming_operation(self, recorder: Recorder) -> None:
        with make_client(recorder) as client:
            with pytest.raises(ForwardConfigurationError, match="streams"):
                client.call("getSnapshotCollectionLog", snapshot_id="s1")

    def test_from_env_reads_a_custom_environment(self) -> None:
        client = ForwardClient.from_env(
            {
                "FORWARD_URL": "https://forward.test",
                "FORWARD_USERNAME": "key",
                "FORWARD_PASSWORD": "secret",
                "FORWARD_NETWORK_ID": "55",
            },
            rate_limit_rpm=None,
        )
        assert client.config.network_id == "55"
        client.close()


class TestOsVulnerabilities:
    def test_a_cve_whose_config_dependence_is_unknown_still_parses(
        self, recorder: Recorder
    ) -> None:
        """Forward's description says dependsOnConfig is null when unknown."""
        cve = {
            "id": "CVE-2025-0001",
            "severity": "HIGH",
            "vendor": "ARISTA",
            "os": "EOS",
            "osVersions": ["4.36.0.1F"],
            "detectionMethod": "VERSION",
            "devices": ["r1", "r2"],
        }
        recorder.add(
            "GET",
            "/api/networks/101/vulnerabilities",
            json_response(
                {
                    "vulnerabilities": [
                        {**cve, "dependsOnConfig": None},
                        {**cve, "id": "CVE-2025-0002", "dependsOnConfig": True},
                    ],
                    "offset": 0,
                    "total": 2,
                    "indexCreatedAt": "2026-11-01T00:00:00Z",
                }
            ),
        )
        with make_client(recorder) as client:
            result = client.vulnerability_analysis.get_os_vulnerabilities(snapshot_id="s1")

        assert result.vulnerabilities is not None
        assert [v.depends_on_config for v in result.vulnerabilities] == [None, True]


class TestSaseAndOverrides:
    def test_a_setup_test_is_dispatched_on_its_action(self, recorder: Recorder) -> None:
        recorder.add(
            "POST",
            "/api/networks/101/sase-setups/zs-main/connectors",
            json_response(None, status=204),
        )
        with make_client(recorder) as client:
            client.sase.test_sase_setup_connectors(name="zs-main")

        assert recorder.query_for() == {"action": ["test"]}

    def test_overrides_patch_sends_null_to_clear(self, recorder: Recorder) -> None:
        recorder.add(
            "PATCH", "/api/config/DISABLE_FLOW_COMPUTATION/overrides", json_response(None, 204)
        )
        with make_client(recorder) as client:
            client.configuration.patch_network_config_property_overrides(
                property="DISABLE_FLOW_COMPUTATION", body={"101": True, "102": None}
            )

        assert recorder.body_for() == {"101": True, "102": None}


class TestSnapshotFileMetadata:
    def test_file_info_asks_for_metadata_only(self, recorder: Recorder) -> None:
        path = "/api/snapshots/s1/files/edge-1,show_run.txt"
        recorder.add(
            "GET",
            path,
            json_response(
                {"id": "7", "fileName": "edge-1,show_run.txt", "creationDateMillis": 1, "size": 9}
            ),
        )
        with make_client(recorder) as client:
            info = client.snapshots.file_info("s1", "edge-1,show_run.txt")

        assert "info" in recorder.query_for()
        assert (info.file_name, info.size) == ("edge-1,show_run.txt", 9)

    def test_file_access_reads_the_policy(self, recorder: Recorder) -> None:
        recorder.add(
            "GET",
            "/api/networks/101/file-access",
            json_response({"action": "ALLOW", "devices": ["edge-1"]}),
        )
        with make_client(recorder) as client:
            access = client.snapshots.file_access(snapshot_id="s1")

        assert recorder.query_for() == {"snapshotId": ["s1"]}
        assert (str(access.action), access.devices) == ("ALLOW", ["edge-1"])
