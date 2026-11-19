"""``client.change_reports.compare``: each section read per the shared ``## Reads`` rules.

A fake Forward answers every read; the expected values are written from the
rules in ``tests/fixtures/change_report/RULES.md``, not from the reader.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from forward_sdk._async.client import AsyncForwardClient
from forward_sdk._async.services import change_reports as change_reports_module
from forward_sdk.change_report import Flow
from forward_sdk.nqe.state import BgpSession, InterfaceAdmin, LldpAdjacency
from forward_sdk.testing import snapshot_payload
from forward_sdk.trace import Endpoint, PathQuery, device
from tests.conftest import Recorder, json_response

pytestmark = pytest.mark.anyio

DIFF = "/api/diffs/s1/s2"
NO_ENDPOINT = {"message": "No endpoint GET /api/diffs."}


def check(check_id: str, status: str, name: str = "c") -> dict[str, Any]:
    return {"id": check_id, "name": name, "status": status}


def facets(
    groups: dict[str, int], *, timed_out: bool = False, total_type: str = "EXACT"
) -> dict[str, Any]:
    return {
        "timedOut": timed_out,
        "totalResults": {"value": 1, "type": total_type},
        "facets": {
            "pathGroups": [
                {"traceType": kind, "paths": [{"path": [f"d{i}"]} for i in range(n)]}
                for kind, n in groups.items()
            ]
        },
    }


class FakeForward:
    """Answers each read; ``overrides`` replaces one route's answer by a key."""

    def __init__(self, *, trigger: str = "COLLECTION", state: str = "PROCESSED") -> None:
        self.answers: dict[str, Callable[[httpx.Request], httpx.Response]] = {}
        self.trigger, self.state = trigger, state
        self.check_rows: dict[str, list[dict[str, Any]]] = {
            "Existential": [
                {
                    "diffType": "MODIFIED",
                    "a": check("C-3", "PASS", "app"),
                    "b": check("C-3", "FAIL", "app"),
                },
                {"diffType": "UNCHANGED", "a": check("C-1", "PASS"), "b": check("C-1", "PASS")},
            ],
            "Isolation": [{"diffType": "ADDED", "b": check("C-4", "PASS", "new")}],
        }
        self.facets_by_snapshot: dict[str, dict[str, Any]] = {}

    def handle(self, request: httpx.Request) -> httpx.Response:
        path, params = request.url.path, request.url.params
        for key, answer in self.answers.items():
            if key == f"{request.method} {path}?{params}" or key == f"{request.method} {path}":
                return answer(request)
        if path == "/api/networks/101/snapshots":
            return json_response(
                {
                    "snapshots": [
                        snapshot_payload("s1"),
                        snapshot_payload("s2", state=self.state, processingTrigger=self.trigger),
                    ]
                }
            )
        if path == f"{DIFF}/checks":
            kind = params.get("type")
            if kind == "QueryStringBased":
                return json_response(NO_ENDPOINT, status=404)
            return json_response({"checks": self.check_rows.get(kind, [])})
        if path == f"{DIFF}/subnet-connectivity" and params.get("view") == "locationPairs":
            pairs = {"DELETED": [{"src": "site-b", "dst": "site-a", "count": 3}]}
            return json_response({"counts": pairs.get(params["type"], [])})
        if path == f"{DIFF}/subnet-connectivity":
            return json_response(
                {"isPartialResult": False, "totalSubnetPairs": 42, "evaluatedSubnetPairs": 40}
            )
        if path == f"{DIFF}/l3":
            return json_response(
                {
                    "deviceInfos": [
                        {
                            "deviceName": "edge-1",
                            "stats": {"ADDED": 1, "DELETED": 0, "MODIFIED": 2},
                        },
                        {
                            "deviceName": "edge-9",
                            "stats": {"ADDED": 0, "DELETED": 0, "MODIFIED": 0, "UNCHANGED": 7},
                        },
                    ]
                }
            )
        if path == f"{DIFF}/files":
            assert params.get("type") == "CONFIG"
            return json_response(
                {
                    "deviceInfos": [
                        {"deviceName": "edge-2", "files": [{"name": "edge-2,running_config.txt"}]},
                    ]
                }
            )
        if path == f"{DIFF}/devices" and "count" in params:
            return json_response({"count": 2, "complete": True})
        if path == f"{DIFF}/devices":
            return json_response(
                [
                    {"diffType": "ADDED", "b": {"name": "core-9"}},
                    {"diffType": "DELETED", "a": {"name": "edge-7"}},
                    {"diffType": "MODIFIED", "a": {"name": "edge-1"}, "b": {"name": "edge-1"}},
                    {"diffType": "UNCHANGED", "a": {"name": "edge-3"}, "b": {"name": "edge-3"}},
                ]
            )
        if path == f"{DIFF}/cloud-acl":
            return json_response(NO_ENDPOINT, status=404)
        if path in {
            f"{DIFF}/{a}" for a in ("interfaces", "topology", "l2", "acl", "cloud-objects")
        }:
            return json_response({"count": 1, "complete": path.endswith("/topology") is False})
        if path == f"{DIFF}/routing-loop/count":
            return json_response({"count": 0, "complete": True})
        if path == f"{DIFF}/vulnerabilities/counts":
            return json_response(
                {
                    "warnings": {"cveCount": 4, "exposedVulnerableDevicesCount": 2},
                    "improvements": {"cveCount": 1},
                }
            )
        if path == "/api/networks/101/trace-facets":
            snapshot = params.get("snapshotId")
            return json_response(self.facets_by_snapshot[snapshot or ""])
        raise AssertionError(f"unexpected {request.method} {request.url}")


