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

import forward_sdk._async.services.nqe_repo as nqe_repo_service
from forward_sdk._async.client import AsyncForwardClient
from forward_sdk.errors import (
    ForwardBadRequestError,
    ForwardBgpKeyMatchedNothingError,
    ForwardConfigurationError,
    ForwardNotFoundError,
    ForwardResponseError,
    ForwardServerError,
    ForwardTimeoutError,
    ForwardTransportError,
)
from forward_sdk.models import (
    AutoCreateImpact,
    BgpAdvertisement,
    BgpAdvertisementKey,
    Change,
    CheckUpdate,
    IntervalSchedule,
    LocationBulkPatch,
    NewLocation,
    NewTenableVmScanner,
    SrcUpdateServiceNowSettings,
    TenableVmScannerPatch,
    TenableVmScannerTestConfig,
)
from forward_sdk.testing import snapshot_payload
from forward_sdk.trace import Endpoint, PathQuery, device, interface
from tests.conftest import Recorder, json_response
from tests.unit.test_nqe_export import export, zipped

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
                property="DISABLE_FLOW_COMPUTATION", body={"101": True, "103": None}
            )

        assert recorder.body_for() == {"101": True, "103": None}


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


RECEIVED = {
    "vrf": "default",
    "externalPeer": "198.51.100.2",
    "prefix": "192.0.2.0/24",
    "type": "EBGP",
    "nextHop": "198.51.100.2",
    "origin": "IGP",
    "asPath": [65001],
    "localPref": 100,
    "med": 0,
    "communities": ["65001:100"],
}


class TestReceivedBgpAdvertisements:
    async def test_reads_one_vrf_and_prefix_of_a_device(self, recorder: Recorder) -> None:
        recorder.add(
            "GET",
            "/api/networks/101/devices/edge-1/bgp-advertisements",
            json_response([RECEIVED, {**RECEIVED, "type": "IBGP", "asPath": []}]),
        )
        async with make_client(recorder) as client:
            rows = await client.bgp_advertisements.get_device_bgp_advertisements(
                device_name="edge-1", vrf="default", prefix="192.0.2.0/24", snapshot_id="s9"
            )

        assert recorder.query_for() == {
            "vrf": ["default"],
            "prefix": ["192.0.2.0/24"],
            "snapshotId": ["s9"],
        }
        assert [row.type for row in rows] == ["EBGP", "IBGP"]
        assert rows[0].external_peer == "198.51.100.2" and rows[0].as_path == [65001]

    async def test_padding_is_trimmed_before_sending(self, recorder: Recorder) -> None:
        # Forward compares the VRF exactly, so " default" would match nothing.
        recorder.add(
            "GET", "/api/networks/101/devices/edge-1/bgp-advertisements", json_response([])
        )
        async with make_client(recorder) as client:
            await client.bgp_advertisements.get_device_bgp_advertisements(
                device_name="edge-1", vrf=" default ", prefix="192.0.2.0/24 ", snapshot_id="s9"
            )

        assert recorder.query_for()["vrf"] == ["default"]
        assert recorder.query_for()["prefix"] == ["192.0.2.0/24"]

    async def test_a_received_route_without_next_hop_or_origin_is_read(
        self, recorder: Recorder
    ) -> None:
        # Forward leaves null fields out of rows it builds without its creator checks.
        bare = {
            "vrf": "default",
            "externalPeer": "198.51.100.2",
            "prefix": "192.0.2.0/24",
            "type": "IBGP",
        }
        recorder.add(
            "GET",
            "/api/networks/101/devices/edge-1/bgp-advertisements",
            json_response([RECEIVED, bare]),
        )
        async with make_client(recorder) as client:
            rows = await client.bgp_advertisements.get_device_bgp_advertisements(
                device_name="edge-1", vrf="default", prefix="192.0.2.0/24", snapshot_id="s9"
            )

        assert rows[1].next_hop is None and rows[1].origin is None

    async def test_no_advertisements_is_an_empty_list(self, recorder: Recorder) -> None:
        recorder.add(
            "GET", "/api/networks/101/devices/edge-1/bgp-advertisements", json_response([])
        )
        async with make_client(recorder) as client:
            rows = await client.bgp_advertisements.get_device_bgp_advertisements(
                device_name="edge-1", vrf="default", prefix="192.0.2.0/24", snapshot_id="s9"
            )

        assert rows == []

    @pytest.mark.parametrize(("vrf", "prefix"), [("", "192.0.2.0/24"), ("default", "  ")])
    async def test_a_blank_vrf_or_prefix_is_refused_before_sending(
        self, recorder: Recorder, vrf: str, prefix: str
    ) -> None:
        async with make_client(recorder) as client:
            with pytest.raises(ValueError, match="must not be blank"):
                await client.bgp_advertisements.get_device_bgp_advertisements(
                    device_name="edge-1", vrf=vrf, prefix=prefix, snapshot_id="s9"
                )

        assert recorder.requests == []


DIFFS_PATH = "/api/networks/101/change-sets/cs1/draft/devices/edge-1/bgp-advertisements"


