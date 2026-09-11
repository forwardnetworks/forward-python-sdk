# Generated from tests/_async/test_core_services.py by scripts/unasync.py -- do not edit.
# Edit the async source and re-run: uv run python scripts/unasync.py

"""Networks, snapshots, devices and device tags."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from forward_sdk._sync.client import ForwardClient
from forward_sdk.errors import (
    ForwardError,
    ForwardExecutionError,
    ForwardNotFoundError,
    ForwardResponseError,
    ForwardTimeoutError,
)
from tests.conftest import Recorder, json_response

pytestmark = pytest.mark.anyio

SNAPSHOTS = "/api/networks/101/snapshots"
DEVICES = "/api/networks/101/devices"


def make_client(recorder: Recorder, **overrides: Any) -> ForwardClient:
    settings: dict[str, Any] = {
        "username": "key",
        "password": "secret",
        "rate_limit_rpm": None,
        "network_id": "101",
        "transport": recorder.transport,
    }
    settings.update(overrides)
    return ForwardClient("https://forward.test", **settings)


def snapshot(id_: str, state: str = "PROCESSED", **extra: Any) -> dict[str, Any]:
    payload = {"id": id_, "state": state, "createdAt": "2026-01-01T00:00:00.000Z"}
    payload.setdefault("processedAt", "2026-01-01T00:00:00.000Z")
    payload.update(extra)
    return payload


class TestNetworks:
    def test_list_parses_networks(self, recorder: Recorder) -> None:
        recorder.add(
            "GET",
            "/api/networks",
            json_response([{"id": "101", "name": "Prod", "orgId": "7"}]),
        )
        with make_client(recorder) as client:
            networks = client.networks.list()

        assert networks[0].id == "101"
        assert networks[0].name == "Prod"
        assert networks[0].org_id == "7"

    def test_get_filters_the_listing(self, recorder: Recorder) -> None:
        """Forward has no single-network endpoint, so this reads the list."""
        recorder.add(
            "GET",
            "/api/networks",
            json_response(
                [{"id": "1", "name": "a", "orgId": "7"}, {"id": "2", "name": "b", "orgId": "7"}]
            ),
        )
        with make_client(recorder) as client:
            assert (client.networks.get("2")).name == "b"

    def test_get_missing_network_raises(self, recorder: Recorder) -> None:
        recorder.add("GET", "/api/networks", json_response([]))
        with make_client(recorder) as client:
            with pytest.raises(ForwardNotFoundError, match="no network with id"):
                client.networks.get("999")

    def test_create_sends_the_name_as_a_query_parameter(self, recorder: Recorder) -> None:
        """Forward's create endpoint takes a name in the query and no body."""
        recorder.add(
            "POST", "/api/networks", json_response({"id": "5", "name": "New", "orgId": "7"})
        )
        with make_client(recorder) as client:
            network = client.networks.create("New")

        assert network.id == "5"
        assert recorder.query_for()["name"] == ["New"]
        assert recorder.body_for() is None

    def test_create_applies_a_note_as_a_follow_up_update(self, recorder: Recorder) -> None:
        """The create endpoint accepts no note, so it is set with a second call."""
        recorder.add(
            "POST", "/api/networks", json_response({"id": "5", "name": "New", "orgId": "7"})
        )
        recorder.add(
            "PATCH",
            "/api/networks/5",
            json_response({"id": "5", "name": "New", "orgId": "7", "note": "from the SDK"}),
        )
        with make_client(recorder) as client:
            network = client.networks.create("New", note="from the SDK")

        assert network.note == "from the SDK"
        assert recorder.body_for() == {"note": "from the SDK"}

    def test_version(self, recorder: Recorder) -> None:
        recorder.add("GET", "/api/version", json_response({"version": "26.4.1", "build": "abc"}))
        with make_client(recorder) as client:
            assert (client.version())["version"] == "26.4.1"


