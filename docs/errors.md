# Errors

Failed requests raise a subclass of `ForwardError`. Some arguments the SDK checks
itself raise a plain `ValueError` instead, such as an empty, `.` or `..` path
parameter, so `except ForwardError` does not catch them. A multipart body with no
file part raises `TypeError`.

One current limitation: if a streamed response drops while its body is being
read, the `httpx` exception comes through unwrapped, so also catch
`httpx.TransportError` around a stream.

```
ForwardError
├── ForwardConfigurationError   the call cannot work as written; nothing was sent
├── ForwardTransportError       no response arrived, after retries
├── ForwardTimeoutError         a polling helper (such as wait()) stopped waiting
├── ForwardPaginationError      paging could not continue safely
├── ForwardExecutionError       background work finished without results
├── ForwardResponseError        Forward answered, but not in a shape we can read
├── ForwardBgpKeyMatchedNothingError  a staged BGP key matched nothing (verify_bgp_staging)
├── ForwardMcpError             Forward's MCP server answered a JSON-RPC error (client.mcp)
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
    error.response  # the HTTP response
    error.operation  # the API operation's definition (error.operation.operation_id)
```

`ForwardRateLimitError` adds `retry_after`: the seconds `Retry-After` asked for,
uncapped, or `None`. An error body that cannot be parsed leaves `error_info` as
`None`; the HTTP error is still raised.

Other errors carry their own detail:

- `ForwardTransportError.attempts`
- `ForwardPaginationError.rows` and `.pages`: how much was collected before paging
  stopped (short of the promised total, a page that did not advance, or a page or
  row ceiling).
- `ForwardExecutionError`, raised for NQE executions and for snapshot and
  collection jobs: `.execution_key`, `.status`, `.outcome`, `.detail` and
  `.attempts`.

`str(error)` reads `GET /path failed with HTTP 404: <Forward's message>`, then
Forward's `reason` in parentheses when it sent one. With no message, it uses the
raw body text (truncated at 500 characters). A query error appends up to three of
its diagnostics.

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
in either of its two forms, up to `RetryPolicy.max_retry_after` (120 seconds by
default), and otherwise the delay is exponential with full
jitter, because SDK callers commonly fan out across a thread pool and lockstep
retries would re-stampede the server.

A request whose body is not safe to repeat, such as creating a network or
committing queries, is retried **only** on a connection error, where the server
demonstrably never received it. A read timeout on such a request is surfaced
as `ForwardTransportError` rather than silently repeating work that may already
have happened. The one
exception is Forward's rate-limit 429 ("Rate limit exceeded"): that limiter
refuses before the request reaches its handler, so repeating even a create
cannot do the work twice. Any other 429 is not repeated for such a request.

One current limitation: streamed requests do not follow this rule yet. While
connecting, and on any retryable status, they are retried whether or not they are
safe to repeat.

A few slow operations, which carry a longer minimum read timeout, are stricter:
their 5xx answers are not retried, and a transport failure is retried only when
the request never left (a connect failure or a wait for a pooled connection; for
a request not safe to repeat, a connect failure only).

Streaming responses are retried while connecting, but not once the body has
started arriving, since a partly consumed stream cannot be rewound without
dropping or duplicating rows. When you need resumability, page instead of
streaming.

## Denials

`ForwardAuthError`, `ForwardPermissionError` and `ForwardNotFoundError` can each
mean several different things, including a missing licence or an
undeployed feature. See [availability](gating.md).

`error.denial` reads Forward's wording and returns a `Denial`, or `None`.
`error.denial.kind` is `rbac`, `license`, `license_expired`, `org_setting`,
`deployment_setting` or `authority`; `error.denial.detail` is the permission,
setting, authority or query Forward named, or `None`.
`forward_sdk.errors.classify_denial(message)` does the same for a message. It is
a hint from prose, not a contract: branch on `status`.

A few more cases look like the ordinary kind of the same status:

- `error.endpoint_not_served` is true for a 404 that means Forward has no route
  for the request ("No endpoint GET /api/backups."), as opposed to a missing
  resource. A delete through such a route deleted nothing.
- `error.action_not_supported` is true for a 400 that means this deployment
  does not map the `?action=` on the route (Spring's `Parameter conditions
  "action=bulkAdd" not met ...`), which is what an older build looks like for a
  newer action. Fall back to the older call. Read from Spring's source; not
  seen live.
- `error.servicenow_instance_asleep` is true for the 409 a hibernating or
  suspended ServiceNow instance causes; nothing was created, and a retry once
  it is woken can work.
- `error.unknown_network_property` is true for the 400 a property Forward
  defines only for orgs gets when written per network.
- `error.check_field_not_allowed` is true for the 400 for a check field its
  type refuses or requires: a name on a Predefined or non-parameterized NQE
  check (or none on a parameterized one), a note on an NQE check, tags on
  anything but a path check, `perfMonitoringEnabled` on anything but an
  Existential check. The NQE check wordings were seen live on 26.10.6; the other
  check wordings are read from source.
- `error.unrecognized_devices` lists the devices of the 400 Forward answers
  when an atlas update names a device it does not know as a collection source
  (`None` otherwise).
- `error.org_not_accessible` is true for the 403 a support or cross-org key
  gets for an organization that has not granted it access (seen live on
  26.10.6).
- `error.account_locked` is true for a 401 that means the account is locked, not
  that the credential is wrong. The wording is from Forward's source and has not
  been seen live.

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

That strictness is narrower than it sounds. Many generated models require nothing,
including several on the busiest paths:

| Model | Required fields |
| --- | --- |
| `SnapshotInfo` | none |
| `Device` | none |
| `NqeRunResult` | none |
| `ApiVersion` | none |
| `Network` | `id`, `name`, `orgId` |

If you do hit one, `payload` is what tells you whether Forward changed or the
model is wrong. Report it either way.

## Source

- `src/forward_sdk/errors.py`
- `src/forward_sdk/_http.py`
- `src/forward_sdk/_error_body.py`
- `src/forward_sdk/_async/transport.py`
- `src/forward_sdk/config.py`
- `src/forward_sdk/_generated/models.py`