class TestVerifyBgpStaging:
    async def test_every_key_with_a_row_passes_and_reports_its_change(
        self, recorder: Recorder
    ) -> None:
        recorder.add(
            "GET",
            DIFFS_PATH,
            json_response(
                [
                    {
                        "change": "MODIFIED",
                        "original": RECEIVED,
                        "updateAdded": {**RECEIVED, "med": 50},
                    },
                    {
                        "change": "WITHDRAWN",
                        "original": {**RECEIVED, "prefix": "203.0.113.0/24"},
                        "updateRemoved": {
                            "vrf": "default",
                            "externalPeer": "198.51.100.2",
                            "prefix": "203.0.113.0/24",
                        },
                    },
                ]
            ),
        )
        staged = BgpAdvertisement.model_validate({**RECEIVED, "med": 50})
        withdrawn = BgpAdvertisementKey(
            vrf="default", external_peer="198.51.100.2", prefix="203.0.113.0/24"
        )
        async with make_client(recorder) as client:
            report = await client.change_sets.handle("cs1").verify_bgp_staging(
                "edge-1", staged=[staged], withdrawn=[withdrawn]
            )

        assert recorder.query_for() == {"view": ["diffs"]}
        assert [o.change for o in report.staged] == [Change.modified]
        assert [o.change for o in report.withdrawn] == [Change.withdrawn]
        assert report.unmatched == []

    async def test_a_withdrawal_that_reads_absent_is_raised_with_the_report(
        self, recorder: Recorder
    ) -> None:
        recorder.add(
            "GET",
            DIFFS_PATH,
            json_response(
                [
                    {
                        "change": "ABSENT",
                        "updateRemoved": {
                            "vrf": "default",
                            "externalPeer": "198.51.100.2",
                            "prefix": "203.0.113.0/24",
                        },
                    }
                ]
            ),
        )
        withdrawn = BgpAdvertisementKey(
            vrf="default", external_peer="198.51.100.2", prefix="203.0.113.0/24"
        )
        async with make_client(recorder) as client:
            with pytest.raises(
                ForwardBgpKeyMatchedNothingError, match=r"withdraw 203\.0\.113\.0/24"
            ) as caught:
                await client.change_sets.handle("cs1").verify_bgp_staging(
                    "edge-1", withdrawn=[withdrawn]
                )

        outcome = caught.value.report.withdrawn[0]
        assert outcome.found and outcome.change == Change.absent

    async def test_a_staged_key_with_no_row_is_raised(self, recorder: Recorder) -> None:
        recorder.add("GET", DIFFS_PATH, json_response([]))
        staged = BgpAdvertisement.model_validate(RECEIVED)
        async with make_client(recorder) as client:
            with pytest.raises(
                ForwardBgpKeyMatchedNothingError, match=r"stage 192\.0\.2\.0/24"
            ) as caught:
                await client.change_sets.handle("cs1").verify_bgp_staging("edge-1", staged=[staged])

        assert caught.value.report.staged[0].found is False

    async def test_a_bare_address_matches_the_host_route_forward_writes_back(
        self, recorder: Recorder
    ) -> None:
        recorder.add(
            "GET",
            DIFFS_PATH,
            json_response(
                [{"change": "INJECTED", "updateAdded": {**RECEIVED, "prefix": "192.0.2.7/32"}}]
            ),
        )
        staged = BgpAdvertisement.model_validate({**RECEIVED, "prefix": "192.0.2.7"})
        async with make_client(recorder) as client:
            report = await client.change_sets.handle("cs1").verify_bgp_staging(
                "edge-1", staged=[staged]
            )

        assert report.staged[0].change == Change.injected

    async def test_injected_where_a_replace_was_meant_is_reported_not_raised(
        self, recorder: Recorder
    ) -> None:
        # An EBGP/IBGP mismatch: the received route stays and the staged one is added beside it.
        recorder.add(
            "GET",
            DIFFS_PATH,
            json_response([{"change": "INJECTED", "updateAdded": {**RECEIVED, "type": "IBGP"}}]),
        )
        staged = BgpAdvertisement.model_validate({**RECEIVED, "type": "IBGP"})
        async with make_client(recorder) as client:
            report = await client.change_sets.handle("cs1").verify_bgp_staging(
                "edge-1", staged=[staged]
            )

        assert report.staged[0].change == Change.injected
        assert report.unmatched == []


class TestActionNotSupported:
    async def test_an_unmapped_action_is_flagged(self, recorder: Recorder) -> None:
        message = (
            'Parameter conditions "action=bulkAdd" not met for actual request parameters: '
            "action={bulkAdd}"
        )
        recorder.add("POST", DIFFS_PATH, json_response({"message": message}, status=400))
        async with make_client(recorder) as client:
            with pytest.raises(ForwardBadRequestError) as caught:
                await client.bgp_advertisements.add_bgp_advertisements(
                    change_set_id="cs1", device_name="edge-1", body=[RECEIVED]
                )

        assert caught.value.action_not_supported

    async def test_a_plain_text_body_is_flagged_too(self, recorder: Recorder) -> None:
        recorder.add(
            "POST",
            DIFFS_PATH,
            httpx.Response(
                400,
                text='Parameter conditions "action=bulkAdd" not met for actual request parameters',
                headers={"content-type": "text/plain"},
            ),
        )
        async with make_client(recorder) as client:
            with pytest.raises(ForwardBadRequestError) as caught:
                await client.bgp_advertisements.add_bgp_advertisements(
                    change_set_id="cs1", device_name="edge-1", body=[RECEIVED]
                )

        assert caught.value.action_not_supported

    async def test_an_ordinary_bad_request_is_not(self, recorder: Recorder) -> None:
        recorder.add(
            "POST",
            DIFFS_PATH,
            json_response({"message": "asPath must be non-empty for EBGP"}, status=400),
        )
        async with make_client(recorder) as client:
            with pytest.raises(ForwardBadRequestError) as caught:
                await client.bgp_advertisements.add_bgp_advertisements(
                    change_set_id="cs1", device_name="edge-1", body=[RECEIVED]
                )

        assert not caught.value.action_not_supported

    async def test_the_same_words_on_a_404_are_not(self, recorder: Recorder) -> None:
        recorder.add(
            "POST",
            DIFFS_PATH,
            json_response({"message": 'Parameter conditions "x" not met'}, status=404),
        )
        async with make_client(recorder) as client:
            with pytest.raises(ForwardNotFoundError) as caught:
                await client.bgp_advertisements.add_bgp_advertisements(
                    change_set_id="cs1", device_name="edge-1", body=[RECEIVED]
                )

        assert not caught.value.action_not_supported


