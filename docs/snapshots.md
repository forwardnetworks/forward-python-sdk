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

### Processing exceptions

When a snapshot's numbers look wrong, the exceptions Forward raised while
processing it usually say why. Unpublished, and it needs the network's
`DEBUG_SNAPSHOTS` permission:

```python
for exc in client.snapshots.exceptions(snapshot_id):
    print(exc.exception_type, exc.occurrences, exc.devices)
    print(exc.stack_trace)

client.snapshots.exceptions_text(snapshot_id)  # the same, as one plain-text dump
```

`exceptions()` merges identical stack traces and says how often each was raised
and by which devices. `PARSING` and `MODELING` exceptions belong to devices;
`SNAPSHOT_GENERATION`, `REACHABILITY` and `SHERLOCK_INDEX` are snapshot-wide and
name no device. Parser-equivalence failures are reported here too. A snapshot
with none returns an empty list.

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

## Raw snapshot artifacts

Use the device service for files collected from a known device:

```python
files = client.devices.files("edge-01", snapshot_id=snapshot_id)
running_config = client.devices.file("edge-01", "configuration.txt", snapshot_id=snapshot_id)
```

For an artifact not owned by one modeled device -- a cloud provider's metadata
for an object no device represents, such as GCP `cloud_instances.gen` -- use the
snapshot-level file list and file methods:

```python
for name in client.snapshots.files(snapshot_id):
    if name.endswith(",cloud_instances.gen"):
        content = client.snapshots.file(snapshot_id, name)
```

`file` raises `ForwardNotFoundError` for a name the snapshot has none of.

## Advanced reachability

```python
client.snapshots.compute_advanced_reachability(snapshot_id)
```

For progress, there is a pollable job, which uses unpublished endpoints:

```python
job = client.snapshots.start_reachability_job(snapshot_id, network_id="101")
job.wait()
```

## Predicted snapshots

Forward creates and processes a snapshot for every Predict run. They are
processed like any other, so on a network using Predict the newest processed
snapshot is very often a prediction rather than a state the network was ever in.
Basing a change set on one predicts a change against a change.

`latest_processed` and `latest_processed_id` exclude them. Pass
`include_predicted=True` to get Forward's newest processed snapshot whatever
produced it.

The filter excludes `PREDICT` rather than requiring `COLLECTION`, and the
difference is not cosmetic. A reprocessed snapshot reports `REPROCESS` and is
real collected data: reprocessing is how a changed query or feature flag gets
picked up, and on one live network 20 of 43 processed snapshots were
reprocessed. Requiring `COLLECTION` would skip those and silently select the
previous collection, which during a change rehearsal is the snapshot taken while
the change was still applied. Everything downstream then reports green against
the wrong state of the network.

Forward does not publish `PREDICT` in its API description, so a deployment not
using Predict sees no difference either way.

`list()` takes the same idea as a filter: `exclude_triggers=["PREDICT"]` drops
predictions from a listing. Forward does not filter by trigger, so the SDK does,
and when a `limit` is also given the listing is fetched without a server limit
and cut afterwards, so you get up to `limit` matching snapshots rather than a
short page. That costs a larger response.
