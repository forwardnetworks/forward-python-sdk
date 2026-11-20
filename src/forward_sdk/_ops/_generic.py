"""Builders derived from the operation table.

Most Forward operations are ordinary: fill the path template, pass the declared
query parameters, send a JSON body if there is one. Hand-writing a hundred and
fifty near-identical builders would be the same duplication this SDK avoids
elsewhere, and each copy an opportunity to mistype a parameter name.

So those are built from the operation table instead. The generated builder gets
a real :class:`inspect.Signature` derived from the spec, which means editors and
the conformance suite see the same required path parameters and optional query
parameters that Forward documents.

Operations whose shape is not ordinary -- streaming, multipart uploads, bodies
assembled from several arguments -- are written by hand in the other modules.
"""

from __future__ import annotations

import inspect
import ipaddress
import json
import keyword
import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from pydantic import BaseModel

from forward_sdk._generated._defs import OpDef, ParamDef
from forward_sdk._generated.operations import OPERATIONS
from forward_sdk._ops import JSON_ACCEPT, RequestSpec, op, spec_for

__all__ = ["build_all", "generic", "snake"]

BODY_ARGUMENT = "body"

_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def snake(name: str) -> str:
    """Convert an API parameter name to Python style.

    ``networkId`` becomes ``network_id``. Forward has parameters named ``with``
    and ``from``, which are Python keywords, so those gain a trailing underscore
    (``with_``, ``from_``) following the usual convention.
    """
    converted = _CAMEL_BOUNDARY.sub("_", name).lower()
    return converted + "_" if keyword.iskeyword(converted) else converted


def _annotation(param: ParamDef) -> Any:
    if param.schema_type == "boolean":
        return "bool | None"
    if param.schema_type == "integer":
        return "int | None"
    if param.schema_type == "number":
        return "float | None"
    if param.schema_type == "array":
        return "Sequence[str] | None"
    return "str | None"


def _signature(operation: OpDef, *, has_body: bool) -> inspect.Signature:
    """Derive a keyword-only signature from what the spec declares."""
    parameters = [
        inspect.Parameter(
            snake(param.name),
            inspect.Parameter.KEYWORD_ONLY,
            annotation="str",
        )
        for param in operation.path_params
    ]
    if has_body:
        parameters.append(
            inspect.Parameter(
                BODY_ARGUMENT,
                inspect.Parameter.KEYWORD_ONLY,
                default=None,
                annotation="Any",
            )
        )
    # A parameter fixed by the operation's dispatch is not the caller's to set.
    # A bare key (``?action``) only makes the route match: its value is the caller's.
    dispatched = {key for key, value in operation.fixed_query if value is not None}
    settable = [p for p in operation.query_params if p.name not in dispatched]

    # Required query parameters get no default, so omitting one is caught here
    # rather than by the server. Forward has several, `dstIp` on path search
    # among them. The exception is a parameter with exactly one legal value,
    # which is defaulted rather than demanded of every caller.
    parameters.extend(
        inspect.Parameter(
            snake(param.name),
            inspect.Parameter.KEYWORD_ONLY,
            annotation=_annotation(param).removesuffix(" | None"),
        )
        for param in settable
        if param.required and len(param.enum) != 1
    )
    parameters.extend(
        inspect.Parameter(
            snake(param.name),
            inspect.Parameter.KEYWORD_ONLY,
            default=param.enum[0],
            annotation=_annotation(param).removesuffix(" | None"),
        )
        for param in settable
        if param.required and len(param.enum) == 1
    )
    parameters.extend(
        inspect.Parameter(
            snake(param.name),
            inspect.Parameter.KEYWORD_ONLY,
            default=None,
            annotation=_annotation(param),
        )
        for param in settable
        if not param.required
    )
    return inspect.Signature(parameters, return_annotation="RequestSpec")


