# Configuration

```python
from forward_sdk import ForwardClient

client = ForwardClient(
    "https://fwd.app",
    username="abcd-efghi-jklm",
    password="secret",
    verify=True,
    timeout=300.0,
    retries=4,
    rate_limit_rpm="auto",
    network_id="101",
    snapshot_id=None,
    user_agent="my-integration/1.2",
)
```

## From the environment

`ForwardClient.from_env()` reads these, and keyword arguments override them:

| Variable | Meaning |
| --- | --- |
| `FORWARD_URL` | Instance URL. Required. |
| `FORWARD_USERNAME` | API token access key, or a login name. |
| `FORWARD_PASSWORD` | API token secret, or a password. |
| `FORWARD_NETWORK_ID` | Default network. |
| `FORWARD_SNAPSHOT_ID` | Default snapshot. |
| `FORWARD_VERIFY_TLS` | `false` disables certificate verification. |
| `FORWARD_TIMEOUT` | Read timeout in seconds. |
| `FORWARD_RETRIES` | Retry count. |
| `FORWARD_RATE_LIMIT_RPM` | Requests per minute. |
| `FORWARD_USER_AGENT` | Extra `User-Agent` token. |

These names match the Forward NetBox and Nautobot plugins, so an existing
deployment's environment works unchanged.

## Base URL

Pass either the instance URL or the API root; `/api` is appended only when it is
not already there.

## Timeouts

`timeout` accepts a read deadline in seconds, or a full `httpx.Timeout` for
separate connect, read, write and pool limits. The default allows ten seconds to
connect and five minutes to read, because a large query legitimately takes
minutes. Streaming responses get a longer read timeout, since Forward may pause
between chunks while it works.

## Retries

`retries` accepts a count, or a `RetryPolicy` for full control:

```python
from forward_sdk.config import RetryPolicy

client = ForwardClient(
    "https://fwd.app",
    retries=RetryPolicy(
        max_attempts=6,
        backoff_base=0.5,
        backoff_cap=30.0,
        max_retry_after=120.0,
    ),
)
```

`max_retry_after` caps an honoured `Retry-After`, so an unreasonable server
value cannot stall a caller indefinitely.

## Rate limiting

`rate_limit_rpm="auto"`, the default, paces requests at 1800 per minute against
Forward's hosted service, which blocks above 2000 per minute. The gap leaves
headroom for anything else using the same account, since the ceiling is
account-wide rather than per client. Self-hosted deployments are left
unthrottled, because their limits are a local matter. Pass an integer to set a
rate, or `None` to disable.

The limiter is per client. A fleet of workers sharing one Forward account needs
a shared counter to stay under an account-wide ceiling; pass any object with an
`acquire()` method as `throttle=` to plug one in.

## TLS

`verify` defaults to `True` and accepts a CA bundle path. Prefer supplying the
bundle over disabling verification.

## Proxies

`trust_env=True` (the default) honours `HTTPS_PROXY` and `NO_PROXY`. Pass
`proxy=` to set one explicitly.

## Observability

```python
client.counters  # requests, retries, 429s, sleep time, NQE rows and pages
```

Pass `hooks=` with a subclass of `forward_sdk.telemetry.Hooks` to observe each
request, response, retry and pause. Override only the methods you need. A hook
that raises is logged and ignored rather than failing the request it was
watching.

The SDK logs to the `forward_sdk` logger at debug level, and never logs
credentials or bodies.

## Threads and event loops

A synchronous `ForwardClient` is safe to share across threads. An
`AsyncForwardClient` belongs to the event loop that created it.