class TestSnapshots:
    def test_list_reads_the_snapshots_field(self, recorder: Recorder) -> None:
        recorder.add("GET", SNAPSHOTS, json_response({"id": "101", "snapshots": [snapshot("9")]}))
        with make_client(recorder) as client:
            snapshots = client.snapshots.list()

        assert snapshots[0].id == "9"
        assert str(snapshots[0].state) == "PROCESSED"

    def test_latest_processed_uses_the_listing(self, recorder: Recorder) -> None:
        """Forward's dedicated latestProcessed endpoint is deprecated."""
        recorder.add("GET", SNAPSHOTS, json_response({"snapshots": [snapshot("9")]}))
        with make_client(recorder) as client:
            latest = client.snapshots.latest_processed()

        assert latest is not None and latest.id == "9"
        assert recorder.query_for()["state"] == ["PROCESSED"]
        assert recorder.paths == [SNAPSHOTS]

    def test_latest_processed_does_not_trust_the_listing_order(self, recorder: Recorder) -> None:
        """The listing's order is not documented, so the newest is chosen here.

        Taking the first row would pin a sync to an arbitrary processed
        snapshot, and nothing about the result would look wrong.
        """
        recorder.add(
            "GET",
            SNAPSHOTS,
            json_response(
                {
                    "snapshots": [
                        snapshot("7", processedAt="2026-01-01T00:00:00.000Z"),
                        snapshot("9", processedAt="2026-03-01T00:00:00.000Z"),
                        snapshot("8", processedAt="2026-02-01T00:00:00.000Z"),
                    ]
                }
            ),
        )
        with make_client(recorder) as client:
            latest = client.snapshots.latest_processed()

        assert latest is not None and latest.id == "9"

    def test_archived_flag_is_always_sent(self, recorder: Recorder) -> None:
        """The argument must reach the wire, not rely on a server default."""
        recorder.add("GET", SNAPSHOTS, json_response({"snapshots": []}))
        with make_client(recorder) as client:
            client.snapshots.list(include_archived=False)

        assert recorder.query_for()["includeArchived"] == ["false"]

    def test_latest_processed_returns_none_when_empty(self, recorder: Recorder) -> None:
        recorder.add("GET", SNAPSHOTS, json_response({"snapshots": []}))
        with make_client(recorder) as client:
            assert client.snapshots.latest_processed() is None

    def test_metrics_and_completeness(self, recorder: Recorder) -> None:
        recorder.add(
            "GET",
            "/api/snapshots/9/metrics",
            json_response({"numCollectionFailureDevices": 0, "numProcessingFailureDevices": 0}),
        )
        with make_client(recorder) as client:
            assert client.snapshots.is_complete("9") is True

    def test_completeness_detects_failures(self, recorder: Recorder) -> None:
        recorder.add(
            "GET",
            "/api/snapshots/9/metrics",
            json_response({"numCollectionFailureDevices": 3}),
        )
        with make_client(recorder) as client:
            assert client.snapshots.is_complete("9") is False

    def test_wait_until_processed_polls(self, recorder: Recorder, no_sleep: list[float]) -> None:
        recorder.add(
            "GET",
            SNAPSHOTS,
            json_response({"snapshots": [snapshot("9", "PROCESSING")]}),
            json_response({"snapshots": [snapshot("9", "PROCESSED")]}),
        )
        with make_client(recorder) as client:
            result = client.snapshots.wait_until_processed("9")

        assert str(result.state) == "PROCESSED"
        assert recorder.count("GET", SNAPSHOTS) == 2

    def test_wait_reports_a_failed_snapshot(
        self, recorder: Recorder, no_sleep: list[float]
    ) -> None:
        recorder.add("GET", SNAPSHOTS, json_response({"snapshots": [snapshot("9", "FAILED")]}))
        with make_client(recorder) as client:
            with pytest.raises(ForwardExecutionError, match="ended in state FAILED"):
                client.snapshots.wait_until_processed("9")

    def test_wait_times_out_without_cancelling_processing(
        self, recorder: Recorder, no_sleep: list[float]
    ) -> None:
        recorder.add("GET", SNAPSHOTS, json_response({"snapshots": [snapshot("9", "PROCESSING")]}))
        with make_client(recorder) as client:
            with pytest.raises(ForwardTimeoutError, match="processing continues"):
                client.snapshots.wait_until_processed("9", timeout=0)

    def test_wait_on_unknown_snapshot_raises(
        self, recorder: Recorder, no_sleep: list[float]
    ) -> None:
        recorder.add("GET", SNAPSHOTS, json_response({"snapshots": []}))
        with make_client(recorder) as client:
            with pytest.raises(ForwardNotFoundError, match="not in network"):
                client.snapshots.wait_until_processed("9")

    def test_upload_sends_multipart_and_defaults_to_async(
        self, recorder: Recorder, tmp_path: Path
    ) -> None:
        archive = tmp_path / "snap.zip"
        archive.write_bytes(b"PK\x03\x04payload")
        recorder.add("POST", SNAPSHOTS, json_response(snapshot("12", "UNPROCESSED"), status=202))

        with make_client(recorder) as client:
            info = client.snapshots.upload(archive, note="nightly")

        assert info.id == "12"
        request = recorder.requests[0]
        assert request.headers["content-type"].startswith("multipart/form-data")
        body = request.content.decode("latin-1")
        assert "snap.zip" in body
        assert "nightly" in body
        # Forward calls this parameter "async"; true means it returns immediately.
        assert 'name="async"\r\n\r\ntrue' in body
        assert 'name="process"\r\n\r\ntrue' in body

    def test_upload_reports_a_missing_file_before_sending(
        self, recorder: Recorder, tmp_path: Path
    ) -> None:
        with make_client(recorder) as client:
            with pytest.raises(FileNotFoundError, match="not found"):
                client.snapshots.upload(tmp_path / "absent.zip")

        assert recorder.requests == []

    def test_latest_collected_skips_empty_snapshots(self, recorder: Recorder) -> None:
        """A processed snapshot can still hold no collected devices."""
        recorder.add(
            "GET",
            SNAPSHOTS,
            json_response({"snapshots": [snapshot("9"), snapshot("8")]}),
        )
        recorder.add(
            "POST",
            "/api/nqe",
            json_response({"items": [], "totalNumItems": 0}),
            json_response({"items": [{"name": "sw1"}], "totalNumItems": 1}),
        )
        with make_client(recorder) as client:
            assert client.snapshots.latest_collected_id() == "8"

        assert recorder.count("POST", "/api/nqe") == 2


