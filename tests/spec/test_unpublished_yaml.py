"""``spec/unpublished.yaml`` says what its author meant.

In a one-line YAML mapping, ``{ type: string, description: A user id, not a
name. }`` is cut at the comma: the description becomes "A user id" and "not a
name." becomes a key with no value, which then appears in the generated JSON
and models. Quote such descriptions.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

SPEC = Path(__file__).resolve().parents[2] / "spec" / "unpublished.yaml"


def stray_keys(node: Any, path: str = "") -> list[str]:
    found = []
    if isinstance(node, dict):
        for key, value in node.items():
            if value is None and isinstance(key, str) and (" " in key or key.endswith((".", ")"))):
                found.append(f"{path}: {key!r}")
            found.extend(stray_keys(value, f"{path}/{key}"))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            found.extend(stray_keys(value, f"{path}[{index}]"))
    return found


def test_no_description_is_split_into_a_stray_key() -> None:
    assert stray_keys(yaml.safe_load(SPEC.read_text(encoding="utf-8"))) == []


def test_the_check_finds_a_split_description() -> None:
    split = yaml.safe_load("p: { type: string, description: A user id, not a name. }")
    assert stray_keys(split) == ["/p: 'not a name.'"]
