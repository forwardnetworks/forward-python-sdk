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

`ForwardClient.from_env()` reads these, and keyword arguments override them. Pass a mapping first to read
from it instead of `os.environ`, for example after translating your own variable names:
`ForwardClient.from_env({"FORWARD_URL": ...})`. The underlying `forward_sdk.config.config_from_env` is public.

| Variable | Meaning |
| --- | --- |
| `FORWARD_URL` | Instance URL. Required. |
| `FORWARD_USERNAME` | API token access key, or a login name. |
| `FORWARD_PASSWORD` | API token secret, or a password. |
| `FORWARD_NETWORK_ID` | Default network. |
| `FORWARD_SNAPSHOT_ID` | Default snapshot. |
| `FORWARD_VERIFY_TLS` | `true` (also `1`, `yes`, `on`) or `false` (also `0`, `no`, `off`, which disables certificate verification), in any case; or a path to a CA bundle file or directory, to verify against that CA instead of the system trust store (the usual fix for a private CA). |
| `FORWARD_TIMEOUT` | The same as a numeric `timeout`, in seconds; see [timeouts](#timeouts). |
| `FORWARD_RETRIES` | Retry count. |
| `FORWARD_RATE_LIMIT_RPM` | Requests per minute (an integer); `0` or less disables pacing. |
| `FORWARD_SNAPSHOT_CACHE_TTL` | `lifetime`, or seconds; see the caching section. |
| `FORWARD_USER_AGENT` | Extra `User-Agent` token. |

Surrounding whitespace is stripped, and a blank value counts as unset. A
malformed number, or a `FORWARD_VERIFY_TLS` that is neither a recognised word nor
an existing path, raises `ForwardConfigurationError` naming the variable.

These names match the Forward NetBox and Nautobot plugins, so an existing
deployment's environment works unchanged.

## Base URL

Pass either the instance URL or the API root; `/api` is appended only when it is
not already there.

## Timeouts

`timeout` accepts a number of seconds, or a full `httpx.Timeout` for separate
connect, read, write and pool limits. A number sets the read and write limits;
the connect limit becomes that number or ten seconds, whichever is smaller, and
the pool limit stays at ten seconds. The default allows ten seconds to connect or
to get a pooled connection and five minutes to read or write, because a large
query legitimately takes minutes. Some slow operations raise a shorter read limit
to their own minimum. Streaming responses have a separate read timeout of ten
minutes, which `timeout` does not change, since Forward may pause between chunks
while it works.

## Retries

`retries` accepts a count of retries after the first send (`retries=4` allows
five sends), or a `RetryPolicy` for full control. The default is three retries.
The statuses that can be retried are 408, 425, 429, 500, 502, 503 and 504; not
every request is safe to repeat, and some are retried in fewer cases. Without a
`Retry-After`, the delay is a random value between zero and an exponentially
growing limit (`backoff_base`, doubling, up to `backoff_cap`).

```python
from forward_sdk.config import RetryPolicy

client = ForwardClient(
    "https://fwd.app",
    retries=RetryPolicy(
        max_attempts=6,  # counts the first send, so 5 retries
        backoff_base=0.5,
        backoff_cap=30.0,
        max_retry_after=120.0,
    ),
)
```

`max_retry_after` caps an honoured `Retry-After`, so an unreasonable server
value cannot stall a caller indefinitely. A lockout longer than the cap is not
waited out in full. `backoff_cap` does not limit a `Retry-After`. A
`max_attempts` below 1, or a backoff value that is not positive, raises
`ForwardConfigurationError`.

## Rate limiting

`rate_limit_rpm="auto"`, the default, paces requests at 1800 per minute against
Forward's hosted service (a host of `fwd.app` or ending in `.fwd.app`) and
leaves other deployments alone. Pass a positive integer to set a rate, or `None`
(or zero or less) to disable.

Three things about Forward's limiter are worth knowing, because they change how
much the number matters.

**The budget is per authenticated user.** Forward counts requests per user per
minute in a sliding window, not per client, per host or per connection. Every
process authenticating with the same credentials spends from one allowance, so
a single client's pacing cannot protect a fleet. To plug in a shared counter,
pass an object as `throttle=` whose `acquire()` blocks until a request may be sent
and returns the seconds it slept, as a `float`. For `AsyncForwardClient` it must
be `async def`. It replaces the pacing set by `rate_limit_rpm`. One integration
uses a Django cache so its workers share a budget across processes.

**Overshooting stalls you rather than slowing you.** Exceeding the limit does
not delay the request. It blocks the user for a lockout period and answers with
`429` and a `Retry-After` carrying the remaining block. The lockout defaults to
one minute and an administrator can set it as high as sixty. The SDK honours
`Retry-After` up to `max_retry_after` (120 seconds by default) and for as many
attempts as the retry policy allows, then raises `ForwardRateLimitError`. A
breach costs a sync the lockout, not a few hundred milliseconds.

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

Pass `hooks=` with a subclass of `forward_sdk.telemetry.Hooks`, and override
only the methods you need: `on_request` (each attempt), `on_response` (responses
to non-streaming requests), `on_retry` (before a retry delay) and `on_sleep`
(reports a rate-limit pacing pause). They run inline, so keep them cheap. A hook
that raises is logged and ignored rather than failing the request it was
watching.

`client.counters` also splits failures into `http_timeouts`, `transport_errors`
(which includes the timeouts), `http_transient` and `http_rejected`.
`client.counters_raw.status_classes()` counts the responses to non-streaming
requests by status class.

The SDK logs to the `forward_sdk` logger: retries at debug level, and a warning
with the traceback when a hook, progress callback or `on_poll` callback raises.
Its own log messages do not include credentials or request or response bodies; a
traceback from your callback contains whatever your exception does.

## What the SDK caches, and what it does not

By default the client keeps two things.

- **The NQE query index**, the map of library path to query, because resolving a
  batch of paths would otherwise re-fetch the whole library once per path. Any
  write through `client.nqe.repo` clears it, `client.nqe.repo.index(refresh=True)`
  fetches it again, and it expires after `cache_ttl` seconds (default 60; `0`
  disables it).
- **Your roles, once `client.access()` has read them.** They do not expire, and
  `cache_ttl` does not apply. After a role change, call
  `client.access(refresh=True)`. A refused read (403 or 404) is not cached.

Snapshot answers are not cached unless you ask. `latest_processed` and
`latest_collected_id` ask Forward on every call. When a call that allows it has
no snapshot ID, from the call or the client, the SDK makes no extra request to
find one: it leaves the ID out, and Forward uses the latest processed snapshot at
the time of that request. That is deliberate.
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
`snapshot_cache_ttl`. It is off by default and takes two shapes, and which one
you want follows from what your run is doing.

**A run that holds one point in time wants `"lifetime"`.** Each lookup is
resolved once and the answer kept for as long as the client lives, unless
something clears it (see below). The setting covers only the two lookup methods.
A call with no snapshot ID still lets Forward choose each time, so pass the
resolved ID to the calls that must agree:

```python
client = ForwardClient.from_env(snapshot_cache_ttl="lifetime")

# The same answer each time it is asked, until the cache is cleared.
snapshot_id = client.snapshots.latest_processed_id()
if snapshot_id is None:
    raise SystemExit("no processed snapshot")
rows = client.nqe.query(
    "foreach d in network.devices select {name: d.name}",
    snapshot_id=snapshot_id,
)
```

**Repeated independent reads want a number of seconds.** A dashboard, a
long-lived service answering unrelated questions, anything where "latest" should
eventually mean something newer:

```python
client = ForwardClient.from_env(snapshot_cache_ttl=60)
```

Do not reach for the number when you meant the first thing. A TTL that expires
mid-run lets the next resolution return a different snapshot, so the run
straddles two moments. Nothing raises, and the data is real on both sides of the
expiry; it is simply from two different times. That is the same failure this
whole area keeps producing, an answer that looks entirely plausible.

The same value can be set with `FORWARD_SNAPSHOT_CACHE_TTL`. It covers `latest_processed` and `latest_collected_id`, keyed by the exact
question asked, so two different tag scopes stay two different answers. A
network with no processed snapshot is cached as an answer too, since re-asking
would spend the budget the setting exists to save.

Uploading, invalidating, reprocessing or collecting through
`client.snapshots` clears it, with either kind of setting. For other changes, such
as an upload from another process or a snapshot that finished while you were
running, call `client.snapshots.clear_cache()`.

Leave it off if "latest" genuinely has to mean latest. A stale snapshot does not
raise. It returns real data from the wrong moment, which is why this is a
setting rather than a default.

`client.counters` has `cache_hits` and `cache_misses`, shared by the snapshot
cache and the NQE index (roles are not counted). With `cache_ttl=0` each index
lookup still counts a miss; with snapshot caching off, snapshot lookups count
nothing. The request counts there would also reveal this kind of change.

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
just at whether the results are still correct. `attempts_per_minute` is an
average from this client's first attempt to its latest, not a sliding window: idle
time can hide a burst, and other processes using the same credentials are not
counted.

## Threads and event loops

A synchronous `ForwardClient` is safe to share across threads. An
`AsyncForwardClient` belongs to the event loop that created it.

Exceptions today: do not use `client.mcp` from several threads at once (see
[MCP](mcp.md)); while one thread writes through `client.nqe.repo`, another
thread's query-path lookup can raise `KeyError`; and while `access()` is reading
or refreshing roles, another thread can see new roles before the matching
impersonation flag is set.

## Source

- `src/forward_sdk/_async/client.py`
- `src/forward_sdk/_sync/client.py`
- `src/forward_sdk/_async/transport.py`
- `src/forward_sdk/progress.py`
- `src/forward_sdk/config.py`
- `src/forward_sdk/telemetry.py`
- `src/forward_sdk/_http.py`
- `src/forward_sdk/_async/throttle.py`
- `src/forward_sdk/_sync/throttle.py`
- `src/forward_sdk/_async/services/snapshots.py`
- `src/forward_sdk/_async/services/nqe_repo.py`
