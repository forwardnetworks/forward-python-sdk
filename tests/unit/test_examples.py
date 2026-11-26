"""Each script in ``examples/`` runs, and does what ``examples/README.md`` says.

An example is the first code a new user copies, so a rename or a changed return
shape must fail here rather than in their terminal. The scripts run unchanged
against a scripted Forward: the real client reads its settings from the
environment, builds the requests and parses the answers. Only the network is
replaced.
"""

from __future__ import annotations

import asyncio
import csv
import importlib.util
import json
import os
import re
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
import pytest

from forward_sdk import AsyncForwardClient, ForwardClient
from tests.conftest import Recorder, error_response, json_response, ndjson_response

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"

NETWORKS = "/api/networks"
SNAPSHOTS = "/api/networks/101/snapshots"
DEVICES = "/api/networks/101/devices"
COMMITS = "/api/nqe/repos/org/commits"
EXECUTIONS = "/api/networks/101/nqe-executions"

PROCESSED = {
    "id": "900",
    "state": "PROCESSED",
    "createdAt": "2026-01-02T00:00:00Z",
    "processedAt": "2026-01-02T01:00:00Z",
}


@pytest.fixture
def forward(recorder: Recorder, monkeypatch: pytest.MonkeyPatch) -> Recorder:
    """Point ``from_env`` at the recorder, with the settings the README lists."""
    for name in [name for name in os.environ if name.startswith("FORWARD_")]:
        monkeypatch.delenv(name)  # A developer's own settings must not reach the examples.
    monkeypatch.setenv("FORWARD_URL", "https://forward.test")
    monkeypatch.setenv("FORWARD_USERNAME", "access-key")
    monkeypatch.setenv("FORWARD_PASSWORD", "secret")
    monkeypatch.setenv("FORWARD_NETWORK_ID", "101")
    monkeypatch.setenv("FORWARD_RATE_LIMIT_RPM", "0")
    for client in (ForwardClient, AsyncForwardClient):
        real = client.__dict__["from_env"].__func__

        def from_env(cls: type[Any], /, *args: Any, _real: Any = real, **overrides: Any) -> Any:
            return _real(cls, *args, transport=recorder.transport, **overrides)

        monkeypatch.setattr(client, "from_env", classmethod(from_env))
    return recorder


def load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"example_{name}", EXAMPLES / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def network(network_id: str, name: str) -> dict[str, str]:
    return {"id": network_id, "name": name, "orgId": "1"}


def background_query(request: httpx.Request, key: str, rows: list[Any]) -> httpx.Response:
    """Forward's three answers for a background query: submitted, finished, one result page."""
    if request.method == "POST":
        return json_response({"executionKey": key}, 201)
    if request.url.path.endswith("/result"):
        return json_response({"items": rows, "totalNumItems": len(rows)})
    return json_response({"status": "COMPLETED", "outcome": "OK", "rowsProduced": len(rows)})


def test_the_readme_lists_exactly_the_examples_and_each_is_tested_here() -> None:
    scripts = {path.name for path in EXAMPLES.glob("*.py")}
    readme = (EXAMPLES / "README.md").read_text(encoding="utf-8")
    assert set(re.findall(r"^\| `(\w+\.py)` \|", readme, re.MULTILINE)) == scripts
    tested = set(re.findall(r'load\("(\w+)"\)', Path(__file__).read_text(encoding="utf-8")))
    assert {f"{name}.py" for name in tested} == scripts


def test_list_networks_shows_each_network_with_its_latest_processed_snapshot(
    forward: Recorder, capsys: pytest.CaptureFixture[str]
) -> None:
    forward.add("GET", "/api/version", json_response({"version": "26.1.0"}))
    forward.add(
        "GET",
        NETWORKS,
        json_response([network("101", "Core"), network("102", "Lab")]),
    )
    forward.add("GET", SNAPSHOTS, json_response({"snapshots": [PROCESSED]}))
    forward.add("GET", "/api/networks/102/snapshots", json_response({"snapshots": []}))

    load("list_networks").main()

    out = capsys.readouterr().out
    assert "Connected to Forward 26.1.0" in out
    assert re.search(r"^Core \(101\): snapshot 900, processed 2026-01-02", out, re.MULTILINE)
    assert "Lab (102): no processed snapshot" in out


