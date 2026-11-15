"""Routes brought over from the Go SDK: the ones whose wire shape is a decision.

Most of the new surface is derived from the spec and covered by the generated
conformance and smoke tests. These are the ones with a trap: bytes that must not
become JSON, a path segment with a slash in it, and a query key that selects the
operation.
"""

from __future__ import annotations

import httpx
import pytest
from pydantic import ValidationError

from forward_sdk._async.client import AsyncForwardClient
from forward_sdk.errors import ForwardConfigurationError, ForwardServerError, ForwardTransportError
from forward_sdk.models import (
    AutoCreateImpact,
    IntervalSchedule,
    NewTenableVmScanner,
    SrcUpdateServiceNowSettings,
    TenableVmScannerPatch,
    TenableVmScannerTestConfig,
)
from forward_sdk.testing import snapshot_payload
from forward_sdk.trace import Endpoint, PathQuery, device, interface
from tests.conftest import Recorder, json_response

pytestmark = pytest.mark.anyio


def make_client(recorder: Recorder) -> AsyncForwardClient:
    return AsyncForwardClient(
        "https://forward.test",
        username="key",
        password="secret",
        rate_limit_rpm=None,
        network_id="101",
        transport=recorder.transport,
    )


class TestPerformanceUpload:
    async def test_the_payload_goes_out_as_raw_bytes(self, recorder: Recorder) -> None:
        recorder.add("POST", "/api/networks/101/performance", json_response(None, status=204))
        payload = b"\x08\x01\xff\x00protobuf"
        async with make_client(recorder) as client:
            await client.performance_collection.upload_performance_data(body=payload)

        request = recorder.requests[-1]
        assert request.content == payload
        assert request.headers["content-type"] == "application/octet-stream"

    async def test_a_non_bytes_body_is_refused_before_sending(self, recorder: Recorder) -> None:
        async with make_client(recorder) as client:
            with pytest.raises(TypeError, match="bytes"):
                await client.performance_collection.upload_performance_data(body={"a": 1})

        assert recorder.requests == []


class TestInterfaces:
    async def test_a_slash_in_the_interface_name_is_encoded(self, recorder: Recorder) -> None:
        recorder.add(
            "GET",
            "/api/networks/101/devices/sw1/interfaces/Ethernet1%2F1",
            json_response({"name": "Ethernet1/1", "type": "L3"}),
        )
        async with make_client(recorder) as client:
            interface = await client.device_interfaces.get_device_interface(
                device_name="sw1", interface_name="Ethernet1/1", snapshot_id="900"
            )

        assert interface.name == "Ethernet1/1"
        assert recorder.query_for() == {"snapshotId": ["900"]}


class TestChangeSetCheckPatch:
    async def test_patch_sends_the_body_to_the_check(self, recorder: Recorder) -> None:
        recorder.add(
            "PATCH",
            "/api/networks/101/change-sets/CHG-1/checks/CSC-7",
            json_response(None, status=204),
        )
        async with make_client(recorder) as client:
            await client.change_set_checks.update_change_set_check(
                change_set_id="CHG-1", check_id="CSC-7", body={"name": "renamed"}
            )

        assert recorder.body_for() == {"name": "renamed"}


class TestProtocolModeling:
    async def test_the_view_key_selects_affected_devices(self, recorder: Recorder) -> None:
        recorder.add(
            "GET",
            "/api/internal/snapshots/900/protocol-modeling",
            json_response({"error": "No affected devices since this is a base snapshot"}),
        )
        async with make_client(recorder) as client:
            await client.protocol_modeling.list_predict_affected_devices(snapshot_id="900")

        assert recorder.query_for() == {"view": ["affected-devices"]}


class TestChangeSetHandle:
    async def test_commit_sends_the_analysis_mode_only_when_given(self, recorder: Recorder) -> None:
        path = "/api/networks/101/change-sets/CHG-1/commits"
        recorder.add("POST", path, json_response({"commitId": "c1"}))
        async with make_client(recorder) as client:
            handle = client.change_sets.handle("CHG-1")
            await handle.commit("plain")
            assert recorder.query_for() == {"note": ["plain"]}
            await handle.commit("config", analysis_mode="CONFIG_ONLY")

        assert recorder.query_for() == {"note": ["config"], "analysisMode": ["CONFIG_ONLY"]}

    async def test_discard_device_changes_is_its_own_route(self, recorder: Recorder) -> None:
        path = "/api/networks/101/change-sets/CHG-1/devices/edge-1"
        recorder.add("POST", path, json_response(None, status=204))
        async with make_client(recorder) as client:
            await client.change_sets.handle("CHG-1").discard_device_changes("edge-1")

        assert recorder.query_for() == {"action": ["discardChanges"]}


