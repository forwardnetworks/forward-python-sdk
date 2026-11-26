# Quickstart

## Get an API token

In Forward, open **Settings → Personal → Account** and create an API token. You
are shown the secret once. A token has two parts:

| Part | Looks like | Use as |
| --- | --- | --- |
| Access key | `abcd-efghi-jklm` | the username |
| Secret | 32 lowercase letters and digits | the password |

Authentication is HTTP basic. A login name and password also work, but a token
is preferred: it cannot sign in to the web interface, and it is not subject to
two-factor authentication or account lockout.

## Connect

```python
from forward_sdk import ForwardClient

client = ForwardClient(
    "https://fwd.app",
    username="abcd-efghi-jklm",
    password="your-token-secret",
)
```

Or from the environment, which uses the same variable names as the Forward
NetBox and Nautobot plugins:

```bash
export FORWARD_URL=https://fwd.app
export FORWARD_USERNAME=abcd-efghi-jklm
export FORWARD_PASSWORD=your-token-secret
export FORWARD_NETWORK_ID=101
```

```python
with ForwardClient.from_env() as client:
    print(client.version())
```

`FORWARD_NETWORK_ID` becomes the default network for calls that omit
`network_id`. The examples below pass it explicitly for clarity.

Reuse one client rather than creating one per call: it holds a connection pool,
and a sync job that reconnects for every request pays a TLS handshake each time.

## Find a network and a snapshot

```python
networks = client.networks.list()
snapshot = client.snapshots.latest_processed(networks[0].id)
if snapshot is not None:
    print(snapshot.id, snapshot.state, snapshot.processed_at)
```

"Latest" means newest by creation time, not most recently processed.
`latest_processed` skips drafts, forks and, unless you pass
`include_predicted=True`, predicted snapshots. It returns `None` when no
processed snapshot qualifies.

For queries, device reads and most other calls where `snapshot_id` is optional,
leaving it out means the client's default `snapshot_id` if you set one (with
`from_env`, `FORWARD_SNAPSHOT_ID`), and otherwise the network's latest processed
snapshot. The `device_tags` methods send only an ID you pass them. You rarely
need to look one up explicitly. Without a fixed ID, though, two calls can see
different snapshots if a new one finishes processing between them. A job that
needs one point in time should look up an ID once and pass it to every call, or
set it as the client's `snapshot_id`.

## Run a query

```python
rows = client.nqe.query(
    "foreach device in network.devices select {name: device.name, model: device.model}",
    network_id="101",
)
for row in rows:
    print(row["name"], row["model"])
```

Rows are plain dictionaries whose keys are the column names your query selected.

## List devices

```python
for device in client.devices.iter(network_id="101", vendor="CISCO"):
    print(device.name, device.platform, device.os_version)
```

## Next

- [Configuration](configuration.md): timeouts, retries, rate limiting, TLS.
- [The NQE guide](nqe/index.md): background executions, streaming, diffs.
- [Errors](errors.md) and [availability](gating.md).

## Source

- `src/forward_sdk/_sync/client.py`
- `src/forward_sdk/_sync/services/networks.py`
- `src/forward_sdk/_sync/services/snapshots.py`
- `src/forward_sdk/_sync/services/nqe.py`
- `src/forward_sdk/_sync/services/devices.py`
- `src/forward_sdk/config.py`