class TestUpdateCheck:
    async def test_only_the_fields_set_are_sent_and_an_empty_tag_list_clears(
        self, recorder: Recorder
    ) -> None:
        recorder.add(
            "PATCH",
            "/api/snapshots/s1/checks/C-1",
            json_response({"id": "C-1", "name": "renamed", "enabled": False, "tags": []}),
        )
        patch = CheckUpdate(name="renamed", enabled=False, tags=[])
        async with make_client(recorder) as client:
            check = await client.checks.update_check(
                snapshot_id="s1", check_id="C-1", body=patch.to_api()
            )

        assert recorder.body_for() == {"name": "renamed", "enabled": False, "tags": []}
        assert check.id == "C-1" and check.tags == []

    async def test_tags_left_unset_are_not_sent(self) -> None:
        assert CheckUpdate(priority="HIGH").to_api() == {"priority": "HIGH"}


class TestBgpCollectionRequirements:
    async def test_reads_the_requirement_groups(self, recorder: Recorder) -> None:
        recorder.add(
            "GET",
            "/api/networks/101/bgp-collection-requirements",
            json_response({"requirements": [{"devices": ["edge-1", "edge-2"]}]}),
        )
        async with make_client(recorder) as client:
            result = await client.bgp_advertisements.get_bgp_collection_requirements()

        assert result.requirements is not None
        assert result.requirements[0].devices == ["edge-1", "edge-2"]


CHECK_DIFFS = "/api/diffs/s1/s2/checks"


def check_row(diff_type: str, status: str = "PASS") -> dict[str, object]:
    return {
        "diffType": diff_type,
        "a": {"id": "C-1", "status": "PASS"},
        "b": {"id": "C-1", "status": status},
    }


class TestWaitForCheckDiffs:
    async def test_reads_again_while_a_row_is_processing(
        self, recorder: Recorder, no_sleep: list[float]
    ) -> None:
        recorder.add(
            "GET",
            CHECK_DIFFS,
            json_response({"checks": [check_row("PROCESSING")]}),
            json_response({"checks": [check_row("MODIFIED", "FAIL")]}),
        )
        async with make_client(recorder) as client:
            result = await client.snapshot_diffs.wait_for_check_diffs("s1", "s2", ["NQE"])

        assert len(recorder.requests) == 2
        assert recorder.query_for() == {"type": ["NQE"]}
        assert result.checks is not None and result.checks[0].diff_type == "MODIFIED"

    async def test_a_side_still_processing_counts_too(
        self, recorder: Recorder, no_sleep: list[float]
    ) -> None:
        recorder.add(
            "GET",
            CHECK_DIFFS,
            json_response({"checks": [check_row("MODIFIED", "PROCESSING")]}),
            json_response({"checks": [check_row("MODIFIED", "FAIL")]}),
        )
        async with make_client(recorder) as client:
            await client.snapshot_diffs.wait_for_check_diffs("s1", "s2", ["NQE"])

        assert len(recorder.requests) == 2

    async def test_a_finished_answer_returns_on_the_first_read(self, recorder: Recorder) -> None:
        recorder.add("GET", CHECK_DIFFS, json_response({"checks": [check_row("UNCHANGED")]}))
        async with make_client(recorder) as client:
            await client.snapshot_diffs.wait_for_check_diffs("s1", "s2", ["Isolation"])

        assert len(recorder.requests) == 1

    async def test_the_wait_backs_off_to_the_ceiling(
        self, recorder: Recorder, no_sleep: list[float]
    ) -> None:
        processing = json_response({"checks": [check_row("PROCESSING")]})
        done = json_response({"checks": []})
        recorder.add("GET", CHECK_DIFFS, processing, processing, processing, processing, done)
        async with make_client(recorder) as client:
            await client.snapshot_diffs.wait_for_check_diffs(
                "s1", "s2", ["NQE"], poll_interval=1.0, max_poll_interval=3.0
            )

        assert no_sleep == [1.0, 2.0, 3.0, 3.0]

    async def test_still_processing_at_the_deadline_raises_with_the_last_answer(
        self, recorder: Recorder, no_sleep: list[float]
    ) -> None:
        recorder.add("GET", CHECK_DIFFS, json_response({"checks": [check_row("PROCESSING")]}))
        async with make_client(recorder) as client:
            with pytest.raises(ForwardTimeoutError) as caught:
                await client.snapshot_diffs.wait_for_check_diffs(
                    "s1", "s2", ["NQE"], timeout=10.0, poll_interval=2.0
                )

        assert caught.value.partial.checks[0].diff_type == "PROCESSING"  # type: ignore[attr-defined]