def make_client(fake: FakeForward) -> AsyncForwardClient:
    recorder = Recorder()
    recorder.default = fake.handle
    return AsyncForwardClient(
        "https://forward.test",
        username="k",
        password="s",
        rate_limit_rpm=None,
        network_id="101",
        transport=recorder.transport,
    )


async def test_a_collected_comparison_reads_every_section_per_the_rules() -> None:
    fake = FakeForward()
    async with make_client(fake) as client:
        report = await client.change_reports.compare("s1", "s2", created_at="2026-01-01T00:00:00Z")

    data = report.data
    assert data["meta"]["after_kind"] == "collected" and data["meta"]["network_id"] == "101"
    s = data["sections"]
    # checks: one row per check per type, ids from a/b, QueryStringBased 404 contributes nothing.
    assert [(i["check_id"], i["type"], i["change"]) for i in s["checks"]["items"]] == [
        ("C-3", "Existential", "regressed"),
        ("C-4", "Isolation", "added"),
        ("C-1", "Existential", "unchanged"),
    ]
    assert (
        s["connectivity"]["total_subnet_pairs"] == 42
        and s["connectivity"]["evaluated_subnet_pairs"] == 40
    )
    assert s["connectivity"]["location_pairs"]["DELETED"] == [
        {"src": "site-b", "dst": "site-a", "count": 3}
    ]
    # routing: nonzero devices only, DELETED -> removed.
    assert s["routing"]["items"] == [{"device": "edge-1", "added": 1, "removed": 0, "modified": 2}]
    # config: the file name without its "<device>," prefix.
    assert s["config"]["items"] == [{"device": "edge-2", "files_changed": ["running_config.txt"]}]
    assert (s["devices"]["added"], s["devices"]["removed"], s["devices"]["modified"]) == (
        ["core-9"],
        ["edge-7"],
        ["edge-1"],
    )
    # diff_counts: cloud-acl answered "No endpoint", so it is omitted, not an error.
    assert set(s["diff_counts"]["families"]) == {
        "devices",
        "interfaces",
        "topology",
        "l2",
        "acl",
        "cloud_objects",
        "routing_loop",
    }
    assert s["diff_counts"]["families"]["topology"] == {"count": 1, "complete": False}
    assert s["vulnerabilities"] == {
        "status": "ok",
        "introduced": 4,
        "resolved": 1,
        "exposed_devices": 2,
    }
    assert s["predict"]["status"] == "not_applicable" and s["flows"]["status"] == "not_requested"
    assert report.result == "regression"
    assert "## Checks" in report.to_markdown() and report.to_json().endswith("}\n")


