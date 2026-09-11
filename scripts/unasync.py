"""Derive the synchronous SDK from the asynchronous one.

Everything that performs I/O is written once, as async, in ``forward_sdk._async``
and ``tests/_async``. This script mechanically rewrites those trees into
``forward_sdk._sync`` and ``tests/_sync``.

Writing the two by hand would mean maintaining two copies of every retry rule,
poll loop and paginator, and they would drift. Generating one from the other
makes divergence impossible, and ``scripts/check_generated.py`` fails CI if the
checked-in output is stale.

The substitutions are deliberately literal and unconditional: if a construct
needs cleverness to translate, it belongs in the pure layers (``_ops``, ``nqe``)
that both clients already share.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from _tidy import tidy

TREES = (
    (Path("src/forward_sdk/_async"), Path("src/forward_sdk/_sync")),
    (Path("tests/_async"), Path("tests/_sync")),
)

HEADER = (
    "# Generated from {source} by scripts/unasync.py -- do not edit.\n"
    "# Edit the async source and re-run: uv run python scripts/unasync.py\n"
)

# Applied in order, as regular expressions over the whole file.
SUBSTITUTIONS: tuple[tuple[str, str], ...] = (
    # Language constructs.
    (r"\basync def\b", "def"),
    (r"\basync with\b", "with"),
    (r"\basync for\b", "for"),
    # `await x` and `await (…)` alike; the trailing \s+ avoids requiring a word
    # character after the keyword.
    (r"\bawait\s+", ""),
    (r"\byield from await\b", "yield from"),
    # Module paths.
    (r"forward_sdk\._async", "forward_sdk._sync"),
    (r"\btests\._async\b", "tests._sync"),
    # httpx.
    (r"\bhttpx\.AsyncClient\b", "httpx.Client"),
    (r"\bhttpx\.AsyncBaseTransport\b", "httpx.BaseTransport"),
    (r"\bhttpx\.AsyncHTTPTransport\b", "httpx.HTTPTransport"),
    (r"\bMockTransport\b", "MockTransport"),
    # Response and client methods.
    (r"\baclose\b", "close"),
    (r"\baread\b", "read"),
    (r"\baiter_lines\b", "iter_lines"),
    (r"\baiter_bytes\b", "iter_bytes"),
    (r"\baiter_text\b", "iter_text"),
    (r"\baiter_raw\b", "iter_raw"),
    (r"\b__aenter__\b", "__enter__"),
    (r"\b__aexit__\b", "__exit__"),
    (r"\b__aiter__\b", "__iter__"),
    (r"\b__anext__\b", "__next__"),
    # Concurrency primitives.
    (r"\basyncio\.sleep\b", "time.sleep"),
    (r"\basyncio\.Lock\b", "threading.Lock"),
    (r"\bimport asyncio\b", "import threading"),
    # Typing and contextlib.
    (r"\bAsyncIterator\b", "Iterator"),
    (r"\bAsyncGenerator\b", "Generator"),
    (r"\bAsyncContextManager\b", "ContextManager"),
    (r"\basynccontextmanager\b", "contextmanager"),
    (r"\bcollections\.abc import AsyncIterable\b", "collections.abc import Iterable"),
    # Public names.
    (r"\bAsyncForwardClient\b", "ForwardClient"),
    (r"\bAsyncTransport\b", "Transport"),
    (r"\bAsyncRateLimiter\b", "RateLimiter"),
    (r"\bAsyncService\b", "Service"),
    (r"\bAsyncNqeExecution\b", "NqeExecution"),
    (r"\bAsyncAiConversation\b", "AiConversation"),
    (r"\bAsyncSnapshotJob\b", "SnapshotJob"),
    (r"\bAsyncReachabilityJob\b", "ReachabilityJob"),
    (r"\bAsyncChangeSetHandle\b", "ChangeSetHandle"),
    # Generated service classes follow one naming rule, so one substitution
    # covers all of them.
    (r"\bAsync(\w+)Service\b", r"\1Service"),
    (r"\bAsyncGeneratedServices\b", "GeneratedServices"),
    (r"_attach_generated_services\b", "_attach_generated_services"),
    (r"\bAsyncNetworksService\b", "NetworksService"),
    (r"\bAsyncSnapshotsService\b", "SnapshotsService"),
    (r"\bAsyncDevicesService\b", "DevicesService"),
    (r"\bAsyncDeviceTagsService\b", "DeviceTagsService"),
    (r"\bAsyncNqeService\b", "NqeService"),
    (r"\bAsyncNqeRepository\b", "NqeRepository"),
    (r"\bAsyncNqe\b", "Nqe"),
    (r"\bAsyncPager\b", "Pager"),
    # pytest.
    (r"^@pytest\.mark\.anyio\n", ""),
    (r"\bpytest_asyncio\b", "pytest"),
)

COMPILED = tuple((re.compile(pattern, re.MULTILINE), repl) for pattern, repl in SUBSTITUTIONS)


def convert(source: str, origin: str) -> str:
    body = source
    for pattern, replacement in COMPILED:
        body = pattern.sub(replacement, body)
    # `time` and `threading` replace asyncio, so make sure they are importable
    # even where the async source only needed asyncio.
    if "time.sleep(" in body and not re.search(r"^import time$", body, re.MULTILINE):
        body = _add_import(body, "import time")
    if "threading.Lock(" in body and not re.search(r"^import threading$", body, re.MULTILINE):
        body = _add_import(body, "import threading")
    return HEADER.format(source=origin) + "\n" + body


def _add_import(body: str, statement: str) -> str:
    lines = body.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if line.startswith(("import ", "from ")) and "__future__" not in line:
            lines.insert(index, statement + "\n")
            return "".join(lines)
    return statement + "\n" + body


def sync_tree(source_root: Path, target_root: Path) -> list[Path]:
    written: list[Path] = []
    if not source_root.exists():
        return written

    expected: set[Path] = set()
    for source in sorted(source_root.rglob("*.py")):
        relative = source.relative_to(source_root)
        target = target_root / relative
        expected.add(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        converted = tidy(convert(source.read_text(encoding="utf-8"), str(source)), str(target))
        if not target.exists() or target.read_text(encoding="utf-8") != converted:
            target.write_text(converted, encoding="utf-8")
            written.append(target)

    # Remove generated files whose async source is gone, so a rename does not
    # leave an orphan behind that still imports and still passes tests.
    for stale in sorted(target_root.rglob("*.py")):
        if stale not in expected:
            stale.unlink()
            written.append(stale)

    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="report staleness, write nothing")
    args = parser.parse_args(argv)

    changed: list[Path] = []
    for source_root, target_root in TREES:
        if args.check:
            before = (
                {path: path.read_text(encoding="utf-8") for path in target_root.rglob("*.py")}
                if target_root.exists()
                else {}
            )
            sync_tree(source_root, target_root)
            after = {path: path.read_text(encoding="utf-8") for path in target_root.rglob("*.py")}
            if before != after:
                changed.extend(sorted(set(after) - set(before)) or list(after))
        else:
            changed.extend(sync_tree(source_root, target_root))

    if args.check and changed:
        print("sync tree is stale; run: uv run python scripts/unasync.py")
        return 1

    for path in changed:
        print(f"  {path}")
    print(f"unasync: {len(changed)} file(s) updated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