def generic(
    operation_id: str,
    *,
    accept: str | None = None,
    stream: bool | None = None,
    idempotent: bool | None = None,
) -> Callable[..., RequestSpec]:
    """Create and register a builder for ``operation_id`` from the operation table.

    Whether the response is streamed, and what to accept, are taken from the
    operation's declared response media unless overridden.
    """
    operation = OPERATIONS[operation_id]
    if stream is None:
        stream = operation.stream
    if accept is None:
        accept = operation.response_media[0] if stream and operation.response_media else JSON_ACCEPT
    has_body = bool(operation.request_media)
    raw_body = operation.request_media == ("application/octet-stream",)
    form_body = operation.request_media == ("application/x-www-form-urlencoded",)
    multipart_body = operation.request_media == ("multipart/form-data",)
    path_names = {snake(param.name): param.name for param in operation.path_params}
    query_names = {snake(param.name): param.name for param in operation.query_params}
    # A required parameter with exactly one legal value. `__signature__` is
    # introspection only and does not apply defaults at call time, so the value
    # is supplied here.
    implied = {
        snake(param.name): param.enum[0]
        for param in operation.query_params
        if param.required and len(param.enum) == 1
    }

    checked = {snake(p.name): p for p in operation.parameters if p.validate}
    for param in checked.values():
        if param.validate not in _CHECKS:
            raise AssertionError(f"unknown parameter check {param.validate!r} on {operation_id}")

    def build(**kwargs: Any) -> RequestSpec:
        for name, value in implied.items():
            kwargs.setdefault(name, value)
        for name, param in checked.items():
            _check(operation_id, param, kwargs.get(name))
            if param.validate == "non-blank":
                # Forward compares these exactly, so padding would match nothing.
                kwargs[name] = str(kwargs[name]).strip()
            elif param.validate == "cve-id":
                # Forward's CveId is upper case; the check above accepted any case.
                kwargs[name] = str(kwargs[name]).strip().upper()

        body = kwargs.pop(BODY_ARGUMENT, None) if has_body else None
        path_params: dict[str, Any] = {}
        query: dict[str, Any] = {}
        for key, value in kwargs.items():
            if key in path_names:
                path_params[path_names[key]] = value
            elif key in query_names:
                query[query_names[key]] = value
            else:
                # Undeclared parameters are passed through under their given
                # name: Forward occasionally accepts more than it documents, and
                # refusing here would be worse than letting the server decide.
                query[key] = value

        extra: dict[str, Any] = {}
        if idempotent is not None:
            extra["idempotent"] = idempotent
        if raw_body:
            # An octet-stream body is the bytes themselves. Sending them through
            # the JSON path would encode them as a string and Forward would
            # reject it as unparseable protobuf.
            if body is None:
                raise TypeError(f"{operation_id} needs a body of bytes")
            if not isinstance(body, (bytes, bytearray, memoryview)):
                raise TypeError(f"{operation_id} takes bytes, not {type(body).__name__}")
            extra["content"] = bytes(body)
            extra["headers"] = {"Content-Type": operation.request_media[0]}
            body = None
        elif form_body:
            # Form fields (the password for a new API token, say). As JSON they
            # never arrive: Forward answers that the field is required. No body
            # is an empty form: an SSO user mints a token with its name only.
            if body is None:
                body = {}
            if not isinstance(body, Mapping):
                raise TypeError(
                    f"{operation_id} takes its form fields as a mapping, not {type(body).__name__}"
                )
            # Values go to httpx as they are: it writes a bool as true/false and
            # a list as the field repeated.
            extra["data"] = {key: value for key, value in body.items() if value is not None}
            body = None
        elif multipart_body:
            # A file part is bytes, a file object, or a (filename, content[,
            # content type]) tuple; a mapping is a JSON part (add_data_file's
            # "request"); text, numbers, bools and lists of them are plain form
            # fields (a list repeats the field), as httpx sends them.
            if not isinstance(body, Mapping):
                raise TypeError(
                    f"{operation_id} takes its parts as a mapping of part name to content, "
                    f"not {type(body).__name__}"
                )
            files: list[tuple[str, Any]] = []
            fields: dict[str, Any] = {}
            for key, value in body.items():
                if value is None:
                    continue
                if isinstance(value, Mapping):
                    files.append((key, (None, json.dumps(_wire(value)), "application/json")))
                elif isinstance(value, (bytes, bytearray, memoryview, tuple)) or hasattr(
                    value, "read"
                ):
                    files.append((key, _part(value)))
                else:
                    fields[key] = value
            if not files:
                # Without a file part httpx would send the fields url-encoded,
                # which a multipart endpoint refuses with a confusing error.
                raise TypeError(
                    f"{operation_id} needs at least one file part: bytes, a file object or a "
                    "(filename, content[, content type]) tuple, not text"
                )
            extra["files"] = files
            if fields:
                extra["data"] = fields
            body = None
        return spec_for(
            operation_id,
            path_params=path_params,
            query=query,
            json=_wire(body),
            accept=accept,
            stream=stream,
            **extra,
        )

    build.__name__ = snake(operation_id)
    build.__qualname__ = build.__name__
    build.__doc__ = _describe(operation)
    build.__signature__ = _signature(operation, has_body=has_body)  # type: ignore[attr-defined]
    return op(operation_id)(build)


