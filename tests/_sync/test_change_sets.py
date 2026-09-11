# Generated from tests/_async/test_change_sets.py by scripts/unasync.py -- do not edit.
# Edit the async source and re-run: uv run python scripts/unasync.py

"""Forward Predict change sets and snapshot diffs.

Unpublished endpoints, described from Forward's server source and verified
against a live instance of the current build. The traps a consumer met while
working around their absence are pinned here, so a signature cannot quietly
lose them.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from forward_sdk import config_value
from forward_sdk._ops import change_sets as ops
from forward_sdk._sync.client import ForwardClient
from forward_sdk.errors import ForwardTimeoutError
from forward_sdk.models import ConfigValue
from tests.conftest import Recorder, json_response

pytestmark = pytest.mark.anyio

CS = "/api/networks/115/change-sets"
SNAPSHOTS = "/api/networks/115/snapshots"


def make_client(recorder: Recorder, **overrides: Any) -> ForwardClient:
    settings: dict[str, Any] = {
        "username": "key",
        "password": "secret",
        "rate_limit_rpm": None,
        "network_id": "115",
        "transport": recorder.transport,
    }
    settings.update(overrides)
    return ForwardClient("https://forward.test", **settings)


def change_set(**extra: Any) -> dict[str, Any]:
    payload = {"id": "CHG-101", "name": "x", "networkId": "115", "snapshotId": "656"}
    payload.update(extra)
    return payload


class TestCreate:
    def test_body_carries_no_network_id(self, recorder: Recorder) -> None:
        """Forward rejects unknown body properties, and networkId is one.

        The network is the path's. A consumer's notes said some builds want it
        in the body too; the source says the opposite, and sending it is a 400.
        """
        recorder.add("POST", CS, json_response(change_set()))
        with make_client(recorder) as client:
            handle = client.change_sets.create("x", snapshot_id="656")

        body = recorder.body_for()
        assert body == {"name": "x", "snapshotId": "656"}
        assert "networkId" not in body
        assert handle.id == "CHG-101"

    def test_optional_fields_are_sent_only_when_given(self, recorder: Recorder) -> None:
        recorder.add("POST", CS, json_response(change_set()))
        with make_client(recorder) as client:
            client.change_sets.create(
                "x", snapshot_id="656", description="d", tags=["a"], dir_path="/team"
            )

        assert recorder.body_for() == {
            "name": "x",
            "snapshotId": "656",
            "description": "d",
            "tags": ["a"],
        }
        assert recorder.query_for()["dirPath"] == ["/team"]


class TestActions:
    """Query-string actions select the operation; they are not parameters."""

    def test_predict_sends_the_required_note(self, recorder: Recorder) -> None:
        """note is required by Forward; omitting it is a 400 there, so the SDK
        requires it too rather than letting the server explain."""
        recorder.add("POST", f"{CS}/CHG-101", json_response({"id": "702"}))
        with make_client(recorder) as client:
            started = client.change_sets.handle("CHG-101").predict("first try")

        query = recorder.query_for()
        assert query["action"] == ["predict"]
        assert query["note"] == ["first try"]
        assert recorder.body_for() is None
        assert started.id == "702"

    def test_predict_is_not_retried(self, recorder: Recorder) -> None:
        """A retry after an ambiguous failure could create a second snapshot."""
        assert ops.predict(network_id="115", change_set_id="CHG-101", note="n").idempotent is False

    def test_bulk_delete_uses_change_set_ids(self, recorder: Recorder) -> None:
        """The key is changeSetIds. ids is refused with a 400."""
        recorder.add("POST", CS, httpx.Response(204))
        with make_client(recorder) as client:
            client.change_sets.delete(["CHG-1", "CHG-2"])

        assert recorder.query_for()["action"] == ["delete"]
        assert recorder.body_for() == {"changeSetIds": ["CHG-1", "CHG-2"]}

    def test_deleting_one_uses_the_direct_route(self, recorder: Recorder) -> None:
        recorder.add("DELETE", f"{CS}/CHG-1", httpx.Response(204))
        with make_client(recorder) as client:
            client.change_sets.delete(["CHG-1"])

        assert recorder.count("DELETE", f"{CS}/CHG-1") == 1

    def test_summary_is_a_view(self, recorder: Recorder) -> None:
        """There is no plain GET on a change set; the view selects the only read."""
        recorder.add("GET", f"{CS}/CHG-101", json_response({"name": "x", "snapshotId": "656"}))
        with make_client(recorder) as client:
            summary = client.change_sets.handle("CHG-101").summary()

        assert recorder.query_for()["view"] == ["summary"]
        assert summary.snapshot_id == "656"


class TestStaging:
    def test_commands_are_sent_as_plain_text(self, recorder: Recorder) -> None:
        """The body is the CLI text itself, not a JSON string of it."""
        recorder.add("PUT", f"{CS}/CHG-101/draft/devices/dc-core/commands", httpx.Response(204))
        with make_client(recorder) as client:
            client.change_sets.handle("CHG-101").set_commands(
                "dc-core", "interface Ethernet1\n shutdown"
            )

        request = recorder.requests[-1]
        assert request.headers["content-type"].startswith("text/plain")
        assert request.content == b"interface Ethernet1\n shutdown"

    def test_draft_is_none_when_nothing_is_staged(self, recorder: Recorder) -> None:
        """Forward answers {draft: null} rather than 404."""
        recorder.add("GET", f"{CS}/CHG-101/draft", json_response({"draft": None}))
        with make_client(recorder) as client:
            assert client.change_sets.handle("CHG-101").draft() is None

    def test_draft_saved_at_is_epoch_millis(self, recorder: Recorder) -> None:
        """Unlike every other timestamp, which is an ISO string."""
        recorder.add(
            "GET",
            f"{CS}/CHG-101/draft",
            json_response({"draft": {**change_set(), "savedAt": 1757584800000}}),
        )
        with make_client(recorder) as client:
            draft = client.change_sets.handle("CHG-101").draft()

        assert draft is not None and draft.saved_at == 1757584800000


class TestPredictAndWait:
    def test_waits_for_the_predicted_snapshot(
        self, recorder: Recorder, no_sleep: list[float]
    ) -> None:
        recorder.add("POST", f"{CS}/CHG-101", json_response({"id": "702"}))
        recorder.add(
            "GET",
            SNAPSHOTS,
            json_response({"snapshots": [{"id": "702", "state": "PROCESSING"}]}),
            json_response(
                {"snapshots": [{"id": "702", "state": "PROCESSED", "processingTrigger": "PREDICT"}]}
            ),
        )
        with make_client(recorder) as client:
            snapshot = client.change_sets.handle("CHG-101").predict_and_wait("n")

        assert snapshot.id == "702"
        assert str(snapshot.state) == "PROCESSED"
        assert recorder.count("GET", SNAPSHOTS) == 2


class TestDiffs:
    DIFF = "/api/diffs/656/702"

    def _partial(self) -> dict[str, Any]:
        return {"evaluatedSubnetPairs": 0, "totalSubnetPairs": 39, "isPartialResult": True}

    def _settled(self) -> dict[str, Any]:
        return {
            "evaluatedSubnetPairs": 39,
            "totalSubnetPairs": 39,
            "newlyIsolatedSubnetPairs": 14,
            "isPartialResult": False,
        }

    def test_waits_until_the_comparison_is_not_partial(
        self, recorder: Recorder, no_sleep: list[float]
    ) -> None:
        """An early read returns zeros that mean "not finished", not "unchanged".

        A snapshot-ready webhook fires the instant a prediction finishes, so a
        caller reacting to it lands in this window almost every time.
        """
        recorder.add(
            "GET",
            f"{self.DIFF}/subnet-connectivity",
            json_response(self._partial()),
            json_response(self._partial()),
            json_response(self._settled()),
        )
        with make_client(recorder) as client:
            result = client.snapshot_diffs.wait_for_subnet_connectivity("656", "702")

        assert result.newly_isolated_subnet_pairs == 14
        assert recorder.count("GET", f"{self.DIFF}/subnet-connectivity") == 3

    def test_times_out_with_the_last_partial_result_attached(
        self, recorder: Recorder, no_sleep: list[float]
    ) -> None:
        """The clock advances by each poll's sleep, so a small budget expires."""
        recorder.add(
            "GET",
            f"{self.DIFF}/subnet-connectivity",
            *[json_response(self._partial()) for _ in range(10)],
        )
        with make_client(recorder) as client:
            with pytest.raises(ForwardTimeoutError) as caught:
                client.snapshot_diffs.wait_for_subnet_connectivity(
                    "656", "702", timeout=5, poll_interval=2
                )

        partial = getattr(caught.value, "partial", None)
        assert partial is not None and partial.is_partial_result
        assert sum(no_sleep) >= 5

    def test_counts_ask_every_area_once(self, recorder: Recorder) -> None:
        for area in ("devices", "interfaces", "topology", "l2", "acl", "nat", "arp", "mac"):
            recorder.add(
                "GET", f"{self.DIFF}/{area}", json_response({"count": 1, "complete": True})
            )
        with make_client(recorder) as client:
            counts = client.snapshot_diffs.counts("656", "702")

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
        assert all(recorder.query_for(i).get("count") == [""] for i in range(8))

    def test_an_unknown_area_is_refused_locally(self, recorder: Recorder) -> None:
        with make_client(recorder) as client:
            with pytest.raises(ValueError, match="unknown diff area"):
                client.snapshot_diffs.count("routes", "656", "702")

    def test_vulnerability_device_count_is_optional(self, recorder: Recorder) -> None:
        """Present only where vulnerability analysis is licensed."""
        recorder.add(
            "GET", f"{self.DIFF}/vulnerabilities/counts", json_response({"newCveCount": 2})
        )
        with make_client(recorder) as client:
            result = client.snapshot_diffs.vulnerability_counts("656", "702")

        assert result.new_cve_count == 2
        assert result.new_exposed_vulnerable_devices_count is None


class TestConfigValue:
    def test_reads_the_value_whatever_the_key(self) -> None:
        assert config_value(ConfigValue.model_validate({"firewall_predict": True})) is True
        assert config_value({"session_timeout": 30}) == 30
        assert config_value({}) is None