async def test_a_check_that_cannot_be_evaluated_is_judged_per_check() -> None:
    # REQUIRES_ADDITIONAL_SNAPSHOT_PROCESSING does not clear by waiting, so it must not
    # blank the section: the regression beside it is still reported.
    fake = FakeForward()
    fake.check_rows["Existential"] = [
        {
            "diffType": "MODIFIED",
            "a": check("C-2", "PASS"),
            "b": check("C-2", "REQUIRES_ADDITIONAL_SNAPSHOT_PROCESSING"),
        },
        {"diffType": "MODIFIED", "a": check("C-3", "PASS"), "b": check("C-3", "FAIL")},
    ]
    async with make_client(fake) as client:
        report = await client.change_reports.compare("s1", "s2")

    checks = report.data["sections"]["checks"]
    assert checks["status"] == "ok" and checks["counts"]["not_evaluated"] == 1
    codes = {(r["code"], r["detail"]) for r in report.data["verdict"]["reasons"]}
    assert codes == {("check_not_evaluated", "C-2"), ("check_regressed", "C-3")}
    assert report.result == "incomplete"


async def test_a_licence_refusal_is_feature_gated_and_does_not_decide_the_verdict() -> None:
    fake = FakeForward()
    fake.answers[f"GET {DIFF}/vulnerabilities/counts"] = lambda _: json_response(
        {"message": "Unlicensed operation: NetworkOperation.VIEW_VULNERABILITIES"}, status=403
    )
    fake.check_rows = {}
    async with make_client(fake) as client:
        report = await client.change_reports.compare("s1", "s2")

    assert report.data["sections"]["vulnerabilities"]["reason"]["kind"] == "feature_gated"
    assert report.result == "no_regression"


async def test_flows_count_paths_per_group_and_an_unfinished_search_is_unavailable() -> None:
    fake = FakeForward()
    fake.check_rows = {}
    fake.facets_by_snapshot = {
        "s1": facets({"VALID": 2, "DROPPED": 1, "INADMISSIBLE": 1}),
        "s2": facets({"VALID": 0, "BLACKHOLE": 3}),
    }
    flow = PathQuery(from_=Endpoint(device("edge-1")))
    async with make_client(fake) as client:
        report = await client.change_reports.compare("s1", "s2", flows=[Flow("app", flow)])
        fake.facets_by_snapshot["s2"] = facets({"VALID": 1}, total_type="LOWER_BOUND")
        capped = await client.change_reports.compare("s1", "s2", flows=[Flow("app", flow)])

    item = report.data["sections"]["flows"]["items"][0]
    assert item["base"] == {
        "valid": 2,
        "dropped": 1,
        "blackhole": 0,
        "unreachable": 0,
        "loop": 0,
        "other": 1,
    }
    assert item["after"]["blackhole"] == 3 and item["change"] == "lost_valid"
    assert report.result == "regression"
    assert capped.data["sections"]["flows"]["items"][0]["change"] == "unavailable"
    assert capped.result == "incomplete"


async def test_a_prediction_reads_predict_and_security_rules() -> None:
    fake = FakeForward(trigger="PREDICT", state="FAILED")
    fake.check_rows = {}
    fake.answers["GET /api/networks/101/change-sets/cs1/draft"] = lambda _: json_response(
        {"draft": {"deviceToChanges": {"fw-1": {}, "sw-1": {}}}}
    )
    fake.answers["GET /api/networks/101/change-sets/cs1/devices/fw-1/security-rules-diff"] = (
        lambda _: json_response(
            {
                "entries": [
                    {
                        "diffType": "ADDED",
                        "b": {
                            "scopeId": "LOCAL",
                            "rulebaseId": "PRIMARY",
                            "definition": {"name": "allow-app"},
                        },
                    },
                    {
                        "diffType": "DELETED",
                        "a": {
                            "scopeId": "LOCAL",
                            "rulebaseId": "PRIMARY",
                            "definition": {"name": "old"},
                        },
                    },
                    {
                        "diffType": "UNCHANGED",
                        "a": {
                            "scopeId": "LOCAL",
                            "rulebaseId": "PRIMARY",
                            "definition": {"name": "same"},
                        },
                    },
                ]
            }
        )
    )
    fake.answers["GET /api/networks/101/change-sets/cs1/devices/sw-1/security-rules-diff"] = (
        lambda _: json_response(
            {"message": "Device is not supported for firewall predict"}, status=400
        )
    )
    async with make_client(fake) as client:
        report = await client.change_reports.compare("s1", "s2", change_set_id="cs1")

    s = report.data["sections"]
    assert report.data["meta"]["after_kind"] == "predicted"
    assert s["predict"]["processing_state"] == "FAILED"
    assert s["predict"]["failures"] == [
        {"device": "-", "message": "snapshot processing ended FAILED"}
    ]
    assert s["predict"]["modeling_problems"] == []
    assert s["security_rules"]["items"] == [
        {"device": "fw-1", "rule": "LOCAL/PRIMARY/allow-app", "change": "added"},
        {"device": "fw-1", "rule": "LOCAL/PRIMARY/old", "change": "removed"},
    ]