def _part(value: Any) -> Any:
    """A file part as httpx takes it: a memoryview, alone or as the content in a tuple, as bytes."""
    if isinstance(value, memoryview):
        return bytes(value)
    if isinstance(value, tuple) and len(value) > 1 and isinstance(value[1], memoryview):
        return (value[0], bytes(value[1]), *value[2:])
    return value


def _wire(body: Any) -> Any:
    """A request model (or a list of them) as the JSON Forward expects; anything else unchanged.

    So ``add_location(body=NewLocation(...))`` works as well as passing
    ``.to_api()`` or a plain dict.
    """
    if isinstance(body, BaseModel):
        to_api = getattr(body, "to_api", None)
        return (
            to_api()
            if callable(to_api)
            else body.model_dump(by_alias=True, exclude_none=True, mode="json")
        )
    if isinstance(body, (list, tuple)) and any(isinstance(item, BaseModel) for item in body):
        return [_wire(item) for item in body]
    return body


_CHECKS = frozenset({"cve-id", "ip-address", "non-blank"})
_CVE_ID = re.compile(r"(?i)CVE-[0-9]{4}-[0-9]{4,}")


def _check(operation_id: str, param: ParamDef, value: Any) -> None:
    """Refuse a value Forward would misread, before it is sent.

    ``ip-address``: Forward answers 500 to anything else; blank counts as
    absent, as Forward treats it. ``non-blank``: a required filter Forward
    would take as "match nothing" when blank, returning an empty result that
    looks like a real answer. ``cve-id``: CVE-YYYY-NNNN in any case, as
    Forward's CveId requires; anything else is refused before a request.
    """
    blank = value is None or not str(value).strip()
    if param.validate == "cve-id":
        if blank or not _CVE_ID.fullmatch(str(value).strip()):
            raise ValueError(
                f"{operation_id}: {param.name} must be a CVE id such as CVE-2024-20399, "
                f"not {value!r}"
            )
        return
    if param.validate == "non-blank":
        if blank:
            raise ValueError(
                f"{operation_id}: {param.name} is required and must not be blank "
                "(Forward has no unfiltered form of this read)"
            )
        return
    if blank:
        return
    try:
        ipaddress.ip_address(str(value))
    except ValueError:
        raise ValueError(
            f"{operation_id}: {param.name} must be an IP address, not {value!r} "
            "(Forward answers 500 to anything else)"
        ) from None


def _describe(operation: OpDef) -> str:
    lines = [f"``{operation.method.upper()} {operation.path}``."]
    if operation.fixed_query:
        selector = "&".join(
            key if value is None else f"{key}={value}" for key, value in operation.fixed_query
        )
        lines.append(f"Selected by the query string ``{selector}``.")
    if operation.deprecated:
        lines.append("Deprecated by Forward.")
    if operation.stability != "published":
        lines.append("Unpublished: not part of Forward's documented API.")
    return " ".join(lines)


def build_all(
    operation_ids: Sequence[str],
    *,
    overrides: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Callable[..., RequestSpec]]:
    """Register generic builders for several operations at once."""
    settings = overrides or {}
    return {
        operation_id: generic(operation_id, **settings.get(operation_id, {}))
        for operation_id in operation_ids
    }
