# Snapshots

A snapshot is Forward's model of your network at a point in time. Most reads are
against one.

## Choosing a snapshot

Passing `snapshot_id=None` anywhere in the SDK means the network's latest
processed snapshot. Forward implements that by omitting the parameter, so it
costs no extra request.

```python
client.snapshots.list(network_id="101")
client.snapshots.latest_processed("101")
client.snapshots.latest_processed_id("101")
```

`latest_processed` reads the snapshot listing rather than Forward's dedicated
endpoint for it, which is deprecated.

### Latest *collected*

A snapshot can be fully processed and still contain nothing you can use, for
instance when collection failed for every device in scope. A job that needs real
data wants the newest snapshot that actually collected devices:

```python
snapshot_id = client.snapshots.latest_collected_id(
    "101",
    include_tags=["production"],
    scan_limit=10,
)
```

This probes candidates newest-first with a one-row query and returns the first
that has devices in scope, raising `ForwardNotFoundError` if none does.

## Health

```python
metrics = client.snapshots.metrics(snapshot_id)
client.snapshots.is_complete(snapshot_id)  # no collection or processing failures
```

## Uploading

```python
info = client.snapshots.upload("capture.zip", network_id="101", note="nightly")
client.snapshots.wait_until_processed(info.id, network_id="101")
```

Several archives can be merged into one snapshot, so long as they do not
describe the same device:

```python
client.snapshots.upload(["site-a.zip", "site-b.zip"], network_id="101")
```

By default Forward answers as soon as the upload lands and continues unpacking
in the background. Pass `wait=True` to hold the request open instead.

`wait_until_processed` polls the network's snapshot listing, because Forward has
no endpoint for a single snapshot. It raises `ForwardExecutionError` if
processing fails, and `ForwardTimeoutError` if your deadline passes first, in
which case processing continues on Forward.

## Exporting

```python
client.snapshots.download(snapshot_id, "snapshot.zip")
client.snapshots.download(snapshot_id, "configs.zip", only="CONFIG")

for chunk in client.snapshots.export(snapshot_id):
    ...
```

## Advanced reachability

```python
client.snapshots.compute_advanced_reachability(snapshot_id)
```

For progress, there is a pollable job, which uses unpublished endpoints:

```python
job = client.snapshots.start_reachability_job(snapshot_id, network_id="101")
job.wait()
```
