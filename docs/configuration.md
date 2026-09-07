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
Forward's hosted service and leaves self-hosted deployments alone. Pass an
integer to set a rate, or `None` to disable.

Three things about Forward's limiter are worth knowing, because they change how
much the number matters.

**The budget is per authenticated user.** Forward counts requests per user per
minute in a sliding window, not per client, per host or per connection. Every
process authenticating with the same credentials spends from one allowance, so
a single client's pacing cannot protect a fleet. Pass any object with an
`acquire()` method as `throttle=` to plug in a shared counter; one integration
uses a Django cache so its workers share a budget across processes.

**Overshooting stalls you rather than slowing you.** Exceeding the limit does
not delay the request. It blocks the user for a lockout period and answers with
`429` and a `Retry-After` carrying the remaining block. The lockout defaults to
one minute and an administrator can set it as high as sixty. The SDK honours
`Retry-After`, so a client waits correctly, but it waits: a breach costs a sync
its remaining runtime, not a few hundred milliseconds.

**The ceiling is a setting, not a constant.** It is an org property defaulting
to 2000 per minute, adjustable from 1 to 10,000, and on self-hosted deployments
the whole limiter ships disabled and an administrator decides whether to enable
it. The SDK cannot query any of this, so 1800 is headroom against the default,
not a reading of your ceiling. If you run many workers, or your administrator
has lowered the number, set `rate_limit_rpm` yourself.

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

## What the SDK caches, and what it does not

One thing is cached: the NQE query index, the map of library path to query,
because resolving a batch of paths would otherwise re-fetch the whole library
once per path. Any write through `client.nqe.repo` clears it.

Nothing else is. In particular a snapshot is resolved on every call that needs
one, including `latest_processed` and `latest_collected`. That is deliberate.
Caching a snapshot means deciding how long "latest" stays true, and only the
caller knows: a long sync wants one snapshot for the whole run, while a
dashboard polling for new data wants the opposite, and a wrong guess here is
silent, because a stale snapshot returns real data from the wrong point in time.

The cost is real, so plan for it. An integration migrating onto this SDK went
from one snapshot request per sync to one per slice, because its planner
resolved a snapshot inside a thread pool and the client it replaced had cached
that itself. Nothing failed and no test noticed. It showed up only as request
volume against a live account.

If you know an invariant the SDK cannot assume, tell it, with
`snapshot_cache_ttl`. It is off by default and takes a number of seconds:

```python
# This sync pins one point in time for its whole run, so resolving "latest"
# once is right here even though it would not be in general.
client = ForwardClient.from_env(snapshot_cache_ttl=600)
```

That covers `latest_processed` and `latest_collected_id`, keyed by the exact
question asked, so two different tag scopes stay two different answers. A
network with no processed snapshot is cached as an answer too, since re-asking
would spend the budget the setting exists to save.

Uploading a snapshot through the same client clears it, because that is the
event that makes a cached answer wrong. For anything the SDK cannot see, such
as an upload from another process or a snapshot that finished while you were
running, call `client.snapshots.clear_cache()`.

Leave it off if "latest" genuinely has to mean latest. A stale snapshot does not
raise. It returns real data from the wrong moment, which is why this is a
setting rather than a default.

Read `client.counters` for `cache_hits` and `cache_misses` on the one cache
that exists, and for the request counts that would reveal this kind of change.

### Why this matters more than it looks

A redundant request is not free, because the budget above is spent per user
across every process using those credentials, and exhausting it locks the user
out rather than slowing them down. So caching is not a latency optimisation
here. It is how a long sync stays inside an allowance it shares with everything
else running under the same account.

The integration that went from one snapshot request per sync to one per slice
did not get slower in any way a test could see. It simply started spending a
larger share of a budget it did not own alone. Look at `http_attempts` and
`attempts_per_minute` after a change to how often you resolve something, not
just at whether the results are still correct.

## Threads and event loops

A synchronous `ForwardClient` is safe to share across threads. An
`AsyncForwardClient` belongs to the event loop that created it.