class TestWaitForCheckDiffsArguments:
    async def test_a_single_type_given_as_a_string_is_one_type(self, recorder: Recorder) -> None:
        recorder.add("GET", CHECK_DIFFS, json_response({"checks": []}))
        async with make_client(recorder) as client:
            await client.snapshot_diffs.wait_for_check_diffs("s1", "s2", "NQE")

        assert recorder.query_for() == {"type": ["NQE"]}

    async def test_no_types_is_refused_before_sending(self, recorder: Recorder) -> None:
        async with make_client(recorder) as client:
            with pytest.raises(ValueError, match="at least one check type"):
                await client.snapshot_diffs.wait_for_check_diffs("s1", "s2", [])

        assert recorder.requests == []

    async def test_a_zero_interval_still_pauses(
        self, recorder: Recorder, no_sleep: list[float]
    ) -> None:
        recorder.add(
            "GET",
            CHECK_DIFFS,
            json_response({"checks": [check_row("PROCESSING")]}),
            json_response({"checks": []}),
        )
        async with make_client(recorder) as client:
            await client.snapshot_diffs.wait_for_check_diffs("s1", "s2", ["NQE"], poll_interval=0)

        assert no_sleep and all(delay > 0 for delay in no_sleep)

    async def test_the_last_sleep_is_cut_to_the_deadline(
        self, recorder: Recorder, no_sleep: list[float]
    ) -> None:
        recorder.add("GET", CHECK_DIFFS, json_response({"checks": [check_row("PROCESSING")]}))
        async with make_client(recorder) as client:
            with pytest.raises(ForwardTimeoutError):
                await client.snapshot_diffs.wait_for_check_diffs(
                    "s1", "s2", ["NQE"], timeout=5.0, poll_interval=4.0
                )

        assert sum(no_sleep) <= 5.0 + 1e-6


def classic(name: str, **fields: object) -> dict[str, object]:
    return {"name": name, "type": "CISCO_IOS_SSH", "host": "192.0.2.10", **fields}


class TestBgpAdvertisementReadiness:
    def script(
        self,
        recorder: Recorder,
        *,
        config: httpx.Response | None = None,
        devices: list[dict[str, object]] | None = None,
        groups: list[dict[str, object]] | None = None,
    ) -> None:
        recorder.add(
            "GET",
            "/api/config",
            # Forward keys the properties in lowercase (seen live on 26.11).
            config
            or json_response(
                {"predict_modeling": True, "predict_model_ext_adv": True, "predict_model_bgp": True}
            ),
        )
        recorder.add(
            "GET",
            "/api/networks/101/classic-devices",
            json_response({"devices": devices if devices is not None else []}),
        )
        recorder.add(
            "GET",
            "/api/networks/101/bgp-collection-requirements",
            json_response({"requirements": groups or []}),
        )

    async def test_a_device_collecting_received_routes_is_ready(self, recorder: Recorder) -> None:
        self.script(
            recorder,
            devices=[classic("Edge-1", collectBgpAdvertisements=True)],
            groups=[{"devices": ["EDGE-1"]}],
        )
        async with make_client(recorder) as client:
            report = await client.change_sets.bgp_advertisement_readiness(" edge-1 ")

        assert report.ready, report.problems
        assert report.predict_modeling is True and report.predict_model_ext_adv is True
        assert report.device == "edge-1" and report.device_found
        # Forward leaves both out when they are BOTH.
        assert (report.bgp_table_type, report.bgp_peer_type) == ("BOTH", "BOTH")
        assert report.required_by_network
        assert recorder.query_for(0) == {"filter": ["OFF"]}

    async def test_each_silent_failure_is_a_problem(self, recorder: Recorder) -> None:
        self.script(
            recorder,
            config=json_response(
                {
                    "predict_modeling": False,
                    "predict_model_ext_adv": False,
                    "predict_model_bgp": False,
                }
            ),
            devices=[classic("edge-1", collectBgpAdvertisements=True, bgpTableType="ADJ_RIB_OUT")],
        )
        async with make_client(recorder) as client:
            report = await client.change_sets.bgp_advertisement_readiness("edge-1")

        assert not report.ready
        assert [p.split(" ")[0] for p in report.problems] == [
            "PREDICT_MODELING",
            "PREDICT_MODEL_BGP",
            "PREDICT_MODEL_EXT_ADV",
            "bgpTableType",
        ]
        assert report.blocked
        assert not report.required_by_network

    async def test_collection_off_is_a_problem_and_no_classic_source_is_unknown(
        self, recorder: Recorder
    ) -> None:
        self.script(recorder, devices=[classic("edge-1")])
        async with make_client(recorder) as client:
            off = await client.change_sets.bgp_advertisement_readiness("edge-1")
            missing = await client.change_sets.bgp_advertisement_readiness("edge-9")

        assert off.problems == [
            "collectBgpAdvertisements is not on for the device, so there are no received routes "
            "to override or withdraw"
        ]
        assert off.blocked
        # A controller-managed device has no classic source: not checkable, not a problem.
        assert not missing.device_found and missing.problems == []
        assert missing.unknown == ["collection-source:edge-9"]
        assert "no classic collection source" in missing.unknown_reasons["collection-source:edge-9"]
        assert not missing.ready and not missing.blocked

    async def test_many_devices_share_three_reads(self, recorder: Recorder) -> None:
        self.script(
            recorder,
            devices=[
                classic("edge-1", collectBgpAdvertisements=True),
                classic("edge-2", collectBgpAdvertisements=False),
            ],
            groups=[{"devices": ["edge-2"]}],
        )
        async with make_client(recorder) as client:
            reports = await client.change_sets.bgp_advertisement_readiness_for(
                ["edge-2", "edge-1", "edge-9"]
            )

        assert len(recorder.requests) == 3
        assert [r.device for r in reports] == ["edge-2", "edge-1", "edge-9"]
        assert [r.ready for r in reports] == [False, True, False]
        assert [r.blocked for r in reports] == [True, False, False]
        assert [r.required_by_network for r in reports] == [True, False, False]
        # The shared lists are copied, not aliased between devices.
        assert reports[1].problems == [] and reports[1].unknown == []
        assert reports[1].unknown_reasons == {}

    async def test_no_devices_or_a_bare_string_is_refused_before_sending(
        self, recorder: Recorder
    ) -> None:
        async with make_client(recorder) as client:
            with pytest.raises(ValueError, match="at least one device"):
                await client.change_sets.bgp_advertisement_readiness_for([])
            with pytest.raises(ValueError, match="list of names"):
                await client.change_sets.bgp_advertisement_readiness_for("edge-1")

        assert recorder.requests == []

    async def test_no_access_to_org_config_is_unknown_not_ready_and_not_blocked(
        self, recorder: Recorder
    ) -> None:
        self.script(
            recorder,
            config=json_response({"message": "Access denied"}, status=403),
            devices=[classic("edge-1", collectBgpAdvertisements=True)],
        )
        async with make_client(recorder) as client:
            report = await client.change_sets.bgp_advertisement_readiness("edge-1")

        assert report.predict_modeling is None and report.predict_model_ext_adv is None
        assert report.unknown == ["organization-properties"]
        assert report.unknown_reasons["organization-properties"].startswith(
            "organization properties could not be read"
        )
        assert report.notes[0].startswith("organization properties could not be read")
        assert not report.ready and not report.blocked

    async def test_properties_the_api_does_not_return_are_unknown(self, recorder: Recorder) -> None:
        self.script(
            recorder,
            config=json_response({"predict_modeling": True}),
            devices=[classic("edge-1", collectBgpAdvertisements=True)],
        )
        async with make_client(recorder) as client:
            report = await client.change_sets.bgp_advertisement_readiness("edge-1")

        assert report.unknown == ["PREDICT_MODEL_BGP", "PREDICT_MODEL_EXT_ADV"]
        assert set(report.unknown_reasons) == {"PREDICT_MODEL_BGP", "PREDICT_MODEL_EXT_ADV"}
        assert "not returned by the organization properties API" in report.notes[0]
        assert not report.ready and not report.blocked

    async def test_refused_device_and_unserved_requirement_reads_are_unknown(
        self, recorder: Recorder
    ) -> None:
        recorder.add(
            "GET",
            "/api/config",
            json_response(
                {"predict_modeling": True, "predict_model_ext_adv": True, "predict_model_bgp": True}
            ),
        )
        recorder.add(
            "GET",
            "/api/networks/101/classic-devices",
            json_response({"message": "Access denied"}, status=403),
        )
        recorder.add(
            "GET",
            "/api/networks/101/bgp-collection-requirements",
            json_response(
                {"message": "No endpoint GET /api/networks/101/bgp-collection-requirements."},
                status=404,
            ),
        )
        async with make_client(recorder) as client:
            report = await client.change_sets.bgp_advertisement_readiness("edge-1")

        assert report.unknown == ["classic-devices", "bgp-collection-requirements"]
        assert report.problems == []
        assert not report.ready and not report.blocked

    async def test_a_plain_not_found_is_raised(self, recorder: Recorder) -> None:
        self.script(recorder)
        recorder.routes[("GET", "/api/networks/101/classic-devices")] = [
            json_response({"message": "Network 101 not found"}, status=404)
        ]
        async with make_client(recorder) as client:
            with pytest.raises(ForwardNotFoundError):
                await client.change_sets.bgp_advertisement_readiness("edge-1")

    async def test_a_server_error_is_raised(
        self, recorder: Recorder, no_sleep: list[float]
    ) -> None:
        self.script(recorder, config=json_response({"message": "boom"}, status=500))
        async with make_client(recorder) as client:
            with pytest.raises(ForwardServerError):
                await client.change_sets.bgp_advertisement_readiness("edge-1")

    async def test_a_blank_device_is_refused_before_sending(self, recorder: Recorder) -> None:
        async with make_client(recorder) as client:
            with pytest.raises(ValueError, match="device name"):
                await client.change_sets.bgp_advertisement_readiness("  ")

        assert recorder.requests == []


