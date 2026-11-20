"""Every ``client.<service>[.<more>].<method>(`` the docs and README show exists.

A renamed or never-existing method in an example (``client.vulnerabilities.list``)
reads as working code until someone runs it.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from forward_sdk import AsyncForwardClient, ForwardClient

ROOT = Path(__file__).resolve().parents[2]
#: ``client.`` then two or more dotted names, then a call: ``client.nqe.repo.commit(``.
CALL = re.compile(r"\bclient((?:\.[a-z_][a-z0-9_]*){2,})\(")
SOURCES = sorted([*ROOT.joinpath("docs").rglob("*.md"), ROOT / "README.md"])


def calls() -> list[tuple[str, str]]:
    found = set()
    for path in SOURCES:
        for chain in CALL.findall(path.read_text(encoding="utf-8")):
            found.add((path.relative_to(ROOT).as_posix(), chain.lstrip(".")))
    return sorted(found)


@pytest.fixture(scope="module")
def clients() -> Iterator[tuple[Any, ...]]:
    sync = ForwardClient("https://forward.test", username="k", password="s")
    async_client = AsyncForwardClient("https://forward.test", username="k", password="s")
    try:
        yield sync, async_client
    finally:
        sync.close()
        asyncio.run(async_client.aclose())


def test_the_docs_name_client_methods_at_all() -> None:
    chains = [chain for _, chain in calls()]
    assert len(chains) > 50
    assert any(chain.count(".") >= 2 for chain in chains), "three-level calls are checked too"


@pytest.mark.parametrize(("source", "chain"), calls())
def test_a_documented_client_method_exists(
    source: str, chain: str, clients: tuple[Any, ...]
) -> None:
    for client in clients:
        target: Any = client
        walked = "client"
        for name in chain.split("."):
            walked += f".{name}"
            assert hasattr(target, name), f"{source}: {walked} does not exist"
            target = getattr(target, name)
