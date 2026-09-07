"""Every generated service method reaches the endpoint it claims to.

The generated layer is one template applied 169 times, so a fault in it appears
everywhere. This drives each method once through a mock transport and checks the
request against the operation table.

Responses are not asserted here: a canned body cannot satisfy every model's
required fields, and inventing one per operation would test the fixtures rather
than the code. Parsing is covered by the targeted service tests. A validation
error therefore still counts as a pass, because the request had already been
sent and recorded by the time it was raised.
"""

from __future__ import annotations

import inspect
import tempfile
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from forward_sdk import ForwardClient
from forward_sdk._generated._defs import OpDef
from forward_sdk._generated.operations import OPERATIONS
from forward_sdk._ops._generic import snake
from forward_sdk._sync.services._generated import SERVICE_TAGS

_UPLOADS = tempfile.TemporaryDirectory()
SAMPLE_FILE = Path(_UPLOADS.name) / "sample.zip"
SAMPLE_FILE.write_bytes(b"PK\x03\x04")

NETWORK_ID = "101"

GENERATED_OPERATIONS = [op for op in OPERATIONS.values() if op.tag in SERVICE_TAGS]


def argument_for(name: str, parameter: inspect.Parameter, operation: OpDef) -> Any:
    """A plausible value for one method argument."""
    declared = {snake(p.name): p for p in operation.parameters}
    param = declared.get(name)
    if param is not None and param.enum:
        return param.enum[0]
    annotation = str(parameter.annotation)
    if name == "body":
        return {}
    if "bool" in annotation:
        return True
    if "int" in annotation:
        return 1
    if "float" in annotation:
        return 1.0
    if "Sequence" in annotation:
        return ["a"]
    return "sample"


def call_arguments(method: Any, operation: OpDef) -> dict[str, Any]:
    arguments: dict[str, Any] = {}
    for name, parameter in inspect.signature(method).parameters.items():
        if name == "self" or parameter.default is not inspect.Parameter.empty:
            continue
        arguments[name] = argument_for(name, parameter, operation)
    return arguments


def service_for(client: ForwardClient, operation: OpDef) -> Any:
    attribute = SERVICE_TAGS[operation.tag]
    return getattr(client, attribute)


@pytest.mark.parametrize("operation", GENERATED_OPERATIONS, ids=lambda op: op.operation_id)
def test_method_reaches_its_endpoint(operation: OpDef) -> None:
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=[] if operation.response_is_array else {})

    client = ForwardClient(
        "https://forward.test",
        username="key",
        password="secret",
        rate_limit_rpm=None,
        network_id=NETWORK_ID,
        transport=httpx.MockTransport(handle),
    )
    try:
        service = service_for(client, operation)
        method = getattr(service, snake(operation.operation_id))
        result = method(**call_arguments(method, operation))
        if operation.stream:
            list(result)
    except ValidationError:
        pass  # See the module docstring: the request is what matters here.
    finally:
        client.close()

    assert seen, f"{operation.operation_id} sent no request"
    request = seen[0]
    assert request.method == operation.method.upper()

    # The path must match the operation's template, with placeholders filled.
    template = operation.path.split("{")[0]
    assert request.url.path.startswith("/api" + template), (
        f"{operation.operation_id} requested {request.url.path}, "
        f"expected it to start with /api{template}"
    )

    for key, value in operation.fixed_query:
        assert request.url.params.get(key) == (value or ""), (
            f"{operation.operation_id} did not send its {key} selector"
        )
