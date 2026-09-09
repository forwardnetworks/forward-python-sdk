# Errors

Every failure raises a subclass of `ForwardError`.

```
ForwardError
├── ForwardConfigurationError   the call cannot work as written; nothing was sent
├── ForwardTransportError       no response arrived, after retries
├── ForwardTimeoutError         a client-side deadline expired
├── ForwardPaginationError      paging could not continue safely
├── ForwardExecutionError       background work finished without results
├── ForwardResponseError        Forward answered, but not in a shape we can read
└── ForwardAPIError             Forward returned an error response
    ├── ForwardBadRequestError       400
    │   └── ForwardNqeQueryError     400 with query diagnostics
    ├── ForwardAuthError             401
    ├── ForwardPermissionError       403
    ├── ForwardNotFoundError         404
    ├── ForwardConflictError         409
    ├── ForwardRateLimitError        429, after retries
    └── ForwardServerError           5xx
```

## Reading an API error

Forward returns an error body on any 4xx or 5xx, even where its API description
documents no such response, so the SDK always tries to parse one:

```python
from forward_sdk import ForwardAPIError

try:
    client.snapshots.metrics("does-not-exist")
except ForwardAPIError as error:
    error.status  # 404
    error.method  # "GET"
    error.url  # the full URL
    error.error_info  # Forward's parsed error body, when it sent one
    error.reason  # a machine-readable reason code, when present
    error.text  # the raw body, always
    error.attempts  # how many times the request was sent
    error.operation  # which API operation this was
```

`str(error)` leads with Forward's own message rather than a generic description.

## Query errors carry positions

A query that fails to compile reports where:

```python
from forward_sdk import ForwardNqeQueryError

try:
    client.nqe.run("foreach d in network.devices select {n: d.nmae}")
except ForwardNqeQueryError as error:
    for diagnostic in error.query_errors:
        print(diagnostic.message)  # "unknown field 'nmae'"
        print(diagnostic.location)  # where in the query source
```

## What is retried

Retries are automatic for connection failures and for `408`, `425`, `429`,
`500`, `502`, `503` and `504`. `Retry-After` is honoured when Forward sends it,
in either of its two forms, and otherwise the delay is exponential with full
jitter, because SDK callers commonly fan out across a thread pool and lockstep
retries would re-stampede the server.

A request whose body is not safe to repeat, such as creating a network or
committing queries, is retried **only** on a connection error, where the server
demonstrably never received it. A read timeout on such a request is surfaced
rather than silently repeating work that may already have happened.

Streaming responses are retried while connecting, but not once the body has
started arriving, since a partly consumed stream cannot be rewound without
dropping or duplicating rows. When you need resumability, page instead of
streaming.

## Denials

`ForwardAuthError`, `ForwardPermissionError` and `ForwardNotFoundError` can each
mean several different things, including a missing licence or an
undeployed feature. See [availability](gating.md).

## When the response cannot be read

A successful call whose body does not fit the model raises
`ForwardResponseError`. It is under `ForwardError` deliberately: pydantic's
`ValidationError` is not, so before this a parse failure travelled straight
through every `except ForwardError` a caller had written and surfaced as an
unhandled crash. A sync would die with a validation traceback in a job log
instead of a failure its own handling could classify.

```python
from forward_sdk import ForwardError
from forward_sdk.errors import ForwardResponseError

try:
    networks = client.networks.list()
except ForwardResponseError as error:
    error.payload  # what Forward actually sent
    error.model_name  # what the SDK tried to build
    error.__cause__  # the pydantic ValidationError, with per-field detail
```

The models are lenient in one direction and strict in the other, on purpose.
Unknown fields are kept and unknown enum values are tolerated, so a newer
Forward cannot break an older SDK. Required fields are the exception, and they
come from Forward's own generated description rather than from a guess here: a
missing one means the response is not the thing it claims to be, and a model
with a hole in it would carry that silently into whatever you write next.

That strictness is narrower than it sounds. Of 276 generated models, 170 require
at least one field, 379 fields in total, and the models on the busiest paths
require nothing at all:

| Model | Required fields |
| --- | --- |
| `SnapshotInfo` | none |
| `Device` | none |
| `NqeRunResult` | none |
| `ApiVersion` | none |
| `Network` | `id`, `name`, `orgId` |

If you do hit one, `payload` is what tells you whether Forward changed or the
model is wrong. Report it either way.