# Shaped like a live answer (26.11, 2026-11-15); values are invented.
CHECK_RESULT = {
    "type": {"type": "Bag"},
    "diagnostics": [],
    "executionInfo": {"computeDurationMillis": 12},
    "cklbExportable": False,
    "result": {
        "values": [
            {
                "violation": {"value": True},
                "Device": {"value": "edge-1", "metadata": {"type": "DEVICE"}},
                "OS": {"type": "OS", "alternative": {"value": "IOS_XE"}},
                "Management IPs": [{"value": "192.0.2.10"}],
                "Tags": [],
            }
        ],
        "totalNumValues": 3,
        "totalNumValuesIsExact": True,
        "columnSummaries": {},
    },
}


class TestNqeCheckResult:
    async def test_reads_a_check_s_rows_by_its_result_key(self, recorder: Recorder) -> None:
        recorder.add("POST", "/api/nqe/queryResults", json_response(CHECK_RESULT))
        async with make_client(recorder) as client:
            result = await client.nqe.check_result("9", "abc123")

        assert recorder.query_for() == {"snapshotId": ["9"], "resultKey": ["abc123"]}
        assert recorder.body_for() == {}
        assert result.truncated
        assert result.rows() == [
            {
                "violation": True,
                "Device": "edge-1",
                "OS": "IOS_XE",
                "Management IPs": ["192.0.2.10"],
                "Tags": [],
            }
        ]

    async def test_paging_goes_in_the_body_and_item_format_is_dropped(
        self, recorder: Recorder
    ) -> None:
        # Forward refuses itemFormat here (400 Unrecognized field, seen live).
        recorder.add("POST", "/api/nqe/queryResults", json_response(CHECK_RESULT))
        async with make_client(recorder) as client:
            await client.nqe.check_result(
                "9",
                "abc123",
                offset=4,
                limit=2,
                options={"itemFormat": "JSON", "sortBy": {"columnName": "Device"}},
            )

        assert recorder.body_for() == {"offset": 4, "limit": 2, "sortBy": {"columnName": "Device"}}

    async def test_a_page_holding_every_row_is_not_truncated(self, recorder: Recorder) -> None:
        whole = {**CHECK_RESULT, "result": {**CHECK_RESULT["result"], "totalNumValues": 1}}  # type: ignore[dict-item]
        recorder.add("POST", "/api/nqe/queryResults", json_response(whole))
        async with make_client(recorder) as client:
            result = await client.nqe.check_result("9", "abc123")

        assert not result.truncated

    async def test_a_blank_key_is_refused_before_sending(self, recorder: Recorder) -> None:
        async with make_client(recorder) as client:
            with pytest.raises(ValueError, match="resultKey"):
                await client.nqe.check_result("9", " ")

        assert recorder.requests == []

    async def test_a_negative_offset_is_refused_before_sending(self, recorder: Recorder) -> None:
        async with make_client(recorder) as client:
            with pytest.raises(ValueError, match="offset"):
                await client.nqe.check_result("9", "abc123", offset=-1)

        assert recorder.requests == []