class TestSnapshotFileExcerpts:
    PATH = "/api/snapshots/s1/files/edge-1,show_run.txt"
    BODY = {
        "lineCount": 120,
        "excerpts": [{"start": 0, "end": 1, "lines": ["hostname edge-1", "!"]}],
        "predictedFileInfo": {"numModifiedLines": 2, "changeSetId": "CHG-1"},
    }

    async def test_a_few_ranges_go_in_one_comma_separated_value(self, recorder: Recorder) -> None:
        recorder.add("GET", self.PATH, json_response(self.BODY))
        async with make_client(recorder) as client:
            result = await client.snapshots.file_excerpts(
                "s1", "edge-1,show_run.txt", [(0, 1), (40, 49)]
            )

        assert recorder.query_for() == {"lines": ["0-1,40-49"]}
        assert result.line_count == 120
        assert result.excerpts is not None
        assert result.excerpts[0].lines == ["hostname edge-1", "!"]
        assert result.predicted_file_info is not None
        assert result.predicted_file_info.num_modified_lines == 2

    async def test_many_ranges_move_to_the_body(self, recorder: Recorder) -> None:
        recorder.add("POST", self.PATH, json_response(self.BODY))
        ranges = [(i * 10, i * 10 + 4) for i in range(25)]
        async with make_client(recorder) as client:
            await client.snapshots.file_excerpts("s1", "edge-1,show_run.txt", ranges)

        assert recorder.body_for()[:2] == [{"start": 0, "end": 4}, {"start": 10, "end": 14}]
        assert len(recorder.body_for()) == 25

    async def test_search_returns_line_indexes(self, recorder: Recorder) -> None:
        recorder.add("GET", self.PATH + "/search", json_response([3, 17, 17]))
        async with make_client(recorder) as client:
            lines = await client.snapshots.search_file("s1", "edge-1,show_run.txt", "ospf")

        assert lines == [3, 17, 17]
        assert recorder.query_for() == {"q": ["ospf"]}


class TestCallAndFromEnv:
    async def test_call_runs_an_operation_by_id_with_the_clients_defaults(
        self, recorder: Recorder
    ) -> None:
        recorder.add("GET", "/api/networks/101/snapshots", json_response({"snapshots": []}))
        async with make_client(recorder) as client:
            result = await client.call("listNetworkSnapshots")

        assert result == {"snapshots": []}

    async def test_call_names_the_nearest_operation_when_the_id_is_wrong(
        self, recorder: Recorder
    ) -> None:
        async with make_client(recorder) as client:
            with pytest.raises(ForwardConfigurationError, match="listNetworkSnapshots"):
                await client.call("listNetworkSnapshot")

    async def test_call_refuses_a_streaming_operation(self, recorder: Recorder) -> None:
        async with make_client(recorder) as client:
            with pytest.raises(ForwardConfigurationError, match="streams"):
                await client.call("getSnapshotCollectionLog", snapshot_id="s1")

    async def test_from_env_reads_a_custom_environment(self) -> None:
        client = AsyncForwardClient.from_env(
            {
                "FORWARD_URL": "https://forward.test",
                "FORWARD_USERNAME": "key",
                "FORWARD_PASSWORD": "secret",
                "FORWARD_NETWORK_ID": "55",
            },
            rate_limit_rpm=None,
        )
        assert client.config.network_id == "55"
        await client.aclose()


