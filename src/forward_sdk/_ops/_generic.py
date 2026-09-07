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
import keyword
import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any

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
    dispatched = {key for key, _ in operation.fixed_query}
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

    def build(**kwargs: Any) -> RequestSpec:
        for name, value in implied.items():
            kwargs.setdefault(name, value)

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
        return spec_for(
            operation_id,
            path_params=path_params,
            query=query,
            json=body,
            accept=accept,
            stream=stream,
            **extra,
        )

    build.__name__ = snake(operation_id)
    build.__qualname__ = build.__name__
    build.__doc__ = _describe(operation)
    build.__signature__ = _signature(operation, has_body=has_body)  # type: ignore[attr-defined]
    return op(operation_id)(build)


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
