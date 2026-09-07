"""The 3.2 to 3.1 down-conversion.

No Python tooling reads OpenAPI 3.2 yet, so the SDK derives a 3.1 copy of
Forward's description and generates from that. If the conversion loses or
misstates something, every generated model and every request built from the
operation table inherits the fault, so it is checked directly here.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from openapi_spec_validator import validate

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_converter() -> Any:
    """Load the converter script directly, by path.

    The generators are standalone scripts rather than a package, deliberately:
    one of them writes part of the SDK, so running it must not depend on the
    SDK importing cleanly. Loading by path preserves that and keeps the module
    from being known under two names.
    """
    path = REPO_ROOT / "scripts" / "downconvert_spec.py"
    spec = importlib.util.spec_from_file_location("forward_sdk_downconvert", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


converter = _load_converter()

OPEN_OBJECT_SCHEMAS = converter.OPEN_OBJECT_SCHEMAS
SOURCE_VERSION = converter.SOURCE_VERSION
TAG_FIELDS_32 = converter.TAG_FIELDS_32
TARGET_VERSION = converter.TARGET_VERSION
UnsupportedSpecFeature = converter.UnsupportedSpecFeature
coerce_typed_defaults = converter.coerce_typed_defaults
downconvert = converter.downconvert

SOURCE_PATH = Path("spec/forward-openapi.yaml")
DERIVED_PATH = Path("spec/forward-openapi-3.1.json")

HTTP_METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")


@pytest.fixture(scope="module")
def source() -> dict[str, Any]:
    loaded: dict[str, Any] = yaml.safe_load(SOURCE_PATH.read_text(encoding="utf-8"))
    return loaded


@pytest.fixture(scope="module")
def derived() -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(DERIVED_PATH.read_text(encoding="utf-8"))
    return loaded


def operation_ids(doc: dict[str, Any]) -> set[str]:
    found: set[str] = set()
    for item in doc.get("paths", {}).values():
        for method in HTTP_METHODS:
            operation = item.get(method)
            if isinstance(operation, dict):
                found.add(operation["operationId"])
                found.update(
                    variant["operationId"]
                    for variant in operation.get("x-forward-operation-variants", [])
                )
    return found


def test_checked_in_copy_is_valid_openapi_31(derived: dict[str, Any]) -> None:
    """The derived document must satisfy 3.1, or the tooling cannot read it."""
    validate(derived)


def test_version_is_downgraded(source: dict[str, Any], derived: dict[str, Any]) -> None:
    assert source["openapi"] == SOURCE_VERSION
    assert derived["openapi"] == TARGET_VERSION


def test_no_operation_is_lost(source: dict[str, Any], derived: dict[str, Any]) -> None:
    """Merging query-dispatched paths must not drop the operations sharing them."""
    assert operation_ids(source) == operation_ids(derived)


def test_dispatched_operations_keep_their_selector(derived: dict[str, Any]) -> None:
    """The query string that selects an operation survives the merge.

    Six operations share ``POST /networks/{id}/classic-devices``. The one with
    no selector wins the path key; the rest become variants, each keeping the
    ``?action=`` value that tells it apart.
    """
    classic = derived["paths"]["/networks/{networkId}/classic-devices"]["post"]
    assert classic["operationId"] == "addClassicDevice"
    assert "x-forward-fixed-query" not in classic

    variants = {
        variant["operationId"]: {tuple(p) for p in variant["x-forward-fixed-query"]}
        for variant in classic["x-forward-operation-variants"]
    }
    assert variants["addClassicDevices"] == {("action", "addBatch")}
    assert variants["deleteAllClassicDevices"] == {("action", "deleteAll")}
    assert variants["getSpecificClassicDevices"] == {("action", "getBatch")}


def test_paths_carry_no_query_string(derived: dict[str, Any]) -> None:
    """A `?` in a path key breaks path matching for every validator."""
    assert not [path for path in derived["paths"] if "?" in path]


def test_tag_nesting_is_preserved_as_an_extension(derived: dict[str, Any]) -> None:
    """3.1 has no nested tags, but the docs navigation still needs the tree."""
    tags = derived.get("tags", [])
    assert any("x-parent" in tag for tag in tags)
    for tag in tags:
        for field in TAG_FIELDS_32:
            assert field not in tag, f"3.2-only tag field {field!r} survived"


def test_streaming_item_schemas_become_arrays(derived: dict[str, Any]) -> None:
    """3.2 `itemSchema` describes one streamed record; 3.1 needs a schema."""
    content = derived["paths"]["/networks/{networkId}/nqe-executions/{executionKey}/result"]["get"][
        "responses"
    ]["200"]["content"]
    ndjson = content["application/x-ndjson"]
    assert "itemSchema" not in ndjson
    assert ndjson["schema"]["type"] == "array"
    assert ndjson["x-streaming"] is True


def test_query_rows_are_open_objects(derived: dict[str, Any]) -> None:
    """NQE row columns are defined by the query, so the schema stays open."""
    for name in OPEN_OBJECT_SCHEMAS:
        assert name not in derived["components"]["schemas"]
    items = derived["components"]["schemas"]["NqeRunResult"]["properties"]["items"]["items"]
    assert items == {"type": "object", "additionalProperties": True}


def test_mistyped_defaults_are_repaired() -> None:
    """Upstream declares a few booleans with a quoted string default."""
    document: dict[str, Any] = {
        "components": {
            "schemas": {
                "Example": {
                    "properties": {
                        "collect": {"type": "boolean", "default": "true"},
                        "verify": {"type": "boolean", "default": "false"},
                        "count": {"type": "integer", "default": "5"},
                    }
                }
            }
        }
    }
    repaired = coerce_typed_defaults(document)
    properties: dict[str, Any] = document["components"]["schemas"]["Example"]["properties"]
    assert properties["collect"]["default"] is True
    assert properties["verify"]["default"] is False
    assert properties["count"]["default"] == 5
    assert len(repaired) == 3


def test_uncoercible_default_is_left_alone() -> None:
    """Only unambiguous repairs are made; anything else must fail loudly upstream."""
    document: dict[str, Any] = {"a": {"type": "boolean", "default": "perhaps"}}
    assert coerce_typed_defaults(document) == []
    assert document["a"]["default"] == "perhaps"


def test_unknown_32_feature_is_refused() -> None:
    """A 3.2 construct this converter has not been taught must not pass silently."""
    document = {
        "openapi": SOURCE_VERSION,
        "paths": {"/x": {"get": {"operationId": "x", "querystring": {}}}},
    }
    with pytest.raises(UnsupportedSpecFeature, match="does not handle"):
        downconvert(document)


def test_wrong_source_version_is_refused() -> None:
    with pytest.raises(UnsupportedSpecFeature, match="expected openapi"):
        downconvert({"openapi": "3.0.0", "paths": {}})


def test_conversion_is_deterministic(source: dict[str, Any]) -> None:
    """Re-running the generators must not produce a diff, or CI fails forever."""
    first, _ = downconvert(yaml.safe_load(SOURCE_PATH.read_text(encoding="utf-8")))
    second, _ = downconvert(yaml.safe_load(SOURCE_PATH.read_text(encoding="utf-8")))
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