LOCATIONS = "/api/networks/101/locations"


class TestRequestModels:
    async def test_a_request_model_goes_out_as_its_api_json(self, recorder: Recorder) -> None:
        recorder.add(
            "POST",
            LOCATIONS,
            json_response({"id": "L1", "name": "Dayton DC", "lat": 39.8, "lng": -84.2}),
        )
        async with make_client(recorder) as client:
            await client.network_locations.add_location(
                body=NewLocation(name="Dayton DC", lat=39.8, lng=-84.2, admin_division="Ohio")
            )

        assert recorder.body_for() == {
            "name": "Dayton DC",
            "lat": 39.8,
            "lng": -84.2,
            "adminDivision": "Ohio",
        }

    async def test_a_list_of_models_goes_out_as_a_list(self, recorder: Recorder) -> None:
        recorder.add("PATCH", LOCATIONS, json_response(None, status=204))
        async with make_client(recorder) as client:
            await client.network_locations.patch_locations(
                body=[LocationBulkPatch(id="L1", lat=1.0), LocationBulkPatch(id="L2", name="b")]
            )

        assert recorder.body_for() == [{"id": "L1", "lat": 1.0}, {"id": "L2", "name": "b"}]


class TestEnsureLocations:
    async def test_creates_the_missing_patches_the_changed_and_leaves_the_rest(
        self, recorder: Recorder
    ) -> None:
        recorder.add(
            "GET",
            LOCATIONS,
            json_response(
                [
                    {"id": "L1", "name": "Same", "lat": 1.0, "lng": 2.0},
                    {"id": "L2", "name": "Moved", "lat": 1.0, "lng": 2.0},
                ]
            ),
        )
        recorder.add(
            "POST", LOCATIONS, json_response({"id": "L3", "name": "New", "lat": 5.0, "lng": 6.0})
        )
        recorder.add(
            "PATCH",
            f"{LOCATIONS}/L2",
            json_response({"id": "L2", "name": "Moved", "lat": 3.0, "lng": 2.0}),
        )
        async with make_client(recorder) as client:
            held = await client.networks.ensure_locations(
                {
                    "Same": {"lat": 1.0, "lng": 2.0},
                    "Moved": {"lat": 3.0, "lng": 2.0},
                    "New": NewLocation(name="ignored", lat=5.0, lng=6.0),
                }
            )

        assert [(r.method, r.url.path) for r in recorder.requests] == [
            ("GET", LOCATIONS),
            ("PATCH", f"{LOCATIONS}/L2"),
            ("POST", LOCATIONS),
        ]
        assert recorder.body_for(1) == {"lat": 3.0}
        # The mapping's key is the name, whatever the model said.
        assert recorder.body_for(2) == {"name": "New", "lat": 5.0, "lng": 6.0}
        assert {name: loc.id for name, loc in held.items()} == {
            "Same": "L1",
            "Moved": "L2",
            "New": "L3",
        }

    async def test_a_second_run_sends_nothing_but_the_read(self, recorder: Recorder) -> None:
        held = {"id": "L1", "name": "A", "lat": 1.0, "lng": 2.0, "adminDivision": "Ohio"}
        recorder.add("GET", LOCATIONS, json_response([held]))
        async with make_client(recorder) as client:
            located = await client.networks.ensure_locations(
                # Python field names, a padded value, and the name in another case.
                {"a ": {"lat": 1, "lng": 2.0, "admin_division": " Ohio"}}
            )

        assert [r.method for r in recorder.requests] == ["GET"]
        assert located["a "].id == "L1" and located["a "].admin_division == "Ohio"

    async def test_bad_input_is_refused_before_anything_is_sent(self, recorder: Recorder) -> None:
        async with make_client(recorder) as client:
            with pytest.raises(ValueError, match="'Broken'"):
                await client.networks.ensure_locations(
                    {"Fine": {"lat": 1.0, "lng": 2.0}, "Broken": {"lat": 1.0}}
                )

        assert recorder.requests == []

    async def test_a_location_without_coordinates_does_not_fail_the_read(
        self, recorder: Recorder
    ) -> None:
        recorder.add(
            "GET",
            LOCATIONS,
            json_response(
                [
                    {"id": "default", "name": "Default"},
                    {"id": "L1", "name": "A", "lat": 1.0, "lng": 2.0},
                ]
            ),
        )
        async with make_client(recorder) as client:
            await client.networks.ensure_locations({"A": {"lat": 1.0, "lng": 2.0}})

        assert [r.method for r in recorder.requests] == ["GET"]