class TestPredictedSnapshots:
    """Forward processes a snapshot for every Predict run, like any other.

    On a network using Predict the newest processed snapshot is very often a
    prediction rather than a state the network was ever in, so basing a change
    set on it predicts a change against a change.
    """

    def _snap(self, sid: str, trigger: str, minute: int) -> dict[str, Any]:
        return snapshot(
            sid,
            processingTrigger=trigger,
            processedAt=f"2026-01-01T00:{minute:02d}:00.000Z",
        )

    def test_a_prediction_is_not_the_latest_processed(self, recorder: Recorder) -> None:
        recorder.add(
            "GET",
            SNAPSHOTS,
            json_response(
                {
                    "snapshots": [
                        self._snap("predicted", "PREDICT", 20),
                        self._snap("real", "COLLECTION", 10),
                    ]
                }
            ),
        )
        with make_client(recorder) as client:
            latest = client.snapshots.latest_processed()

        assert latest is not None and latest.id == "real"

    def test_a_reprocessed_snapshot_still_counts(self, recorder: Recorder) -> None:
        """The reason the filter excludes PREDICT rather than requiring COLLECTION.

        Reprocessing is how a changed query or feature flag is picked up, and
        the result is real collected data. Requiring COLLECTION would skip it
        and select the previous collection, which during a rehearsal is the
        snapshot taken while the change was still applied.
        """
        recorder.add(
            "GET",
            SNAPSHOTS,
            json_response(
                {
                    "snapshots": [
                        self._snap("reprocessed", "REPROCESS", 30),
                        self._snap("collected", "COLLECTION", 10),
                    ]
                }
            ),
        )
        with make_client(recorder) as client:
            latest = client.snapshots.latest_processed()

        assert latest is not None and latest.id == "reprocessed"

    def test_predictions_can_be_asked_for(self, recorder: Recorder) -> None:
        recorder.add(
            "GET",
            SNAPSHOTS,
            json_response(
                {
                    "snapshots": [
                        self._snap("predicted", "PREDICT", 20),
                        self._snap("real", "COLLECTION", 10),
                    ]
                }
            ),
        )
        with make_client(recorder) as client:
            latest = client.snapshots.latest_processed(include_predicted=True)

        assert latest is not None and latest.id == "predicted"

    def test_a_snapshot_with_no_trigger_is_kept(self, recorder: Recorder) -> None:
        """An older Forward, or a listing that omits the field, must still work."""
        recorder.add(
            "GET",
            SNAPSHOTS,
            json_response({"snapshots": [{"id": "9", "state": "PROCESSED"}]}),
        )
        with make_client(recorder) as client:
            latest = client.snapshots.latest_processed()

        assert latest is not None and latest.id == "9"

    def test_the_two_answers_are_cached_apart(self, recorder: Recorder) -> None:
        for _ in range(2):
            recorder.add(
                "GET",
                SNAPSHOTS,
                json_response(
                    {
                        "snapshots": [
                            self._snap("predicted", "PREDICT", 20),
                            self._snap("real", "COLLECTION", 10),
                        ]
                    }
                ),
            )
        with make_client(recorder, snapshot_cache_ttl="lifetime") as client:
            without = client.snapshots.latest_processed()
            with_predicted = client.snapshots.latest_processed(include_predicted=True)

        assert without is not None and without.id == "real"
        assert with_predicted is not None and with_predicted.id == "predicted"


