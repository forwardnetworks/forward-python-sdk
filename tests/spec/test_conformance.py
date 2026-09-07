"""Requests the SDK builds are valid against Forward's API description.

The coverage test proves each operation is implemented. This one proves the
requests are actually well formed: paths resolve against the spec's templates,
query parameters are declared, and their values match the declared types and
enumerations.

Operations that carry a request body are checked for path and query shape only.
Validating a body credibly needs a realistic payload per operation; a
synthesized one would prove only that the generator and the validator agree with
each other.
"""

from __future__ import annotations

import base64
import inspect
import json
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any, get_args, get_origin

import pytest
from openapi_core import OpenAPI
from openapi_core.testing import MockRequest

from forward_sdk._generated._defs import OpDef
from forward_sdk._generated.operations import OPERATIONS
from forward_sdk._http import encode_query
from forward_sdk._ops import REGISTRY, RequestSpec
from forward_sdk._ops._generic import snake
from forward_sdk.nqe.query_ref import QueryRef

# Importing the coverage module populates the builder registry.
from tests.spec import test_coverage  # noqa: F401

SPEC_PATH = Path("spec/forward-openapi-3.1.json")

# Upload builders read their files while building the request, so that a retry
# can resend the same bytes. Conformance therefore needs a file that exists.
_UPLOAD_DIR = tempfile.TemporaryDirectory()
SAMPLE_UPLOAD = Path(_UPLOAD_DIR.name) / "snapshot.zip"
SAMPLE_UPLOAD.write_bytes(b"PK\x03\x04")
HOST = "https://forward.test"
API_ROOT = "/api"
AUTH = "Basic " + base64.b64encode(b"access-key:secret").decode()

#: Operations reached through endpoints Forward does not publish. There is no
#: description to validate them against; they are covered by unit tests instead.
UNPUBLISHED = {op_id for op_id, op in OPERATIONS.items() if op.stability == "unpublished"}

#: Forward dispatches these by a fixed query string on a path it shares with
#: other operations. Down-converting merges them onto one path key, so the
#: validator would check them against whichever operation won the merge.
DISPATCHED = {op_id for op_id, op in OPERATIONS.items() if op.fixed_query}

#: Operations that send a body. See the module docstring.
WITH_BODY = {op_id for op_id, op in OPERATIONS.items() if op.request_media}


@pytest.fixture(scope="module")
def api() -> OpenAPI:
    return OpenAPI.from_dict(json.loads(SPEC_PATH.read_text()))


def sample_for(name: str, annotation: Any) -> Any:
    """Invent a plausible argument for a builder parameter.

    Values only need to be type-correct: this checks request shape, not
    behaviour.
    """
    origin = get_origin(annotation)
    if annotation is QueryRef or origin is QueryRef:
        return QueryRef.inline("foreach d in network.devices select {n: d.name}")
    if origin in (list, tuple, Sequence) or annotation in (list, tuple):
        # A sequence of paths means file uploads; anything else can be empty.
        return [SAMPLE_UPLOAD] if Path in get_args(annotation) else []
    if annotation is bool:
        return False
    if annotation is int:
        return 1
    if annotation is Path:
        return SAMPLE_UPLOAD
    if "Mapping" in str(annotation) or "dict" in str(annotation):
        return {}
    if name.endswith(("_id", "Id")):
        return "101"
    if name in {"path", "directory"}:
        return "/Example"
    if name == "title":
        return "example"
    return "example"


def enum_values(operation: OpDef) -> dict[str, str]:
    """Allowed values for parameters the spec constrains to an enumeration."""
    return {snake(param.name): param.enum[0] for param in operation.parameters if param.enum}


def build(operation_id: str) -> RequestSpec | None:
    """Build a representative request for an operation, or None if not possible."""
    builder = REGISTRY[operation_id]
    allowed = enum_values(OPERATIONS[operation_id])
    # eval_str resolves the string annotations that `from __future__ import
    # annotations` leaves behind; without it every annotation is just text and
    # the type dispatch below silently does nothing.
    signature = inspect.signature(builder, eval_str=True)
    arguments: dict[str, Any] = {}
    for name, parameter in signature.parameters.items():
        if parameter.kind is inspect.Parameter.VAR_KEYWORD:
            continue
        if parameter.default is not inspect.Parameter.empty:
            continue
        arguments[name] = allowed.get(name) or sample_for(name, parameter.annotation)
    return builder(**arguments)


def mock_request(spec: RequestSpec, operation: OpDef) -> MockRequest:
    """Turn a built request into something openapi-core can validate."""
    path_pattern = API_ROOT + operation.path
    view_args = {p.name: (p.enum[0] if p.enum else "101") for p in operation.path_params}
    args = {key: value for key, value in spec.params}
    return MockRequest(
        HOST,
        operation.method,
        API_ROOT + spec.path,
        path_pattern=path_pattern,
        args=args,
        view_args=view_args,
        headers={"Authorization": AUTH},
    )


VALIDATABLE = sorted(set(REGISTRY) - UNPUBLISHED - DISPATCHED - WITH_BODY)


@pytest.mark.parametrize("operation_id", VALIDATABLE)
def test_request_conforms_to_the_spec(api: OpenAPI, operation_id: str) -> None:
    spec = build(operation_id)
    assert spec is not None
    operation = OPERATIONS[operation_id]
    api.validate_request(mock_request(spec, operation))


@pytest.mark.parametrize("operation_id", sorted(REGISTRY))
def test_query_parameters_are_declared(operation_id: str) -> None:
    """Every query parameter the SDK sends is one the operation accepts.

    Covers the dispatched and unpublished operations too, which the validator
    above cannot reach.
    """
    operation = OPERATIONS[operation_id]
    spec = build(operation_id)
    assert spec is not None

    declared = {p.name for p in operation.query_params}
    declared.update(key for key, _ in operation.fixed_query)
    sent = {key for key, _ in spec.params}
    undeclared = sorted(sent - declared)
    assert not undeclared, (
        f"{operation_id} sends query parameter(s) the spec does not declare: {undeclared}"
    )


@pytest.mark.parametrize("operation_id", sorted(REGISTRY))
def test_path_has_no_unfilled_placeholders(operation_id: str) -> None:
    spec = build(operation_id)
    assert spec is not None
    assert "{" not in spec.path, f"{operation_id} left a path placeholder unfilled"


@pytest.mark.parametrize("operation_id", sorted(DISPATCHED & set(REGISTRY)))
def test_dispatched_operations_send_their_selector(operation_id: str) -> None:
    """The fixed query string that selects the operation must be sent, first.

    These share a path and method with sibling operations, so the selector is
    the only thing that distinguishes them; sending it second would let a
    caller-supplied value shadow it.
    """
    operation = OPERATIONS[operation_id]
    spec = build(operation_id)
    assert spec is not None
    expected = [(key, value or "") for key, value in operation.fixed_query]
    assert list(spec.params)[: len(expected)] == expected


def test_fixed_query_is_not_overridable() -> None:
    """A caller cannot displace the value that selects the operation."""
    operation = OPERATIONS["getVulnerabilities"]
    params = encode_query({"v": "1"}, operation.fixed_query)
    assert params[0] == ("v", "2")