class TestSetDeviceLocations:
    async def test_places_devices_by_location_name(self, recorder: Recorder) -> None:
        recorder.add(
            "GET",
            LOCATIONS,
            json_response(
                [
                    {"id": "L1", "name": "Dayton DC", "lat": 1.0, "lng": 2.0},
                    {"id": "L2", "name": "Ohio Edge", "lat": 1.0, "lng": 2.0},
                ]
            ),
        )
        recorder.add("PATCH", "/api/networks/101/atlas", json_response(None, status=204))
        async with make_client(recorder) as client:
            await client.networks.set_device_locations(
                {"edge-1": "Dayton DC", "edge-2": "Ohio Edge"}
            )

        assert recorder.body_for() == {"edge-1": "L1", "edge-2": "L2"}

    async def test_an_unknown_location_name_is_refused_before_the_update(
        self, recorder: Recorder
    ) -> None:
        recorder.add("GET", LOCATIONS, json_response([]))
        async with make_client(recorder) as client:
            with pytest.raises(ValueError, match="no location named 'Nowhere'"):
                await client.networks.set_device_locations({"edge-1": "Nowhere"})

        assert [r.method for r in recorder.requests] == ["GET"]


PAIRS = "/api/diffs/s1/s2/subnet-connectivity"


class TestWaitForLocationPairs:
    async def test_waits_for_the_comparison_then_reads_the_pairs(
        self, recorder: Recorder, no_sleep: list[float]
    ) -> None:
        partial = {"isPartialResult": True, "totalSubnetPairs": 10, "evaluatedSubnetPairs": 3}
        settled = {"isPartialResult": False, "totalSubnetPairs": 10, "evaluatedSubnetPairs": 10}
        pairs = {"counts": [{"src": "1", "dst": "3", "count": 14}]}

        def answer(request: httpx.Request) -> httpx.Response:
            if request.url.params.get("view") == "locationPairs":
                return json_response(pairs)
            return json_response(partial if len(recorder.requests) == 1 else settled)

        recorder.default = answer
        async with make_client(recorder) as client:
            result = await client.snapshot_diffs.wait_for_location_pairs("s1", "s2", "ADDED")

        assert [r.url.params.get("view") for r in recorder.requests][-1] == "locationPairs"
        assert recorder.query_for()["type"] == ["ADDED"]
        assert len(recorder.requests) == 3
        assert result.pairs.counts is not None and result.pairs.counts[0].count == 14
        assert not result.no_forwarding_change

    async def test_no_forwarding_change_is_flagged_and_type_case_is_forgiven(
        self, recorder: Recorder
    ) -> None:
        def answer(request: httpx.Request) -> httpx.Response:
            if request.url.params.get("view") == "locationPairs":
                return json_response({"counts": []})
            return json_response({"isPartialResult": False, "totalSubnetPairs": 0})

        recorder.default = answer
        async with make_client(recorder) as client:
            result = await client.snapshot_diffs.wait_for_location_pairs("s1", "s2", "added")

        assert result.no_forwarding_change and result.pairs.counts == []
        assert result.nothing_compared  # the 0.1.38 name
        assert recorder.query_for()["type"] == ["ADDED"]

    async def test_all_is_refused_before_sending(self, recorder: Recorder) -> None:
        async with make_client(recorder) as client:
            with pytest.raises(ValueError, match="ADDED, DELETED or MODIFIED"):
                await client.snapshot_diffs.wait_for_location_pairs("s1", "s2", "ALL")

        assert recorder.requests == []


class TestNqeExport:
    async def test_org_sources_come_back_by_path(self, recorder: Recorder) -> None:
        recorder.add(
            "POST",
            "/api/users/current/nqe/changes",
            httpx.Response(200, content=zipped(export(("/A/q", "select {}")))),
        )
        async with make_client(recorder) as client:
            sources = await client.nqe.repo.export_org_sources(["/A/", " /B/q "])

        request = recorder.requests[-1]
        assert recorder.query_for() == {"action": ["export"]}
        assert recorder.body_for() == {"paths": ["/A/", "/B/q"]}
        assert request.headers["accept"] == "application/zip"
        assert sources == {"/A/q": "select {}"}

    async def test_forward_sources_use_the_forward_library_route(self, recorder: Recorder) -> None:
        recorder.add(
            "POST",
            "/api/nqe/repos/fwd/commits/head/queries",
            httpx.Response(200, content=zipped(export())),
        )
        async with make_client(recorder) as client:
            assert await client.nqe.repo.export_forward_sources(["/"]) == {}

    async def test_bad_paths_and_no_paths_send_nothing(self, recorder: Recorder) -> None:
        async with make_client(recorder) as client:
            with pytest.raises(ForwardConfigurationError, match="must start with /"):
                await client.nqe.repo.export_org_sources(["Devices"])
            with pytest.raises(ForwardConfigurationError, match="list of library paths"):
                await client.nqe.repo.export_org_sources("/")
            assert await client.nqe.repo.export_org_sources([]) == {}

        assert recorder.requests == []