class TestUnparseableResponses:
    def test_a_bad_payload_raises_a_catchable_error(self, recorder: Recorder) -> None:
        """The path an integration actually hit, end to end through the client.

        A caller wrapping SDK calls in `except ForwardError` has to see this, or
        a sync dies with a pydantic traceback in a job log instead of a failure
        its own handling could classify.
        """
        recorder.add("GET", "/api/networks", json_response([{"id": "n1", "name": "Prod"}]))
        with make_client(recorder) as client:
            with pytest.raises(ForwardError) as caught:
                client.networks.list()

        assert isinstance(caught.value, ForwardResponseError)
        assert caught.value.payload == {"id": "n1", "name": "Prod"}


class TestSnapshotResolutionCache:
    """Opt-in, because only the caller knows how long "latest" stays true.

    It exists for the rate limit rather than for latency: Forward's budget is
    per authenticated user, spent by every process holding those credentials,
    and exceeding it blocks the user rather than slowing them. A sync that
    resolves a snapshot once per slice across a thread pool spends a large share
    of that allowance on a question with one answer.

    Off by default because a stale snapshot is the worst kind of wrong. It does
    not fail, it returns real data from the wrong moment.
    """

    def test_off_by_default(self, recorder: Recorder) -> None:
        recorder.add("GET", SNAPSHOTS, json_response({"snapshots": [snapshot("9")]}))
        recorder.add("GET", SNAPSHOTS, json_response({"snapshots": [snapshot("9")]}))
        with make_client(recorder) as client:
            client.snapshots.latest_processed()
            client.snapshots.latest_processed()

        assert recorder.count("GET", SNAPSHOTS) == 2

    def test_reuses_a_resolution_within_the_ttl(self, recorder: Recorder) -> None:
        recorder.add("GET", SNAPSHOTS, json_response({"snapshots": [snapshot("9")]}))
        with make_client(recorder, snapshot_cache_ttl=300) as client:
            first = client.snapshots.latest_processed()
            second = client.snapshots.latest_processed()
            counters = client.counters

        assert first is not None and second is not None
        assert first.id == second.id == "9"
        assert recorder.count("GET", SNAPSHOTS) == 1
        assert counters.cache_hits == 1

    def test_a_network_with_no_snapshot_is_a_real_answer(self, recorder: Recorder) -> None:
        """Caching None matters: re-asking spends the budget the cache saves."""
        recorder.add("GET", SNAPSHOTS, json_response({"snapshots": []}))
        with make_client(recorder, snapshot_cache_ttl=300) as client:
            assert client.snapshots.latest_processed() is None
            assert client.snapshots.latest_processed() is None

        assert recorder.count("GET", SNAPSHOTS) == 1

    def test_different_networks_do_not_share_an_answer(self, recorder: Recorder) -> None:
        recorder.add("GET", SNAPSHOTS, json_response({"snapshots": [snapshot("9")]}))
        recorder.add(
            "GET",
            "/api/networks/202/snapshots",
            json_response({"snapshots": [snapshot("7")]}),
        )
        with make_client(recorder, snapshot_cache_ttl=300) as client:
            first = client.snapshots.latest_processed()
            second = client.snapshots.latest_processed("202")

        assert first is not None and first.id == "9"
        assert second is not None and second.id == "7"

    def test_a_different_scope_is_a_different_question(self, recorder: Recorder) -> None:
        """The collected probe depends on its scope, so the key must carry it."""
        for _ in range(2):
            recorder.add("GET", SNAPSHOTS, json_response({"snapshots": [snapshot("9")]}))
            recorder.add("POST", "/api/nqe", json_response({"items": [{"n": "x"}]}))
        with make_client(recorder, snapshot_cache_ttl=300) as client:
            client.snapshots.latest_collected_id(include_tags=["core"])
            client.snapshots.latest_collected_id(include_tags=["edge"])

        assert recorder.count("POST", "/api/nqe") == 2

    def test_expires_after_the_ttl(
        self, recorder: Recorder, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        now = [1000.0]
        monkeypatch.setattr("forward_sdk._sync.services.snapshots.time.monotonic", lambda: now[0])
        recorder.add("GET", SNAPSHOTS, json_response({"snapshots": [snapshot("9")]}))
        recorder.add("GET", SNAPSHOTS, json_response({"snapshots": [snapshot("9")]}))
        with make_client(recorder, snapshot_cache_ttl=60) as client:
            client.snapshots.latest_processed()
            now[0] += 61
            client.snapshots.latest_processed()

        assert recorder.count("GET", SNAPSHOTS) == 2

    def test_uploading_invalidates_it(self, recorder: Recorder, tmp_path: Path) -> None:
        """The upload is the event that makes a cached answer wrong."""
        archive = tmp_path / "snap.zip"
        archive.write_bytes(b"zip")
        recorder.add("GET", SNAPSHOTS, json_response({"snapshots": [snapshot("9")]}))
        recorder.add("POST", SNAPSHOTS, json_response(snapshot("10")))
        recorder.add("GET", SNAPSHOTS, json_response({"snapshots": [snapshot("10")]}))
        with make_client(recorder, snapshot_cache_ttl=300) as client:
            before = client.snapshots.latest_processed()
            client.snapshots.upload([str(archive)])
            after = client.snapshots.latest_processed()

        assert before is not None and before.id == "9"
        assert after is not None and after.id == "10"

    def test_lifetime_never_expires(
        self, recorder: Recorder, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A run holding one point in time must not have it change underneath.

        A TTL is the wrong shape for that: expiring mid-run lets the next
        resolution return a newer snapshot, so the run straddles two moments
        with nothing raising and the data real on both sides. "lifetime" is the
        shape that matches the invariant a sync actually relies on, which is
        that it pinned one snapshot and snapshots are immutable.
        """
        now = [1000.0]
        monkeypatch.setattr("forward_sdk._sync.services.snapshots.time.monotonic", lambda: now[0])
        recorder.add("GET", SNAPSHOTS, json_response({"snapshots": [snapshot("9")]}))
        with make_client(recorder, snapshot_cache_ttl="lifetime") as client:
            first = client.snapshots.latest_processed()
            now[0] += 86_400
            second = client.snapshots.latest_processed()

        assert first is not None and second is not None
        assert first.id == second.id == "9"
        assert recorder.count("GET", SNAPSHOTS) == 1

    def test_lifetime_still_yields_to_an_upload(self, recorder: Recorder, tmp_path: Path) -> None:
        """Never expiring is not the same as never being wrong.

        The client that uploaded a snapshot knows the answer changed, so
        "lifetime" means "until something makes it wrong", not "forever".
        """
        archive = tmp_path / "snap.zip"
        archive.write_bytes(b"zip")
        recorder.add("GET", SNAPSHOTS, json_response({"snapshots": [snapshot("9")]}))
        recorder.add("POST", SNAPSHOTS, json_response(snapshot("10")))
        recorder.add("GET", SNAPSHOTS, json_response({"snapshots": [snapshot("10")]}))
        with make_client(recorder, snapshot_cache_ttl="lifetime") as client:
            client.snapshots.latest_processed()
            client.snapshots.upload([str(archive)])
            after = client.snapshots.latest_processed()

        assert after is not None and after.id == "10"

    def test_clear_cache_forces_a_fresh_resolution(self, recorder: Recorder) -> None:
        recorder.add("GET", SNAPSHOTS, json_response({"snapshots": [snapshot("9")]}))
        recorder.add("GET", SNAPSHOTS, json_response({"snapshots": [snapshot("11")]}))
        with make_client(recorder, snapshot_cache_ttl=300) as client:
            client.snapshots.latest_processed()
            client.snapshots.clear_cache()
            latest = client.snapshots.latest_processed()

        assert latest is not None and latest.id == "11"


class TestSnapshotsContinued:
    def test_latest_collected_applies_tag_scope(self, recorder: Recorder) -> None:
        recorder.add("GET", SNAPSHOTS, json_response({"snapshots": [snapshot("9")]}))
        recorder.add("POST", "/api/nqe", json_response({"items": [{"name": "x"}]}))
        with make_client(recorder) as client:
            client.snapshots.latest_collected_id(include_tags=["core"])

        query = recorder.body_for()["query"]
        # NQE's membership operator is `in`, with the element on the left.
        assert '"core" in device.tagNames' in query

    def test_latest_collected_reports_when_nothing_qualifies(self, recorder: Recorder) -> None:
        recorder.add("GET", SNAPSHOTS, json_response({"snapshots": [snapshot("9")]}))
        recorder.add("POST", "/api/nqe", json_response({"items": []}))
        with make_client(recorder) as client:
            with pytest.raises(ForwardNotFoundError, match="devices in scope"):
                client.snapshots.latest_collected_id()

    def test_export_streams_chunks(self, recorder: Recorder) -> None:
        recorder.add("GET", "/api/snapshots/9", httpx.Response(200, content=b"zipdata"))
        with make_client(recorder) as client:
            chunks = [c for c in client.snapshots.export("9", only="CONFIG")]

        assert b"".join(chunks) == b"zipdata"
        assert recorder.query_for()["only"] == ["CONFIG"]

    def test_download_writes_a_file(self, recorder: Recorder, tmp_path: Path) -> None:
        recorder.add("GET", "/api/snapshots/9", httpx.Response(200, content=b"zipdata"))
        target = tmp_path / "snapshot.zip"
        with make_client(recorder) as client:
            written = client.snapshots.download("9", target)

        assert written.read_bytes() == b"zipdata"

    def test_reachability_job_polls_to_completion(
        self, recorder: Recorder, no_sleep: list[float]
    ) -> None:
        recorder.add(
            "POST",
            "/api/networks/101/snapshots/9/reachability",
            json_response({"jobKey": "job-1", "status": "RUNNING"}),
        )
        recorder.add(
            "GET",
            "/api/networks/101/snapshots/9/reachability/job-1",
            json_response({"status": "RUNNING"}),
            json_response({"status": "COMPLETED"}),
        )
        with make_client(recorder) as client:
            job = client.snapshots.start_reachability_job("9")
            status = job.wait()

        assert status["status"] == "COMPLETED"


class TestDevices:
    def test_list_parses_devices(self, recorder: Recorder) -> None:
        recorder.add(
            "GET",
            DEVICES,
            json_response([{"name": "sw1", "vendor": "CISCO", "model": "C9300"}]),
        )
        with make_client(recorder) as client:
            devices = client.devices.list()

        assert devices[0].name == "sw1"
        assert str(devices[0].vendor) == "CISCO"

    def test_optional_detail_must_be_requested(self, recorder: Recorder) -> None:
        """Tags and location are absent unless asked for; absence is not emptiness."""
        recorder.add("GET", DEVICES, json_response([{"name": "sw1"}]))
        with make_client(recorder) as client:
            devices = client.devices.list(with_=["tags", "locationId"])

        assert devices[0].tags is None
        assert recorder.query_for()["with"] == ["tags", "locationId"]

    def test_filters_are_passed_through(self, recorder: Recorder) -> None:
        recorder.add("GET", DEVICES, json_response([]))
        with make_client(recorder) as client:
            client.devices.list(vendor="CISCO", model="C9300")

        query = recorder.query_for()
        assert query["vendor"] == ["CISCO"]
        assert query["model"] == ["C9300"]

    def test_iter_pages_until_short_page(self, recorder: Recorder) -> None:
        recorder.add(
            "GET",
            DEVICES,
            json_response([{"name": "a"}, {"name": "b"}]),
            json_response([{"name": "c"}]),
        )
        with make_client(recorder) as client:
            names = [d.name for d in client.devices.iter(page_size=2)]

        assert names == ["a", "b", "c"]
        assert recorder.query_for()["skip"] == ["2"]

    def test_device_name_with_slash_is_quoted(self, recorder: Recorder) -> None:
        recorder.add(
            "GET", "/api/networks/101/devices/nyc%2Ffw01", json_response({"name": "nyc/fw01"})
        )
        with make_client(recorder) as client:
            device = client.devices.get("nyc/fw01")

        assert device.name == "nyc/fw01"

    def test_file_returns_text(self, recorder: Recorder) -> None:
        recorder.add(
            "GET",
            "/api/networks/101/devices/sw1/files/running-config",
            httpx.Response(200, text="hostname sw1\n"),
        )
        with make_client(recorder) as client:
            content = client.devices.file("sw1", "running-config")

        assert content == "hostname sw1\n"


class TestDeviceTags:
    def test_list_tags(self, recorder: Recorder) -> None:
        recorder.add("GET", "/api/networks/101/device-tags", json_response([{"name": "core"}]))
        with make_client(recorder) as client:
            tags = client.device_tags.list()

        assert tags[0]["name"] == "core"

    def test_list_with_devices_uses_the_dispatch_parameter(self, recorder: Recorder) -> None:
        recorder.add("GET", "/api/networks/101/device-tags", json_response([]))
        with make_client(recorder) as client:
            client.device_tags.list(with_devices=True)

        assert recorder.query_for()["with"] == ["devices"]

    def test_add_tag_to_devices(self, recorder: Recorder) -> None:
        recorder.add("POST", "/api/networks/101/device-tags/core", json_response({}))
        with make_client(recorder) as client:
            client.device_tags.add_to_devices("core", ["sw1", "sw2"])

        assert recorder.query_for()["action"] == ["addTo"]
        assert recorder.body_for() == ["sw1", "sw2"]

    def test_remove_tag_from_devices(self, recorder: Recorder) -> None:
        recorder.add("POST", "/api/networks/101/device-tags/core", json_response({}))
        with make_client(recorder) as client:
            client.device_tags.remove_from_devices("core", ["sw1"])

        assert recorder.query_for()["action"] == ["removeFrom"]