async def test_a_prediction_without_a_change_set_cannot_read_security_rules() -> None:
    fake = FakeForward(trigger="PREDICT")
    fake.check_rows = {}
    async with make_client(fake) as client:
        report = await client.change_reports.compare("s1", "s2")

    reason = report.data["sections"]["security_rules"]["reason"]
    assert reason == {"kind": "error", "message": "change set not given"}


async def test_state_reads_both_snapshots_and_keys_each_kind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeForward()
    fake.check_rows = {}
    rows = {
        "s1": (
            [
                BgpSession.model_validate(
                    {
                        "Device": "r1",
                        "VRF": "default",
                        "NeighborIP": "192.0.2.1",
                        "SessionState": "ESTABLISHED",
                    }
                )
            ],
            [
                LldpAdjacency.model_validate(
                    {"Device": "r1", "LocalIntf": "et1", "RemoteSys": "r2", "RemotePort": "et2"}
                )
            ],
            [
                InterfaceAdmin.model_validate(
                    {"Device": "r1", "Interface": "et1", "AdminStatus": "AdminStatus.UP"}
                )
            ],
            {("r1", "acl-a"): 3},
        ),
        "s2": (
            [
                BgpSession.model_validate(
                    {
                        "Device": "r1",
                        "VRF": "default",
                        "NeighborIP": "192.0.2.1",
                        "SessionState": "IDLE",
                    }
                )
            ],
            [],
            [
                InterfaceAdmin.model_validate(
                    {"Device": "r1", "Interface": "et1", "AdminStatus": "AdminStatus.UP"}
                )
            ],
            {("r1", "acl-a"): 3},
        ),
    }
    async with make_client(fake) as client:
        nqe = client.nqe

        async def bgp(*, snapshot_id: str, network_id: str) -> Any:
            return rows[snapshot_id][0]

        async def lldp(*, snapshot_id: str, network_id: str) -> Any:
            return rows[snapshot_id][1]

        async def admin(*, snapshot_id: str, network_id: str) -> Any:
            return rows[snapshot_id][2]

        async def acl(*, snapshot_id: str, network_id: str) -> Any:
            return rows[snapshot_id][3]

        monkeypatch.setattr(nqe, "bgp_sessions", bgp)
        monkeypatch.setattr(nqe, "lldp_adjacencies", lldp)
        monkeypatch.setattr(nqe, "interface_admin", admin)
        monkeypatch.setattr(nqe, "acl_counts", acl)
        report = await client.change_reports.compare("s1", "s2", state=True)

    items = report.data["sections"]["state"]["items"]
    assert [(i["kind"], i["identity"], i["change"]) for i in items] == [
        ("bgp_session", ["r1", "default", "192.0.2.1"], "lost"),
        ("lldp_adjacency", ["r1", "et1", "r2", "et2"], "lost"),
    ]
    assert report.result == "regression"


async def test_the_json_and_markdown_round_trip() -> None:
    fake = FakeForward()
    async with make_client(fake) as client:
        report = await client.change_reports.compare("s1", "s2", created_at="2026-01-01T00:00:00Z")
    assert json.loads(report.to_json()) == report.data


async def test_data_the_rules_do_not_allow_marks_only_that_section_unavailable() -> None:
    # A status a newer Forward might add must not abort the whole report.
    fake = FakeForward()
    fake.check_rows["Existential"] = [
        {"diffType": "MODIFIED", "a": check("C-3", "PASS"), "b": check("C-3", "SOME_NEW_STATUS")}
    ]
    async with make_client(fake) as client:
        report = await client.change_reports.compare("s1", "s2")

    sections = report.data["sections"]
    assert sections["checks"]["status"] == "unavailable"
    assert sections["checks"]["reason"]["kind"] == "error"
    assert sections["routing"]["status"] == "ok"
    assert report.result == "incomplete"


