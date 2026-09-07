"""Down-convert the Forward OpenAPI description from 3.2.0 to 3.1.0.

The Forward monorepo publishes OpenAPI 3.2.0, but most Python tooling
(datamodel-code-generator, openapi-core, openapi-spec-validator) only supports
3.0/3.1. This script produces a 3.1 document that is semantically equivalent for
the purposes of model generation and request validation.

Transformations:

* ``openapi: 3.2.0`` -> ``3.1.0``.
* ``tags[].parent`` and ``tags[].summary`` -> ``x-parent`` / ``x-summary``
  (3.2 tag nesting and short labels; kept for docs navigation).
* ``itemSchema: X`` on a streaming media type -> ``schema: {type: array, items: X}``
  plus ``x-item-schema`` (the SDK streams these, but validators need a schema).
* ``NqeRecord`` is inlined at every reference and the named component dropped,
  so generated models expose query rows as ``dict[str, Any]``. An NQE row's
  columns are defined by the query, not by the API, and their names ("Name",
  "Interface Speed") are frequently not valid Python identifiers, so a model
  class would be actively harmful here.
* Path keys carrying a fixed query string (``/x?action=addBatch``) are rewritten
  to the bare path with the fixed query recorded in ``x-forward-fixed-query`` on
  each operation. Forward dispatches ~34 operations this way; OpenAPI has no
  notion of it, and leaving the ``?`` in the key breaks path matching.

It also repairs a small set of upstream defects (see ``coerce_typed_defaults``)
that make the document fail strict 3.1 validation. Those are reported so they
can be fixed upstream rather than silently carried forever.

Any *other* 3.2-only construct is a hard error: it means the upstream spec grew
a feature this script has not been taught to translate.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl

import yaml

SOURCE_VERSION = "3.2.0"
TARGET_VERSION = "3.1.0"

STREAMING_MEDIA_TYPES = ("application/jsonl", "application/x-ndjson", "application/json-seq")

# Schemas whose contents are defined by the user's NQE query rather than by the
# API. These are inlined as open objects so codegen yields dict[str, Any].
OPEN_OBJECT_SCHEMAS = ("NqeRecord",)

OPEN_OBJECT = {"type": "object", "additionalProperties": True}

# 3.2 keywords this script does not understand. If any appears, fail loudly
# rather than silently emitting a document that lies about the API.
UNSUPPORTED_32_KEYWORDS = ("querystring", "additionalOperations", "$self")

HTTP_METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")


class UnsupportedSpecFeature(RuntimeError):
    """Raised when the upstream spec uses a 3.2 feature we cannot down-convert."""


def _walk(node: Any, path: str = "") -> Any:
    """Yield ``(json_path, key, value)`` for every mapping entry in the document."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield path, key, value
            yield from _walk(value, f"{path}/{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _walk(value, f"{path}/{index}")


def check_unsupported(doc: dict[str, Any]) -> None:
    found: list[str] = []
    for path, key, _value in _walk(doc):
        if key in UNSUPPORTED_32_KEYWORDS:
            found.append(f"{path}/{key}")
    if found:
        raise UnsupportedSpecFeature(
            "spec uses OpenAPI 3.2 features this converter does not handle: "
            + ", ".join(sorted(found)[:10])
        )


# 3.2 Tag Object fields that 3.1 rejects, mapped to their extension names.
TAG_FIELDS_32 = ("parent", "summary")


def convert_tags(doc: dict[str, Any]) -> int:
    """3.2 adds ``parent`` and ``summary`` to tags; 3.1 has neither."""
    converted = 0
    for tag in doc.get("tags", []):
        for field in TAG_FIELDS_32:
            if field in tag:
                tag[f"x-{field}"] = tag.pop(field)
                converted += 1
    return converted


def convert_item_schemas(doc: dict[str, Any]) -> int:
    """Replace 3.2 ``itemSchema`` with an array ``schema`` for validators."""
    converted = 0
    for _path, key, value in _walk(doc):
        if key != "content" or not isinstance(value, dict):
            continue
        for media_type, media in value.items():
            if not isinstance(media, dict) or "itemSchema" not in media:
                continue
            item_schema = media.pop("itemSchema")
            media["x-item-schema"] = item_schema
            media.setdefault("schema", {"type": "array", "items": item_schema})
            media["x-streaming"] = media_type in STREAMING_MEDIA_TYPES
            converted += 1
    return converted


def open_nqe_record_schemas(doc: dict[str, Any]) -> int:
    """Inline the query-shaped row schemas as open objects and drop the components.

    Leaving these as named components makes code generation emit a model class
    per row type; inlining makes it emit ``dict[str, Any]``, which is what an
    NQE row actually is.
    """
    schemas = doc.get("components", {}).get("schemas", {})
    targets = {f"#/components/schemas/{name}" for name in OPEN_OBJECT_SCHEMAS if name in schemas}
    if not targets:
        return 0
    replaced = 0

    def visit(node: Any) -> Any:
        nonlocal replaced
        if isinstance(node, dict):
            if node.get("$ref") in targets:
                replaced += 1
                return dict(OPEN_OBJECT)
            return {key: visit(value) for key, value in node.items()}
        if isinstance(node, list):
            return [visit(value) for value in node]
        return node

    for name in OPEN_OBJECT_SCHEMAS:
        schemas.pop(name, None)
    doc["paths"] = visit(doc.get("paths", {}))
    doc["components"]["schemas"] = visit(schemas)
    return replaced


def split_fixed_query(doc: dict[str, Any]) -> int:
    """Move ``/path?action=x`` dispatch out of path keys into an extension.

    Forward selects among several operations on one path with a fixed query
    string. OpenAPI path keys cannot express that, so the bare path is used as
    the key and the fixed query is recorded per operation. When two operations
    collapse onto the same path+method, the fixed query is what tells them
    apart, so the merge keeps both under distinct extension values.
    """
    paths: dict[str, Any] = doc.get("paths", {})
    rebuilt: dict[str, Any] = {}
    converted = 0
    for path_key, item in paths.items():
        bare, _, query = path_key.partition("?")
        if query:
            converted += 1
            fixed = [[k, v] for k, v in parse_qsl(query, keep_blank_values=True)]
            for method in HTTP_METHODS:
                operation = item.get(method)
                if isinstance(operation, dict):
                    operation["x-forward-fixed-query"] = fixed
        target = rebuilt.setdefault(bare, {})
        for key, value in item.items():
            if key in HTTP_METHODS and key in target:
                existing = target[key]
                variants = existing.setdefault("x-forward-operation-variants", [])
                variants.append(
                    {
                        "operationId": value.get("operationId"),
                        "x-forward-fixed-query": value.get("x-forward-fixed-query", []),
                        "summary": value.get("summary"),
                    }
                )
            else:
                target[key] = value
    doc["paths"] = rebuilt
    return converted


JSON_TYPES: dict[str, type | tuple[type, ...]] = {
    "boolean": bool,
    "integer": int,
    "number": (int, float),
    "string": str,
    "array": list,
    "object": dict,
}


def coerce_typed_defaults(doc: dict[str, Any]) -> list[str]:
    """Repair defaults whose literal type contradicts the declared type.

    A few Forward schemas declare ``type: boolean`` with ``default: "true"``
    (a quoted string). Strict 3.1 validation rejects that. Coerce the obvious
    scalar cases and return what was repaired so the caller can report it
    upstream; anything not safely coercible is left alone to fail loudly.
    """
    repaired: list[str] = []

    def visit(node: Any, path: str) -> None:
        if isinstance(node, dict):
            declared = node.get("type")
            default = node.get("default")
            if isinstance(declared, str) and default is not None:
                expected = JSON_TYPES.get(declared)
                if expected is not None and not isinstance(default, expected):
                    coerced = _coerce_scalar(default, declared)
                    if coerced is not None:
                        node["default"] = coerced
                        repaired.append(f"{path}: {declared} default {default!r} -> {coerced!r}")
            for key, value in node.items():
                visit(value, f"{path}/{key}")
        elif isinstance(node, list):
            for index, value in enumerate(node):
                visit(value, f"{path}/{index}")

    visit(doc, "")
    return repaired


def _coerce_scalar(value: Any, declared: str) -> Any | None:
    if not isinstance(value, str):
        return None
    if declared == "boolean":
        if value.lower() == "true":
            return True
        if value.lower() == "false":
            return False
        return None
    try:
        if declared == "integer":
            return int(value)
        if declared == "number":
            return float(value)
    except ValueError:
        return None
    return None


def downconvert(doc: dict[str, Any]) -> tuple[dict[str, Any], dict[str, int]]:
    if doc.get("openapi") != SOURCE_VERSION:
        raise UnsupportedSpecFeature(
            f"expected openapi {SOURCE_VERSION}, found {doc.get('openapi')!r}"
        )
    check_unsupported(doc)
    doc["openapi"] = TARGET_VERSION
    repaired_defaults = coerce_typed_defaults(doc)
    stats = {
        "repaired_defaults": len(repaired_defaults),
        "tags_reparented": convert_tags(doc),
        "item_schemas": convert_item_schemas(doc),
        "open_object_schemas": open_nqe_record_schemas(doc),
        "fixed_query_paths": split_fixed_query(doc),
    }
    return doc, stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path("spec/forward-openapi.yaml"))
    parser.add_argument("--output", type=Path, default=Path("spec/forward-openapi-3.1.json"))
    args = parser.parse_args(argv)

    doc = yaml.safe_load(args.input.read_text())
    converted, stats = downconvert(doc)
    args.output.write_text(json.dumps(converted, indent=2, sort_keys=True) + "\n")

    print(f"wrote {args.output}")
    for key, value in stats.items():
        print(f"  {key}: {value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