def test_run_query_prints_the_count_and_the_first_twenty_rows(
    forward: Recorder, capsys: pytest.CaptureFixture[str]
) -> None:
    rows = [{"name": f"sw{n:02}", "vendor": "CISCO", "os": f"17.{n}"} for n in range(25)]
    forward.default = lambda request: background_query(request, "e1", rows)

    load("run_query").main()

    out = capsys.readouterr().out
    assert "25 devices" in out
    assert re.search(r"sw19\s+CISCO\s+17\.19", out)
    assert "sw20" not in out
    assert f"requests: {len(forward.requests)}" in out
    assert [r.url.path for r in forward.requests if r.method == "POST"] == [EXECUTIONS]
    assert "snapshotId" not in forward.query_for(0)


def test_async_client_runs_both_queries_and_counts_what_it_did(
    forward: Recorder, capsys: pytest.CaptureFixture[str]
) -> None:
    def answer(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == NETWORKS:
            return json_response([network("101", "Core")])
        assert path.startswith(EXECUTIONS)
        if request.method == "POST":
            asked = json.loads(request.content)["query"]
            key = "interfaces" if "foreach i in d.interfaces" in asked else "devices"
            return background_query(request, key, [])
        key = path.removeprefix(f"{EXECUTIONS}/").split("/")[0]
        count = {"devices": 2, "interfaces": 3}[key]
        return background_query(request, key, [{"n": n} for n in range(count)])

    forward.default = answer

    asyncio.run(load("async_client").main())

    out = capsys.readouterr().out
    assert re.search(r"networks:\s+1\n", out)
    assert re.search(r"devices:\s+2\n", out)
    assert re.search(r"interfaces:\s+3\n", out)
    assert f"{len(forward.requests)} requests, 5 rows" in out
    assert sum(r.method == "POST" for r in forward.requests) == 2


def test_device_inventory_writes_every_page_to_the_csv(
    forward: Recorder, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    full = {
        "type": "SWITCH",
        "vendor": "CISCO",
        "model": "C9300",
        "platform": "cisco_ios_xe",
        "osVersion": "17.1",
    }
    first = [{"name": f"sw{n}", **full} for n in range(500)]
    forward.add("GET", DEVICES, json_response(first), json_response([{"name": "last"}]))
    destination = tmp_path / "inventory.csv"

    load("device_inventory").main(str(destination))

    with destination.open(newline="") as handle:
        table = list(csv.reader(handle))
    assert table[0] == ["name", "type", "vendor", "model", "platform", "os_version"]
    assert len(table) == 502
    assert table[1] == ["sw0", "SWITCH", "CISCO", "C9300", "cisco_ios_xe", "17.1"]
    # An attribute Forward did not send is an empty cell, not the word "None".
    assert table[-1] == ["last", "", "", "", "", ""]
    assert [forward.query_for(i)["limit"] for i in range(2)] == [["500"], ["500"]]
    assert forward.query_for(1)["skip"] == ["500"]
    assert f"wrote 501 devices to {destination}" in capsys.readouterr().out


def test_stream_large_query_waits_then_tallies_the_streamed_rows(
    forward: Recorder, no_sleep: list[float], capsys: pytest.CaptureFixture[str]
) -> None:
    completed = {"status": "COMPLETED", "outcome": "OK", "rowsProduced": 3}
    statuses = [{"status": "EXECUTING"}, completed]
    rows = [{"status": "UP"}, {"status": "DOWN"}, {"status": "UP"}]

    def answer(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            assert request.url.path == EXECUTIONS
            return json_response({"executionKey": "e1"}, 201)
        if request.url.path == f"{EXECUTIONS}/e1":
            return json_response(statuses.pop(0))
        assert request.url.path == f"{EXECUTIONS}/e1/result"
        # As Forward does, give no rows until the execution has finished.
        assert not statuses, "the result was read before the query finished"
        return ndjson_response(rows)

    forward.default = answer

    load("stream_large_query").main()

    out = capsys.readouterr().out
    assert "execution e1 started" in out
    assert "3 interfaces" in out
    assert re.search(r"UP\s+2\n\s+DOWN\s+1\n", out)


def test_publish_queries_dry_runs_then_commits_the_files_under_the_library_directory(
    forward: Recorder, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "devices.nqe").write_text(
        "foreach d in network.devices select {name: d.name}\n", encoding="utf-8"
    )

    def answer(request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        if path == SNAPSHOTS:
            return json_response({"snapshots": [PROCESSED]})
        if path == "/api/users/current/nqe/changes":
            return json_response({"changes": []} if method == "GET" else {})
        if path.endswith("/commits/head/queries"):
            return json_response({"queries": []})
        if path == COMMITS and method == "POST":
            return json_response({})
        if path == "/api/nqe/repos/org/commits/head":
            return json_response({"id": "c2"})
        raise AssertionError(f"unexpected request: {method} {request.url}")

    forward.default = answer

    load("publish_queries").main(str(tmp_path))

    assert "published /MyOrg/devices" in capsys.readouterr().out
    commits = [r for r in forward.requests if r.url.path == COMMITS and r.method == "POST"]
    # First a dry run against the latest processed snapshot, then the one real commit.
    assert [dict(r.url.params) for r in commits] == [{"dryRun": "true", "snapshotId": "900"}, {}]
    staged = {
        r.url.params["action"]: r
        for r in forward.requests
        if r.url.path == "/api/users/current/nqe/changes" and r.method == "POST"
    }
    assert staged["addQuery"].url.params["path"] == "/MyOrg/devices"
    sent = json.loads(staged["addQuery"].content)["sourceCode"]
    assert "foreach d in network.devices select {name: d.name}" in sent


def test_publish_queries_with_no_files_says_so_and_calls_nothing(
    forward: Recorder, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    load("publish_queries").main(str(tmp_path))

    assert f"no .nqe files in {tmp_path}" in capsys.readouterr().out
    assert forward.requests == []


def test_ask_forward_ai_prints_the_answer_and_how_it_was_reached(
    forward: Recorder, no_sleep: list[float], capsys: pytest.CaptureFixture[str]
) -> None:
    chat = {"id": "42", "networkId": "101", "snapshotId": "900", "status": "DONE"}
    answered = {
        "id": "7",
        "prompt": "why?",
        "toolCalls": [{"type": "GET_PATHS"}],
        "answer": {"summary": "An ACL blocks it.", "keyInsights": ["ACL 10 denies"]},
    }
    statuses = [{**chat, "status": "PROCESSING"}, chat]

    def answer(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            assert request.url.path == "/api/ai-chats"
            return json_response({**chat, "status": "PROCESSING"}, 201)
        if request.url.path == "/api/ai-chats/42":
            return json_response(statuses.pop(0))
        assert request.url.path == "/api/ai-chats/42/messages"
        # As Forward does, hold no answer until the chat has finished.
        assert not statuses, "the messages were read before the chat was done"
        return json_response({"messages": [answered, {"id": "8", "prompt": "and?"}]})

    forward.default = answer

    load("ask_forward_ai").main("why?")

    out = capsys.readouterr().out
    assert "asked, chat 42, grounded in snapshot 900" in out
    assert "Q: why?\nA: An ACL blocks it.\n   - ACL 10 denies\n" in out
    assert "   (answered using GET_PATHS)\n" in out
    assert "Q: and?\n   (no answer)\n" in out
    assert forward.body_for(0) == {"prompt": "why?"}


def test_ask_forward_ai_explains_a_refusal_instead_of_failing(
    forward: Recorder, capsys: pytest.CaptureFixture[str]
) -> None:
    forward.add(
        "POST",
        "/api/ai-chats",
        error_response(403, "AI is not enabled", method="POST", url="/api/ai-chats"),
    )

    load("ask_forward_ai").main()

    assert "Forward AI is not available here: AI is not enabled" in capsys.readouterr().out
    assert len(forward.requests) == 1
