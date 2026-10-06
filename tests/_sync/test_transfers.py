# Generated from tests/_async/test_transfers.py by scripts/unasync.py -- do not edit.
# Edit the async source and re-run: uv run python scripts/unasync.py

"""Long calls: uploads that stream, exports with progress, polls that report,
and a collection that waits for its snapshot.

These exist because a field script (ATOS, moving customer snapshots) had to work
around each of them: a multi-gigabyte archive held in memory twice, transfers
that looked hung, and a hand-written start-and-poll loop for collection.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from forward_sdk._files import CountingReader
from forward_sdk._sync.client import ForwardClient
from forward_sdk.errors import (
    ForwardConfigurationError,
    ForwardExecutionError,
    ForwardTimeoutError,
)
from tests.conftest import Recorder, json_response

pytestmark = pytest.mark.anyio

SNAPSHOTS = "/api/networks/101/snapshots"
TASKS = "/api/collector-tasks"


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


def snap(id_: str, created: str, state: str = "PROCESSED", **extra: Any) -> dict[str, Any]:
    return {"id": id_, "state": state, "createdAt": created, **extra}


class TestUploadStreams:
    def test_the_archive_is_never_read_into_memory(
        self, recorder: Recorder, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """read_bytes held a multi-gigabyte archive in memory, then httpx held the
        multipart body again. The file must be handed to httpx open instead."""
        archive = tmp_path / "snap.zip"
        archive.write_bytes(b"PK\x03\x04" + b"x" * 4096)

        def forbidden(self: Path) -> bytes:
            raise AssertionError("the archive was read whole")

        monkeypatch.setattr(Path, "read_bytes", forbidden)
        recorder.add("POST", SNAPSHOTS, json_response({"id": "12", "state": "UNPROCESSED"}, 202))
        with make_client(recorder) as client:
            client.snapshots.upload(archive)

        assert b"x" * 4096 in recorder.requests[0].content

    def test_the_file_is_closed_afterwards(
        self, recorder: Recorder, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        archive = tmp_path / "snap.zip"
        archive.write_bytes(b"PK\x03\x04data")
        closed: list[bool] = []
        real_close = CountingReader.close

        def spy(self: CountingReader) -> None:
            real_close(self)
            closed.append(True)

        monkeypatch.setattr(CountingReader, "close", spy)
        recorder.add("POST", SNAPSHOTS, json_response({"id": "12", "state": "UNPROCESSED"}, 202))
        with make_client(recorder) as client:
            client.snapshots.upload(archive, on_progress=lambda done, total: None)

        assert closed == [True]

    def test_the_file_is_closed_when_the_upload_fails(
        self, recorder: Recorder, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        archive = tmp_path / "snap.zip"
        archive.write_bytes(b"PK\x03\x04data")
        closed: list[bool] = []
        real_close = CountingReader.close

        def spy(self: CountingReader) -> None:
            real_close(self)
            closed.append(True)

        monkeypatch.setattr(CountingReader, "close", spy)
        recorder.add("POST", SNAPSHOTS, httpx.Response(400, json={"message": "bad archive"}))
        with make_client(recorder) as client:
            with pytest.raises(Exception, match="bad archive"):
                client.snapshots.upload(archive, on_progress=lambda done, total: None)

        assert closed == [True]

    def test_progress_counts_every_archive_against_the_combined_total(
        self, recorder: Recorder, tmp_path: Path
    ) -> None:
        first, second = tmp_path / "a.zip", tmp_path / "b.zip"
        first.write_bytes(b"A" * 70_000)
        second.write_bytes(b"B" * 30_000)
        seen: list[tuple[int, int | None]] = []
        recorder.add("POST", SNAPSHOTS, json_response({"id": "12", "state": "UNPROCESSED"}, 202))
        with make_client(recorder) as client:
            client.snapshots.upload(
                [first, second], on_progress=lambda done, total: seen.append((done, total))
            )

        assert seen, "no progress was reported"
        assert all(total == 100_000 for _, total in seen)
        dones = [done for done, _ in seen]
        assert dones == sorted(dones), "progress went backwards"
        assert dones[-1] == 100_000

    def test_a_progress_callback_that_raises_does_not_abort_the_upload(
        self, recorder: Recorder, tmp_path: Path
    ) -> None:
        archive = tmp_path / "snap.zip"
        archive.write_bytes(b"PK\x03\x04data")

        def broken(done: int, total: int | None) -> None:
            raise RuntimeError("a bug in a status line")

        recorder.add("POST", SNAPSHOTS, json_response({"id": "12", "state": "UNPROCESSED"}, 202))
        with make_client(recorder) as client:
            info = client.snapshots.upload(archive, on_progress=broken)

        assert info.id == "12"


class TestExport:
    def test_progress_uses_the_content_length(self, recorder: Recorder) -> None:
        recorder.add("GET", "/api/snapshots/9", httpx.Response(200, content=b"z" * 1000))
        seen: list[tuple[int, int | None]] = []
        with make_client(recorder) as client:
            chunks = [
                c
                for c in client.snapshots.export(
                    "9", on_progress=lambda done, total: seen.append((done, total))
                )
            ]

        assert b"".join(chunks) == b"z" * 1000
        assert seen[-1] == (1000, 1000)

    def test_progress_is_reported_on_receipt_not_on_the_next_request(
        self, recorder: Recorder
    ) -> None:
        recorder.add("GET", "/api/snapshots/9", httpx.Response(200, content=b"z" * 1000))
        seen: list[tuple[int, int | None]] = []
        with make_client(recorder) as client:
            # Typed Any: the iterator protocols do not declare close()/close(),
            # which a stream abandoned early needs.
            chunks: Any = client.snapshots.export(
                "9", on_progress=lambda done, total: seen.append((done, total))
            )
            first = chunks.__next__()
            chunks.close()

        assert first and seen == [(len(first), 1000)]

    def test_progress_total_is_none_without_a_length(self, recorder: Recorder) -> None:
        def body() -> Any:
            yield b"ab"
            yield b"cd"

        recorder.add("GET", "/api/snapshots/9", httpx.Response(200, content=body()))
        seen: list[tuple[int, int | None]] = []
        with make_client(recorder) as client:
            _ = [
                c
                for c in client.snapshots.export(
                    "9", on_progress=lambda done, total: seen.append((done, total))
                )
            ]

        assert seen[-1] == (4, None)

    def test_device_filters_select_the_subset_export(self, recorder: Recorder) -> None:
        recorder.add("POST", "/api/snapshots/9", httpx.Response(200, content=b"subset"))
        with make_client(recorder) as client:
            chunks = [
                c
                for c in client.snapshots.export(
                    "9", include_devices=["fw01", "pa*"], only="CONFIG"
                )
            ]

        assert b"".join(chunks) == b"subset"
        request = recorder.requests[0]
        assert request.method == "POST"
        assert request.url.params["only"] == "CONFIG"
        assert recorder.body_for() == {"includeDevices": ["fw01", "pa*"]}
        assert request.headers["accept"] == "*/*"

    def test_obfuscation_is_sent_with_its_key(self, recorder: Recorder) -> None:
        recorder.add("POST", "/api/snapshots/9", httpx.Response(200, content=b"x"))
        with make_client(recorder) as client:
            _ = [
                c for c in client.snapshots.export("9", obfuscation_key="k1", obfuscate_names=True)
            ]

        assert recorder.body_for() == {"obfuscationKey": "k1", "obfuscateNames": True}

    def test_no_filters_is_the_plain_export(self, recorder: Recorder) -> None:
        recorder.add("GET", "/api/snapshots/9", httpx.Response(200, content=b"all"))
        with make_client(recorder) as client:
            _ = [c for c in client.snapshots.export("9")]

        assert recorder.requests[0].method == "GET"

    def test_include_and_exclude_are_exclusive(self, recorder: Recorder) -> None:
        with make_client(recorder) as client:
            with pytest.raises(ForwardConfigurationError, match="exclusive"):
                _ = [
                    c
                    for c in client.snapshots.export(
                        "9", include_devices=["a"], exclude_devices=["b"]
                    )
                ]
        assert recorder.requests == []

    def test_obfuscating_names_needs_a_key(self, recorder: Recorder) -> None:
        with make_client(recorder) as client:
            with pytest.raises(ForwardConfigurationError, match="obfuscation_key"):
                _ = [c for c in client.snapshots.export("9", obfuscate_names=True)]
        assert recorder.requests == []

    def test_download_passes_the_filters_through(self, recorder: Recorder, tmp_path: Path) -> None:
        recorder.add("POST", "/api/snapshots/9", httpx.Response(200, content=b"subset"))
        target = tmp_path / "out.zip"
        with make_client(recorder) as client:
            client.snapshots.download("9", target, exclude_devices=["fw02"])

        assert target.read_bytes() == b"subset"
        assert recorder.body_for() == {"excludeDevices": ["fw02"]}


class TestWaitReports:
    def test_on_poll_sees_each_look(self, recorder: Recorder, no_sleep: list[float]) -> None:
        recorder.add(
            "GET",
            SNAPSHOTS,
            json_response({"snapshots": [snap("9", "2026-01-01T00:00:00Z", "PROCESSING")]}),
            json_response({"snapshots": [snap("9", "2026-01-01T00:00:00Z", "PROCESSED")]}),
        )
        seen: list[tuple[str, float]] = []
        with make_client(recorder) as client:
            client.snapshots.wait_until_processed(
                "9",
                poll_interval=5,
                on_poll=lambda info, elapsed: seen.append((str(info.state), elapsed)),
            )

        assert [state for state, _ in seen] == ["PROCESSING", "PROCESSED"]
        assert seen[1][1] >= seen[0][1]

    def test_a_broken_on_poll_does_not_abort_the_wait(
        self, recorder: Recorder, no_sleep: list[float]
    ) -> None:
        recorder.add(
            "GET", SNAPSHOTS, json_response({"snapshots": [snap("9", "2026-01-01T00:00:00Z")]})
        )

        def broken(info: Any, elapsed: float) -> None:
            raise RuntimeError("status line bug")

        with make_client(recorder) as client:
            done = client.snapshots.wait_until_processed("9", on_poll=broken)

        assert done.id == "9"


class TestCollect:
    OLD = snap("1", "2026-01-01T00:00:00Z")
    NEW = snap("2", "2026-01-02T00:00:00Z", processingTrigger="COLLECTION")

    def _listings(self, recorder: Recorder, *after: dict[str, Any]) -> None:
        recorder.add(
            "GET",
            SNAPSHOTS,
            json_response({"snapshots": [self.OLD]}),
            json_response({"snapshots": [*after, self.OLD]}),
        )

    def test_it_starts_waits_and_returns_the_new_snapshot(
        self, recorder: Recorder, no_sleep: list[float]
    ) -> None:
        self._listings(recorder, self.NEW)
        recorder.add("POST", TASKS, json_response({"taskId": "T1"}))
        recorder.add(
            "GET",
            f"{TASKS}/T1",
            json_response({"id": "T1", "status": "QUEUED"}),
            json_response({"id": "T1", "status": "RUNNING"}),
            json_response({"id": "T1", "status": "SUCCEEDED"}),
        )
        seen: list[str] = []
        with make_client(recorder) as client:
            run = client.snapshots.collect(
                poll_interval=10, on_poll=lambda task, elapsed: seen.append(str(task.status))
            )

        assert (run.task_id, run.status, run.snapshot_id) == ("T1", "SUCCEEDED", "2")
        assert seen == ["QUEUED", "RUNNING", "SUCCEEDED"]
        start = next(r for r in recorder.requests if r.method == "POST")
        assert start.url.params["networkId"] == "101"
        assert start.url.params["type"] == "NETWORK_COLLECTION"

    def test_a_prediction_made_meanwhile_is_not_the_collection(
        self, recorder: Recorder, no_sleep: list[float]
    ) -> None:
        predicted = snap("3", "2026-01-03T00:00:00Z", processingTrigger="PREDICT")
        self._listings(recorder, predicted, self.NEW)
        recorder.add("POST", TASKS, json_response({"taskId": "T1"}))
        recorder.add("GET", f"{TASKS}/T1", json_response({"id": "T1", "status": "SUCCEEDED"}))
        with make_client(recorder) as client:
            run = client.snapshots.collect()

        assert run.snapshot_id == "2"

    def test_without_waiting_it_returns_the_task(self, recorder: Recorder) -> None:
        recorder.add("GET", SNAPSHOTS, json_response({"snapshots": [self.OLD]}))
        recorder.add("POST", TASKS, json_response({"taskId": "T9"}))
        with make_client(recorder) as client:
            run = client.snapshots.collect(wait=False)

        assert (run.task_id, run.status, run.snapshot_id) == ("T9", "QUEUED", None)
        assert not [r for r in recorder.requests if r.url.path.startswith(f"{TASKS}/")]

    @pytest.mark.parametrize("status", ["FAILED", "TIMED_OUT", "CANCELED"])
    def test_a_task_that_does_not_succeed_raises(
        self, recorder: Recorder, no_sleep: list[float], status: str
    ) -> None:
        recorder.add("GET", SNAPSHOTS, json_response({"snapshots": [self.OLD]}))
        recorder.add("POST", TASKS, json_response({"taskId": "T1"}))
        recorder.add("GET", f"{TASKS}/T1", json_response({"id": "T1", "status": status}))
        with make_client(recorder) as client:
            with pytest.raises(ForwardExecutionError, match=status):
                client.snapshots.collect()

    def test_it_gives_up_waiting_but_not_on_the_collection(
        self, recorder: Recorder, no_sleep: list[float]
    ) -> None:
        recorder.add("GET", SNAPSHOTS, json_response({"snapshots": [self.OLD]}))
        recorder.add("POST", TASKS, json_response({"taskId": "T1"}))
        recorder.add("GET", f"{TASKS}/T1", json_response({"id": "T1", "status": "RUNNING"}))
        with make_client(recorder) as client:
            with pytest.raises(ForwardTimeoutError, match="continues on Forward"):
                client.snapshots.collect(timeout=30, poll_interval=10)

    def test_success_with_no_new_snapshot_is_an_error_not_none(
        self, recorder: Recorder, no_sleep: list[float]
    ) -> None:
        recorder.add("GET", SNAPSHOTS, json_response({"snapshots": [self.OLD]}))
        recorder.add("POST", TASKS, json_response({"taskId": "T1"}))
        recorder.add("GET", f"{TASKS}/T1", json_response({"id": "T1", "status": "SUCCEEDED"}))
        with make_client(recorder) as client:
            with pytest.raises(ForwardExecutionError, match="no new snapshot") as caught:
                client.snapshots.collect(snapshot_timeout=20)

        assert "T1" in str(caught.value)

    def test_a_start_with_no_task_id_is_an_error(self, recorder: Recorder) -> None:
        recorder.add("GET", SNAPSHOTS, json_response({"snapshots": [self.OLD]}))
        recorder.add("POST", TASKS, json_response({}))
        with make_client(recorder) as client:
            with pytest.raises(ForwardExecutionError, match="no task id"):
                client.snapshots.collect()