class TestNqeExportSize:
    async def test_an_answer_larger_than_the_cap_stops_the_download(
        self, recorder: Recorder, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(nqe_repo_service, "MAX_EXPORT_BYTES", 100)
        undeclared = httpx.Response(200, content=b"x" * 101)
        del undeclared.headers["content-length"]  # only the streaming cap can stop it
        recorder.add("POST", "/api/users/current/nqe/changes", undeclared)
        async with make_client(recorder) as client:
            with pytest.raises(ForwardResponseError, match="larger than the SDK reads"):
                await client.nqe.repo.export_org_sources(["/"])

    async def test_a_declared_size_over_the_cap_is_refused_before_reading(
        self, recorder: Recorder, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(nqe_repo_service, "MAX_EXPORT_BYTES", 100)
        recorder.add(
            "POST",
            "/api/users/current/nqe/changes",
            httpx.Response(200, content=b"x" * 10, headers={"content-length": "5000"}),
        )
        async with make_client(recorder) as client:
            with pytest.raises(ForwardResponseError, match="larger than the SDK reads"):
                await client.nqe.repo.export_org_sources(["/"])


class TestFwdAppConnection:
    async def test_the_test_proxy_and_cve_update_routes(self, recorder: Recorder) -> None:
        recorder.add("POST", "/api/fwd-app-connection", json_response({"status": "AUTH_FAILED"}))
        recorder.add(
            "GET",
            "/api/fwd-app-proxy",
            json_response(
                {
                    "enabled": True,
                    "url": "http://proxy.example.com:3128",
                    # Forward unwraps the credential: username sits at the top level.
                    "username": "u",
                    "updatedAt": "2026-11-01T00:00:00Z",
                }
            ),
        )
        recorder.add("POST", "/api/cve-index", json_response({"error": "CONNECTION_FAILED"}))
        async with make_client(recorder) as client:
            test = await client.system_administration.test_fwd_app_connection()
            assert recorder.query_for() == {"action": ["test"]}
            proxy = await client.system_administration.get_fwd_app_proxy()
            update = await client.system_administration.update_cve_index_from_software_central()
            assert recorder.query_for() == {"action": ["update"]}

        assert test.status == "AUTH_FAILED"
        assert proxy.enabled and proxy.username == "u"
        assert proxy.updated_at is not None  # the attribution Forward unwraps beside it
        assert update.error == "CONNECTION_FAILED"


CLASSIC = "/api/networks/101/classic-devices"


class TestRegisterDevices:
    async def test_unknown_devices_are_added_without_collection(self, recorder: Recorder) -> None:
        recorder.add("GET", CLASSIC, json_response({"devices": [{"name": "R1", "host": "r1"}]}))
        recorder.add("POST", CLASSIC, json_response(None, status=204))
        async with make_client(recorder) as client:
            added = await client.networks.register_devices(["r1", "r2", "edge-3"])

        assert added == ["r2", "edge-3"]  # r1 is already known, ignoring case
        assert recorder.body_for() == [
            {"name": "r2", "host": "r2", "collect": False},
            {"name": "edge-3", "host": "edge-3", "collect": False},
        ]

    async def test_a_mapping_gives_each_device_its_host(self, recorder: Recorder) -> None:
        recorder.add("GET", CLASSIC, json_response({"devices": []}))
        recorder.add("POST", CLASSIC, json_response(None, status=204))
        async with make_client(recorder) as client:
            await client.networks.register_devices({"r1.": "192.0.2.1"})

        assert recorder.body_for() == [{"name": "r1.", "host": "192.0.2.1", "collect": False}]

    async def test_a_second_run_sends_only_the_read(self, recorder: Recorder) -> None:
        recorder.add("GET", CLASSIC, json_response({"devices": [{"name": "r1", "host": "r1"}]}))
        async with make_client(recorder) as client:
            assert await client.networks.register_devices(["r1"]) == []

        assert [r.method for r in recorder.requests] == ["GET"]

    @pytest.mark.parametrize(
        "name",
        # Valid device names that Forward's Hostnames pattern refuses as a host.
        ["r1.", "-lead", "lead-", "x" * 64, ".".join(["a" * 63] * 4), "a..b"],
    )
    async def test_a_name_that_is_not_a_hostname_is_refused_before_sending(
        self, recorder: Recorder, name: str
    ) -> None:
        async with make_client(recorder) as client:
            with pytest.raises(
                ForwardConfigurationError, match="not a valid IP address or hostname"
            ):
                await client.networks.register_devices([name])

        assert recorder.requests == []

    @pytest.mark.parametrize("host", ["fe80::1%eth0", "edge-1\n"])
    async def test_a_scoped_or_newline_ended_host_is_refused(
        self, recorder: Recorder, host: str
    ) -> None:
        async with make_client(recorder) as client:
            with pytest.raises(
                ForwardConfigurationError, match="not a valid IP address or hostname"
            ):
                await client.networks.register_devices({"edge-1": host})

        assert recorder.requests == []

    async def test_a_name_ending_in_a_newline_is_refused(self, recorder: Recorder) -> None:
        async with make_client(recorder) as client:
            with pytest.raises(ForwardConfigurationError, match="device names take only"):
                await client.networks.register_devices({"r1\n": "192.0.2.1"})

        assert recorder.requests == []

    @pytest.mark.parametrize("name", ["core_1", "Edge-01.lab", "r1", "a" * 63])
    async def test_hostnames_forward_accepts_pass(self, recorder: Recorder, name: str) -> None:
        recorder.add("GET", CLASSIC, json_response({"devices": []}))
        recorder.add("POST", CLASSIC, json_response(None, status=204))
        async with make_client(recorder) as client:
            assert await client.networks.register_devices([name]) == [name]

    @pytest.mark.parametrize(
        "devices",
        [{"has space": "192.0.2.1"}, ["2001:db8::1"], {"caf\u00e9": "192.0.2.1"}],
    )
    async def test_a_device_name_forward_refuses_is_refused_before_sending(
        self, recorder: Recorder, devices: object
    ) -> None:
        async with make_client(recorder) as client:
            with pytest.raises(ForwardConfigurationError, match="device names take only"):
                await client.networks.register_devices(devices)  # type: ignore[arg-type]

        assert recorder.requests == []

    async def test_names_differing_only_in_case_are_refused(self, recorder: Recorder) -> None:
        async with make_client(recorder) as client:
            with pytest.raises(ForwardConfigurationError, match="differ only in case"):
                await client.networks.register_devices(["sw1", "SW1"])

        assert recorder.requests == []