async def test_the_checks_budget_covers_all_check_types_together() -> None:
    fake = FakeForward()
    async with make_client(fake) as client:
        report = await client.change_reports.compare("s1", "s2", section_timeout=0)

    assert report.data["sections"]["checks"]["reason"]["kind"] == "timeout"


async def test_a_refusal_that_is_not_a_licence_is_forbidden() -> None:
    fake = FakeForward()
    fake.check_rows = {}
    fake.answers[f"GET {DIFF}/l3"] = lambda _: json_response(
        {"message": "Access denied"}, status=403
    )
    async with make_client(fake) as client:
        report = await client.change_reports.compare("s1", "s2")

    assert report.data["sections"]["routing"]["reason"]["kind"] == "forbidden"


async def test_security_rules_fall_back_to_the_head_commit_and_tolerate_missing_scopes() -> None:
    fake = FakeForward(trigger="PREDICT")
    fake.check_rows = {}
    fake.answers["GET /api/networks/101/change-sets/cs1/draft"] = lambda _: json_response({})
    fake.answers["GET /api/networks/101/change-sets/cs1/commits/head"] = lambda _: json_response(
        {"deviceToChanges": {"fw-2": {}}}
    )
    fake.answers["GET /api/networks/101/change-sets/cs1/devices/fw-2/security-rules-diff"] = (
        lambda _: json_response(
            {"entries": [{"diffType": "MODIFIED", "b": {"definition": {"name": "r"}}}]}
        )
    )
    async with make_client(fake) as client:
        report = await client.change_reports.compare("s1", "s2", change_set_id="cs1")

    assert report.data["sections"]["security_rules"]["items"] == [
        {"device": "fw-2", "rule": "//r", "change": "modified"}
    ]


async def test_each_check_type_gets_only_what_is_left_of_the_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ticks = iter(range(0, 1000, 10))  # every clock read is 10 s later

    class Clock:
        @staticmethod
        def monotonic() -> float:
            return float(next(ticks))

    monkeypatch.setattr(change_reports_module, "time", Clock)
    fake = FakeForward()
    async with make_client(fake) as client:
        given: list[float] = []
        real = client.snapshot_diffs.wait_for_check_diffs

        async def wait(*args: Any, timeout: float, **kwargs: Any) -> Any:
            given.append(timeout)
            return await real(*args, timeout=timeout, **kwargs)

        monkeypatch.setattr(client.snapshot_diffs, "wait_for_check_diffs", wait)
        report = await client.change_reports.compare("s1", "s2", section_timeout=30)

    assert given == [20.0, 10.0]
    assert report.data["sections"]["checks"]["reason"]["kind"] == "timeout"


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"max_items": 0}, "max_items"),
        ({"after_kind": "predict"}, "after_kind"),
        (
            {"flows": [Flow("a", PathQuery(from_=Endpoint(device("r1"))), expectation="VALID")]},
            "expectation",
        ),
        (
            {"flows": [Flow("a", PathQuery(from_=Endpoint(device("r1"))))] * 2},
            "own name",
        ),
    ],
)
async def test_the_callers_own_mistakes_raise_before_any_read(
    kwargs: dict[str, Any], message: str
) -> None:
    fake = FakeForward()
    reads: list[str] = []
    handle = fake.handle

    def counting(request: httpx.Request) -> httpx.Response:
        reads.append(request.url.path)
        return handle(request)

    fake.handle = counting  # type: ignore[method-assign]
    async with make_client(fake) as client:
        with pytest.raises(ValueError, match=message):
            await client.change_reports.compare("s1", "s2", **kwargs)
    assert reads == []


async def test_a_check_side_without_a_status_is_unexpected_data() -> None:
    fake = FakeForward()
    fake.check_rows["Existential"] = [
        {"diffType": "MODIFIED", "a": {"id": "C-3", "name": "c"}, "b": check("C-3", "PASS")}
    ]
    async with make_client(fake) as client:
        report = await client.change_reports.compare("s1", "s2")

    reason = report.data["sections"]["checks"]["reason"]
    assert reason["kind"] == "error" and "no status" in reason["message"]
