"""A repeated mapping key in the hand-written description loses data silently.

YAML keeps the last of two equal keys, so describing one path twice, or one
schema twice, drops everything the first said. That happened when two drafts of
the same routes were folded in, and nothing failed. This loads the file with a
loader that refuses repeats.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

SPEC_PATH = Path("spec/unpublished.yaml")


class _StrictLoader(yaml.SafeLoader):
    pass


def _construct_mapping(loader: yaml.SafeLoader, node: yaml.MappingNode, deep: bool = False) -> Any:
    seen: set[Any] = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in seen:
            raise yaml.constructor.ConstructorError(
                None, None, f"duplicate key {key!r}", key_node.start_mark
            )
        seen.add(key)
    return yaml.SafeLoader.construct_mapping(loader, node, deep)


_StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping)


def test_no_mapping_key_is_repeated() -> None:
    try:
        yaml.load(SPEC_PATH.read_text(encoding="utf-8"), _StrictLoader)
    except yaml.constructor.ConstructorError as error:
        mark = error.problem_mark
        pytest.fail(f"{SPEC_PATH}: {error.problem} at line {mark.line + 1 if mark else '?'}")


def test_the_strict_loader_does_catch_a_repeat() -> None:
    with pytest.raises(yaml.constructor.ConstructorError):
        yaml.load("paths:\n  /a: {}\n  /a: {}\n", _StrictLoader)