class TestOsVulnerabilities:
    async def test_a_cve_whose_config_dependence_is_unknown_still_parses(
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
        async with make_client(recorder) as client:
            result = await client.vulnerability_analysis.get_os_vulnerabilities(snapshot_id="s1")

        assert result.vulnerabilities is not None
        assert [v.depends_on_config for v in result.vulnerabilities] == [None, True]


class TestSaseAndOverrides:
    async def test_a_setup_test_is_dispatched_on_its_action(self, recorder: Recorder) -> None:
        recorder.add(
            "POST",
            "/api/networks/101/sase-setups/zs-main/connectors",
            json_response(None, status=204),
        )
        async with make_client(recorder) as client:
            await client.sase.test_sase_setup_connectors(name="zs-main")

        assert recorder.query_for() == {"action": ["test"]}

    async def test_overrides_patch_sends_null_to_clear(self, recorder: Recorder) -> None:
        recorder.add(
            "PATCH", "/api/config/DISABLE_FLOW_COMPUTATION/overrides", json_response(None, 204)
        )
        async with make_client(recorder) as client:
            await client.configuration.patch_network_config_property_overrides(
                property="DISABLE_FLOW_COMPUTATION", body={"101": True, "102": None}
            )

        assert recorder.body_for() == {"101": True, "102": None}


class TestSnapshotFileMetadata:
    async def test_file_info_asks_for_metadata_only(self, recorder: Recorder) -> None:
        path = "/api/snapshots/s1/files/edge-1,show_run.txt"
        recorder.add(
            "GET",
            path,
            json_response(
                {"id": "7", "fileName": "edge-1,show_run.txt", "creationDateMillis": 1, "size": 9}
            ),
        )
        async with make_client(recorder) as client:
            info = await client.snapshots.file_info("s1", "edge-1,show_run.txt")

        assert "info" in recorder.query_for()
        assert (info.file_name, info.size) == ("edge-1,show_run.txt", 9)

    async def test_file_access_reads_the_policy(self, recorder: Recorder) -> None:
        recorder.add(
            "GET",
            "/api/networks/101/file-access",
            json_response({"action": "ALLOW", "devices": ["edge-1"]}),
        )
        async with make_client(recorder) as client:
            access = await client.snapshots.file_access(snapshot_id="s1")

        assert recorder.query_for() == {"snapshotId": ["s1"]}
        assert (str(access.action), access.devices) == ("ALLOW", ["edge-1"])


class TestServiceNowIncidents:
    async def test_check_incidents_repeat_the_type_and_dispatch_on_the_view(
        self, recorder: Recorder
    ) -> None:
        recorder.add(
            "GET",
            "/api/snapshots/s1/checks",
            json_response(
                [{"checkId": "7", "number": "INC1", "state": "In Progress", "stateLastUpdated": 5}]
            ),
        )
        async with make_client(recorder) as client:
            links = await client.checks.list_check_service_now_incidents(
                snapshot_id="s1", type=["Isolation", "NQE"]
            )

        assert recorder.query_for() == {
            "view": ["serviceNowIncidents"],
            "type": ["Isolation", "NQE"],
        }
        assert [(link.check_id, link.number, str(link.state)) for link in links] == [
            ("7", "INC1", "In Progress")
        ]

    async def test_bulk_add_sends_check_id_to_incident(self, recorder: Recorder) -> None:
        recorder.add("POST", "/api/snapshots/s1/checks", json_response([]))
        body = {"7": {"summary": "Isolation failed", "impact": "2", "urgency": "3"}}
        async with make_client(recorder) as client:
            await client.checks.add_check_service_now_incidents(snapshot_id="s1", body=body)

        assert recorder.query_for() == {"action": ["addServiceNowIncidents"]}
        assert recorder.body_for() == body

    async def test_link_names_the_incident_in_the_query(self, recorder: Recorder) -> None:
        recorder.add(
            "POST",
            "/api/snapshots/s1/checks/7",
            json_response(
                {"checkId": "7", "number": "INC1", "state": "New", "stateLastUpdated": 5}
            ),
        )
        async with make_client(recorder) as client:
            await client.checks.link_check_service_now_incident(
                snapshot_id="s1", check_id="7", incident="INC1"
            )

        assert recorder.query_for() == {"action": ["linkServiceNowIncident"], "incident": ["INC1"]}

    async def test_closing_several_sends_a_bare_array(self, recorder: Recorder) -> None:
        recorder.add("POST", "/api/integrations/servicenow/incidents", json_response(None, 204))
        async with make_client(recorder) as client:
            await client.integrations.close_service_now_incidents(body=["INC1", "INC2"])

        assert recorder.query_for() == {"action": ["close"]}
        assert recorder.body_for() == ["INC1", "INC2"]

    async def test_levels_are_servicenow_numbers(self, recorder: Recorder) -> None:
        recorder.add(
            "GET",
            "/api/integrations/servicenow/incidents/INC1",
            json_response(
                {
                    "number": "INC1",
                    "sysId": "abc",
                    "impact": "1",
                    "urgency": "3",
                    "state": "New",
                    "url": "https://example.service-now.invalid/nav_to.do",
                }
            ),
        )
        recorder.add(
            "GET",
            "/api/integrations/servicenow",
            json_response(
                {
                    "instanceUrl": "https://example.service-now.invalid",
                    "username": "api-user",
                    "password": "",
                    "enabled": True,
                    "autoCreate": False,
                    "autoUpdate": True,
                    "autoCreateImpact": "3",
                    "autoCreateUrgency": "2",
                }
            ),
        )
        async with make_client(recorder) as client:
            incident = await client.integrations.get_service_now_incident(incident_number="INC1")
            settings = await client.integrations.get_service_now_settings()

        assert (incident.impact, incident.urgency) == ("1", "3")
        assert incident.impact is not None and incident.impact.name == "high"
        assert (settings.auto_create_impact, settings.auto_create_urgency) == ("3", "2")
        assert settings.auto_create_impact is not None
        assert settings.auto_create_impact.name == "low"

    async def test_settings_levels_go_out_as_numbers(self, recorder: Recorder) -> None:
        recorder.add(
            "PATCH",
            "/api/integrations/servicenow",
            json_response(
                {
                    "instanceUrl": "https://example.service-now.invalid",
                    "token": "",
                    "enabled": True,
                    "autoCreate": True,
                    "autoUpdate": True,
                    "autoCreateImpact": "1",
                    "autoCreateUrgency": "3",
                }
            ),
        )
        update = SrcUpdateServiceNowSettings(
            autoCreateImpact=AutoCreateImpact.high, autoCreateUrgency=AutoCreateImpact.low
        )
        async with make_client(recorder) as client:
            await client.integrations.patch_service_now_settings(body=update.to_api())

        assert recorder.body_for() == {"autoCreateImpact": "1", "autoCreateUrgency": "3"}

    async def test_single_close_unlink_and_refresh_dispatch_correctly(
        self, recorder: Recorder
    ) -> None:
        recorder.add("POST", "/api/integrations/servicenow/incidents/INC1", json_response(None))
        recorder.add("POST", "/api/snapshots/s1/checks/7", json_response(None))
        recorder.add("POST", "/api/snapshots/s1/checks", json_response(None, 204))
        recorder.add(
            "GET",
            "/api/snapshots/s1/checks/7",
            json_response([{"snapshotCreationTime": 1, "totalFailures": 4}]),
        )
        async with make_client(recorder) as client:
            await client.integrations.close_service_now_incident(incident_number="INC1")
            await client.checks.unlink_check_service_now_incident(
                snapshot_id="s1", check_id="7", incident="INC1"
            )
            await client.checks.refresh_check_service_now_incidents(snapshot_id="s1")
            history = await client.checks.get_check_service_now_incident_update_history(
                snapshot_id="s1", check_id="7", incident="INC1"
            )

        assert [recorder.query_for(i) for i in range(4)] == [
            {"action": ["close"]},
            {"action": ["unlinkServiceNowIncident"], "incident": ["INC1"]},
            {"action": ["refreshServiceNowIncidents"]},
            {"view": ["serviceNowIncidentUpdateHistory"], "incident": ["INC1"]},
        ]
        assert [(h.snapshot_creation_time, h.total_failures) for h in history] == [(1, 4)]


class TestServiceNowCmdb:
    async def test_the_two_payload_reads_are_told_apart_by_query(self, recorder: Recorder) -> None:
        recorder.add(
            "GET",
            "/api/integrations/servicenow-cmdb",
            json_response(
                {"tables": [{"tableName": "cmdb_ci_ip_router", "rows": [{"name": "r1"}]}]}
            ),
            httpx.Response(200, content=b"PK\x03\x04zip"),
        )
        async with make_client(recorder) as client:
            preview = await client.integrations.get_service_now_cmdb_payload(network_id="101")
            chunks = [c async for c in client.integrations.download_service_now_cmdb_payload()]

        assert recorder.query_for(0) == {"view": ["payload"], "networkId": ["101"]}
        assert recorder.query_for(1) == {"view": ["payload"], "disposition": ["attachment"]}
        assert preview.tables is not None and preview.tables[0].rows == [{"name": "r1"}]
        assert b"".join(chunks) == b"PK\x03\x04zip"

    async def test_history_unwraps_push_status_and_errors(self, recorder: Recorder) -> None:
        recorder.add(
            "GET",
            "/api/integrations/servicenow-cmdb/synchronization-history",
            json_response(
                {
                    "infos": [
                        {
                            "syncInstant": 1,
                            "networkName": "n",
                            "entitiesInserted": 2,
                            "entitiesUpdated": 3,
                        },
                        {
                            "syncInstant": 2,
                            "networkName": "n",
                            "error": "boom",
                            "errors": [
                                {
                                    "errorType": "AUTH_INVALID_CREDENTIALS",
                                    "userMessage": "bad login",
                                    "fieldContext": {"ciTableName": "cmdb_ci_ip_router"},
                                    "count": 2,
                                }
                            ],
                        },
                    ]
                }
            ),
        )
        async with make_client(recorder) as client:
            history = await client.integrations.get_service_now_cmdb_synchronization_history()

        ok, failed = history.infos
        assert (ok.entities_inserted, ok.entities_updated, ok.error) == (2, 3, None)
        assert failed.error == "boom" and failed.errors is not None
        assert str(failed.errors[0].error_type) == "AUTH_INVALID_CREDENTIALS"
        assert failed.errors[0].field_context is not None
        assert failed.errors[0].field_context.ci_table_name == "cmdb_ci_ip_router"

    async def test_a_read_configuration_with_blank_secrets_validates(
        self, recorder: Recorder
    ) -> None:
        conf = {
            "settings": {
                "connectionName": "prod",
                "connectionDetails": {
                    "instanceUrl": "https://example.service-now.invalid",
                    "username": "api-user",
                    "password": "",
                    "clientId": "cid",
                    "clientSecret": "",
                    "proxyConfig": {
                        "host": "http://proxy.invalid",
                        "port": 3128,
                        "username": "p",
                        "password": "",
                    },
                },
                "sourceName": "ForwardNetworks",
            },
            "mappings": [
                {
                    "name": "cmdb_ci_ip_router",
                    "queryId": "Q_abc",
                    "fieldRelations": [
                        {
                            "cmdbField": {
                                "label": "Name",
                                "element": "name",
                                "dataType": "string",
                                "mandatory": True,
                                "maxLength": 255,
                            },
                            "queryColumnName": "name",
                        }
                    ],
                }
            ],
            "syncConfig": {"networkIds": ["101"]},
            "enabled": True,
        }
        recorder.add("GET", "/api/integrations/servicenow-cmdb/configuration", json_response(conf))
        async with make_client(recorder) as client:
            got = await client.integrations.get_service_now_cmdb_configuration()

        assert got.settings is not None
        assert got.settings.connection_details.password == ""
        assert got.settings.connection_details.proxy_config is not None
        assert got.settings.connection_details.proxy_config.port == 3128
        assert got.mappings[0].query_id == "Q_abc"

    async def test_a_read_configuration_saves_back_unchanged(self, recorder: Recorder) -> None:
        # Forward reuses a stored secret only while the instance URL compares
        # equal, so the URL must go back exactly as it came, without a "/".
        conf = {
            "settings": {
                "connectionName": "prod",
                "connectionDetails": {
                    "instanceUrl": "https://example.service-now.invalid",
                    "username": "api-user",
                    "password": "",
                    "proxyConfig": {"host": "http://proxy.invalid", "port": 3128},
                },
                "sourceName": "ForwardNetworks",
            },
            "mappings": [
                {
                    "name": "cmdb_ci_ip_router",
                    "queryId": "Q_abc",
                    "fieldRelations": [
                        {
                            "cmdbField": {
                                "label": "Name",
                                "element": "name",
                                "dataType": "string",
                                "mandatory": True,
                                "maxLength": 255,
                            }
                        }
                    ],
                }
            ],
            "syncConfig": {"networkIds": ["101"]},
            "enabled": False,
        }
        recorder.add("GET", "/api/integrations/servicenow-cmdb/configuration", json_response(conf))
        recorder.add("POST", "/api/integrations/servicenow-cmdb/configuration", json_response(None))
        async with make_client(recorder) as client:
            got = await client.integrations.get_service_now_cmdb_configuration()
            await client.integrations.save_service_now_cmdb_configuration(body=got.to_api())

        assert recorder.body_for() == conf

    async def test_disable_and_push_dispatch_on_their_action(self, recorder: Recorder) -> None:
        recorder.add(
            "POST",
            "/api/integrations/servicenow-cmdb",
            json_response(None, 204),
            json_response(None, 204),
        )
        async with make_client(recorder) as client:
            await client.integrations.disable_service_now_cmdb()
            await client.integrations.push_service_now_cmdb()

        assert [recorder.query_for(i) for i in (0, 1)] == [
            {"action": ["disable"]},
            {"action": ["push"]},
        ]


class TestForward2611Snapshots:
    """Forward 26.11 leaves predicted snapshots out of the listing unless asked
    (FWD-61712), and waiting on a Predict run finds its snapshot there."""

    async def test_waiting_on_a_snapshot_asks_for_predicted_ones(self, recorder: Recorder) -> None:
        recorder.add(
            "GET",
            "/api/networks/101/snapshots",
            json_response({"snapshots": [snapshot_payload("p1", processingTrigger="PREDICT")]}),
        )
        async with make_client(recorder) as client:
            snapshot = await client.snapshots.wait_until_processed("p1")

        assert snapshot.id == "p1"
        assert recorder.query_for()["includePredicted"] == ["true"]

    async def test_a_plain_listing_says_what_it_wants(self, recorder: Recorder) -> None:
        recorder.add("GET", "/api/networks/101/snapshots", json_response({"snapshots": []}))
        async with make_client(recorder) as client:
            await client.snapshots.list()
            await client.snapshots.list(include_predicted=True)

        assert recorder.query_for(0)["includePredicted"] == ["false"]
        assert recorder.query_for(1)["includePredicted"] == ["true"]

    async def test_latest_processed_asks_only_when_predictions_are_wanted(
        self, recorder: Recorder
    ) -> None:
        recorder.add("GET", "/api/networks/101/snapshots", json_response({"snapshots": []}))
        async with make_client(recorder) as client:
            await client.snapshots.latest_processed()
            await client.snapshots.latest_processed(include_predicted=True)

        assert [recorder.query_for(i)["includePredicted"] for i in (0, 1)] == [["false"], ["true"]]


class TestSlowPredictReads:
    async def test_a_slow_read_waits_longer_than_the_client_default(
        self, recorder: Recorder
    ) -> None:
        recorder.add(
            "POST",
            "/api/internal/snapshots/s1/verify-predict-modeling",
            json_response({"diffs": {}, "score": {"precision": 1, "recall": 1, "f1": 1}}),
        )
        async with make_client(recorder) as client:
            await client.predict_verification.verify_predict_modeling_base(snapshot_id="s1")

        assert recorder.requests[-1].extensions["timeout"]["read"] == 1800.0

    async def test_a_longer_client_timeout_is_kept(self, recorder: Recorder) -> None:
        recorder.add(
            "GET",
            "/api/internal/snapshots/s1/protocol-modeling",
            json_response({"error": "Partitioning was not used"}),
        )
        async with AsyncForwardClient(
            "https://forward.test",
            username="key",
            password="secret",
            rate_limit_rpm=None,
            network_id="101",
            timeout=7200.0,
            transport=recorder.transport,
        ) as client:
            await client.protocol_modeling.get_protocol_modeling_partitions(snapshot_id="s1")

        assert recorder.query_for() == {"view": ["partitions"]}
        assert recorder.requests[-1].extensions["timeout"]["read"] == 7200.0

    async def test_an_ordinary_read_keeps_the_client_default(self, recorder: Recorder) -> None:
        recorder.add("GET", "/api/internal/snapshots/s1/bgp-nodes", json_response({"17": "edge-1"}))
        async with make_client(recorder) as client:
            devices = await client.protocol_modeling.list_bgp_node_devices(snapshot_id="s1")

        assert devices == {"17": "edge-1"}
        assert "devices" in recorder.query_for()
        assert recorder.requests[-1].extensions["timeout"]["read"] == 300.0

    async def test_the_blob_download_streams_with_the_long_wait(self, recorder: Recorder) -> None:
        recorder.add(
            "GET",
            "/api/internal/snapshots/s1/protocol-modeling",
            httpx.Response(200, content=b"PK\x03\x04blob"),
        )
        async with make_client(recorder) as client:
            chunks = [
                c
                async for c in client.protocol_modeling.download_protocol_modeling(snapshot_id="s1")
            ]

        request = recorder.requests[-1]
        assert b"".join(chunks) == b"PK\x03\x04blob"
        # Forward picks the blob handler by Accept; a JSON Accept has no handler.
        assert request.headers["accept"] == "application/zip"
        assert recorder.query_for() == {}
        assert request.extensions["timeout"]["read"] >= 1200.0

    async def test_no_read_limit_stays_no_limit(self, recorder: Recorder) -> None:
        recorder.add(
            "POST",
            "/api/internal/snapshots/s1/verify-predict-modeling",
            json_response({"diffs": {}}),
        )
        async with AsyncForwardClient(
            "https://forward.test",
            username="key",
            password="secret",
            rate_limit_rpm=None,
            network_id="101",
            timeout=httpx.Timeout(10.0, read=None),
            transport=recorder.transport,
        ) as client:
            await client.predict_verification.verify_predict_modeling_base(snapshot_id="s1")

        assert recorder.requests[-1].extensions["timeout"]["read"] is None

    async def test_a_slow_read_is_not_repeated_after_a_read_timeout(
        self, recorder: Recorder, no_sleep: list[float]
    ) -> None:
        recorder.add(
            "GET",
            "/api/internal/snapshots/s1/protocol-modeling",
            httpx.ReadTimeout("slow"),
            json_response({}),
        )
        async with make_client(recorder) as client:
            with pytest.raises(ForwardTransportError):
                await client.protocol_modeling.get_protocol_modeling_partitions(snapshot_id="s1")

        assert len(recorder.requests) == 1

    async def test_a_slow_read_is_not_repeated_after_a_server_error(
        self, recorder: Recorder, no_sleep: list[float]
    ) -> None:
        recorder.add(
            "GET",
            "/api/internal/snapshots/s1/protocol-modeling",
            json_response({"message": "boom"}, 503),
            httpx.Response(200, content=b"PK"),
        )
        async with make_client(recorder) as client:
            with pytest.raises(ForwardServerError):
                async for _ in client.protocol_modeling.download_protocol_modeling(
                    snapshot_id="s1"
                ):
                    pass

        assert len(recorder.requests) == 1

    async def test_a_slow_read_is_retried_when_it_never_reached_forward(
        self, recorder: Recorder, no_sleep: list[float]
    ) -> None:
        recorder.add(
            "GET",
            "/api/internal/snapshots/s1/protocol-modeling",
            httpx.ConnectError("refused"),
            json_response({"message": "busy"}, 429),
            json_response({}),
        )
        async with make_client(recorder) as client:
            await client.protocol_modeling.get_protocol_modeling_partitions(snapshot_id="s1")

        assert len(recorder.requests) == 3

    async def test_a_slow_read_is_retried_after_waiting_for_a_pooled_connection(
        self, recorder: Recorder, no_sleep: list[float]
    ) -> None:
        recorder.add(
            "GET",
            "/api/internal/snapshots/s1/protocol-modeling",
            httpx.PoolTimeout("no free connection"),
            json_response({}),
        )
        async with make_client(recorder) as client:
            await client.protocol_modeling.get_protocol_modeling_partitions(snapshot_id="s1")

        assert len(recorder.requests) == 2


class TestRoutingNodeReads:
    async def test_a_neighbor_that_is_not_an_ip_is_refused_before_sending(
        self, recorder: Recorder
    ) -> None:
        async with make_client(recorder) as client:
            with pytest.raises(ValueError, match="IP address"):
                await client.protocol_modeling.get_bgp_node_rules(
                    snapshot_id="s1",
                    device_name="edge-1",
                    vrf_name="default",
                    neighbor_addr="peer-a",
                )

        assert recorder.requests == []

    async def test_an_ipv6_neighbor_is_sent(self, recorder: Recorder) -> None:
        recorder.add(
            "GET",
            "/api/internal/snapshots/s1/bgp-rules",
            json_response(
                {"deviceId": 17, "vrfId": 0, "deviceName": "edge-1", "nbrConfigRules": []}
            ),
        )
        async with make_client(recorder) as client:
            rules = await client.protocol_modeling.get_bgp_node_rules(
                snapshot_id="s1",
                device_name="edge-1",
                vrf_name="default",
                neighbor_addr="2001:db8::1",
            )

        assert recorder.query_for()["neighborAddr"] == ["2001:db8::1"]
        assert rules.device_name == "edge-1"

    async def test_the_subset_repeats_each_changed_device(self, recorder: Recorder) -> None:
        recorder.add(
            "GET",
            "/api/internal/snapshots/s1/rp-nodes",
            json_response({"names": ["core-1", "edge-1"]}),
        )
        async with make_client(recorder) as client:
            subset = await client.protocol_modeling.get_change_set_subset(
                snapshot_id="s1", changed_devices=["edge-1", "edge-2"], threshold=50
            )

        assert recorder.query_for() == {
            "view": ["changeSetSubset"],
            "changedDevices": ["edge-1", "edge-2"],
            "threshold": ["50"],
        }
        assert subset.names == ["core-1", "edge-1"]

    async def test_a_vrf_without_bgp_reads_as_none(self, recorder: Recorder) -> None:
        recorder.add(
            "GET",
            "/api/internal/snapshots/s1/devices/edge-1/vrfs/blue/bgp-node",
            json_response({"bgpNode": None}),
        )
        async with make_client(recorder) as client:
            node = await client.protocol_modeling.get_bgp_node(
                snapshot_id="s1", device_name="edge-1", vrf_name="blue"
            )

        assert node.bgp_node is None


class TestNetworkProperty:
    async def test_set_sends_the_value_as_a_query_parameter(self, recorder: Recorder) -> None:
        recorder.add(
            "PUT",
            "/api/networks/101/config/DISABLE_FLOW_COMPUTATION",
            json_response({"disable_flow_computation": True}),
        )
        async with make_client(recorder) as client:
            await client.configuration.set_network_config_property(
                network_id="101", property="DISABLE_FLOW_COMPUTATION", value="true"
            )

        assert recorder.query_for() == {"value": ["true"]}
        assert recorder.body_for() is None


SCANNER = {
    "sourceId": "00000000-0000-4000-8000-000000000001",
    "type": "TENABLE_VM",
    "name": "vm-1",
    "credentialId": "H-2",
    "govCloud": False,
    "collectionEnabled": True,
    "collectorId": "C1",
    "schedule": {"intervalDays": 1, "startAt": "02:00", "timeZone": "UTC"},
    "allowedForNetworkIds": ["101"],
    "createdById": "11",
    "createdBy": "me@example.com",
}


class TestTenableVmScanners:
    async def test_create_sends_only_what_was_set(self, recorder: Recorder) -> None:
        recorder.add("POST", "/api/vulnerability-scanners", json_response(SCANNER))
        body = NewTenableVmScanner(
            name="vm-1",
            credential_id="H-2",
            collector_id="C1",
            schedule=IntervalSchedule(interval_days=1),
            allowed_for_all_networks=True,
        )
        async with make_client(recorder) as client:
            stored = await client.integrations.create_vulnerability_scanner(body=body.to_api())

        sent = recorder.body_for()
        assert sent == {
            "name": "vm-1",
            "credentialId": "H-2",
            "collectorId": "C1",
            "schedule": {"intervalDays": 1},
            "allowedForAllNetworks": True,
        }
        assert stored.name == "vm-1"
        assert stored.created_by == "me@example.com"
        assert stored.schedule is not None and stored.schedule.start_at == "02:00"

    async def test_a_name_forward_would_refuse_is_caught_by_the_model(self) -> None:
        with pytest.raises(ValidationError):
            NewTenableVmScanner(
                name="vm 1",
                credential_id="H-2",
                collector_id="C1",
                schedule=IntervalSchedule(interval_days=1),
            )
        with pytest.raises(ValidationError):
            IntervalSchedule(interval_days=0)

    async def test_a_patch_never_sends_null(self, recorder: Recorder) -> None:
        recorder.add("PATCH", "/api/vulnerability-scanners/vm-1", json_response(SCANNER))
        async with make_client(recorder) as client:
            await client.integrations.update_vulnerability_scanner(
                name="vm-1", body=TenableVmScannerPatch(collection_enabled=False).to_api()
            )

        assert recorder.body_for() == {"collectionEnabled": False}

    async def test_collect_is_the_post_with_its_action(self, recorder: Recorder) -> None:
        recorder.add("POST", "/api/vulnerability-scanners/vm-1", httpx.Response(200))
        async with make_client(recorder) as client:
            await client.integrations.collect_vulnerability_scanner(name="vm-1")

        assert recorder.query_for() == {"action": ["collect"]}
        assert recorder.body_for() is None

    async def test_the_list_carries_collection_status(self, recorder: Recorder) -> None:
        row = {**SCANNER, "lastAttempted": "2026-01-03T02:00:00Z", "collectionError": "NONE"}
        recorder.add("GET", "/api/vulnerability-scanners", json_response([row]))
        async with make_client(recorder) as client:
            (info,) = await client.integrations.list_vulnerability_scanners()

        assert info.collection_error == "NONE"
        assert info.last_retrieved is None
        assert info.allowed_for_network_ids == ["101"]

    async def test_the_connection_test_names_its_type_and_waits(self, recorder: Recorder) -> None:
        recorder.add("POST", "/api/vulnerability-scanner-tests", json_response({}))
        body = TenableVmScannerTestConfig(name="vm-1", credential_id="H-2", collector_id="C1")
        async with make_client(recorder) as client:
            result = await client.integrations.test_vulnerability_scanner(body=body.to_api())

        assert recorder.body_for() == {
            "type": "TENABLE_VM",
            "name": "vm-1",
            "credentialId": "H-2",
            "collectorId": "C1",
        }
        assert recorder.requests[-1].extensions["timeout"]["read"] >= 90.0
        assert result.error is None

    async def test_a_network_lists_every_scanner_kind(self, recorder: Recorder) -> None:
        rows = [
            {"type": "TENABLE_VM", "name": "vm-1", "include": True, "credentialId": "H-2"},
            {
                "type": "RAPID7",
                "name": "r7",
                "include": False,
                "baseUrl": "https://r7.example.invalid",
                "reportNames": ["weekly"],
            },
        ]
        recorder.add("GET", "/api/networks/101/vulnerability-scanners", json_response(rows))
        async with make_client(recorder) as client:
            scanners = await client.integrations.list_network_vulnerability_scanners()

        assert [(s.type, s.include) for s in scanners] == [("TENABLE_VM", True), ("RAPID7", False)]
        assert scanners[1].report_names == ["weekly"]

    async def test_the_include_switch_is_a_patch(self, recorder: Recorder) -> None:
        recorder.add(
            "PATCH",
            "/api/networks/101/vulnerability-scanners/vm-1",
            json_response(None, status=204),
        )
        async with make_client(recorder) as client:
            await client.integrations.set_network_vulnerability_scanner_include(
                name="vm-1", body={"include": False}
            )

        assert recorder.body_for() == {"include": False}


class TestOrgHttpCredentials:
    async def test_the_org_routes_are_not_the_network_ones(self, recorder: Recorder) -> None:
        stored = {"id": "H-2", "type": "LOGIN", "name": "keys", "username": "a", "password": "S-1"}
        recorder.add("GET", "/api/http-credentials", json_response([stored]))
        recorder.add("POST", "/api/http-credentials", json_response(stored))
        recorder.add("PATCH", "/api/http-credentials/H-2", json_response(stored))
        recorder.add("DELETE", "/api/http-credentials/H-2", json_response(None, status=204))
        async with make_client(recorder) as client:
            (listed,) = await client.credentials.list_org_http_credentials()
            created = await client.credentials.create_org_http_credential(
                body={"type": "LOGIN", "name": "keys", "username": "a", "password": "b"}
            )
            updated = await client.credentials.update_org_http_credential(
                credential_id="H-2", body={"name": "keys"}
            )
            await client.credentials.delete_org_http_credential(credential_id="H-2")

        assert [r.url.path for r in recorder.requests] == [
            "/api/http-credentials",
            "/api/http-credentials",
            "/api/http-credentials/H-2",
            "/api/http-credentials/H-2",
        ]
        assert listed.id == created.id == updated.id == "H-2"


class TestTracePathQuery:
    async def test_the_query_goes_out_as_the_facets_body(self, recorder: Recorder) -> None:
        recorder.add(
            "POST",
            "/api/networks/101/trace-facets",
            json_response(
                {"facets": {"pathGroups": []}, "totalResults": {"value": 0, "type": "EXACT"}}
            ),
        )
        query = PathQuery(
            from_=Endpoint(device("edge-1"), headers=[{"ipv4_dst": ["192.0.2.10"]}]),
            to=Endpoint(interface("core-1", "eth1/1")),
        )
        async with make_client(recorder) as client:
            facets = await client.flow_trace.get_trace_facets(
                body=query.request(max_results=100), snapshot_id="s1"
            )

        assert recorder.body_for() == {
            "filters": {
                "from": {
                    "location": {"type": "DeviceFilter", "values": ["edge-1"]},
                    "headers": [{"type": "PacketFilter", "values": {"ipv4_dst": ["192.0.2.10"]}}],
                },
                "to": {"location": {"type": "InterfaceFilter", "values": ["core-1 eth1/1"]}},
            },
            "maxResults": 100,
        }
        assert recorder.query_for() == {"snapshotId": ["s1"]}
        assert facets.total_results is not None and facets.total_results.value == 0
