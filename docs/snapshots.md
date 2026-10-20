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

The archives are streamed from disk, so a multi-gigabyte one costs no memory (a
400 MiB upload moved the process's peak memory by 3 MiB). To show progress, pass
`on_progress`, called as `on_progress(bytes_sent, total_bytes)` across all the
archives:

```python
client.snapshots.upload(
    "capture.zip",
    network_id="101",
    on_progress=lambda sent, total: print(f"\r{sent / total:.0%}", end=""),
)
```

`wait_until_processed` polls the network's snapshot listing, because Forward has
no endpoint for a single snapshot. It raises `ForwardExecutionError` if
processing fails, and `ForwardTimeoutError` if your deadline passes first, in
which case processing continues on Forward. Pass `on_poll` to see each look,
called as `on_poll(snapshot, elapsed_seconds)`:

```python
client.snapshots.wait_until_processed(
    info.id, on_poll=lambda snap, secs: print(f"{secs:5.0f}s  {snap.state}")
)
```

A progress or poll callback that raises is logged and ignored. A bug in a status
line must not abort a transfer that has been running for an hour.

## Collecting

`collect()` starts a network collection and waits for the snapshot it produces:

```python
run = client.snapshots.collect(network_id="101")
run.snapshot_id  # the new snapshot; it may still be processing
client.snapshots.wait_until_processed(run.snapshot_id)
```

This puts the collector-task calls (`collector_tasks.add_collector_task` and
`get_collector_task`) together, since the task carries no snapshot id. The new
snapshot is the one that appears in the network's listing once the task
succeeds; predictions are ignored, since a change rehearsal may create one
meanwhile. Forward marks the task succeeded and then creates the snapshot in the
same step, so there is a short gap, which `snapshot_timeout` (two minutes by
default) covers. Forward can create a snapshot even when the task ended `FAILED`
because every device failed; the error does not look for it, so check the listing.

`collect()` has been checked against Forward's source and against recorded response
shapes, not against a live collection. A task that fails, times out or is cancelled raises
`ForwardExecutionError` with the task's status. Your own `timeout` raises
`ForwardTimeoutError`, and the collection carries on in Forward. Pass
`wait=False` to return as soon as the task is queued, and `on_poll` to watch the
task: `on_poll(task, elapsed_seconds)`.

## Exporting

```python
client.snapshots.download(snapshot_id, "snapshot.zip")
client.snapshots.download(snapshot_id, "configs.zip", only="CONFIG")

for chunk in client.snapshots.export(snapshot_id):
    ...
```

Export only some devices, or obfuscate sensitive data, with the subset arguments
(they work on `download()` too):

```python
client.snapshots.download(snapshot_id, "two.zip", include_devices=["fw01", "pa*"])
client.snapshots.download(snapshot_id, "all-but.zip", exclude_devices=["lab-*"])
client.snapshots.download(snapshot_id, "safe.zip", obfuscation_key="k", obfuscate_names=True)
```

`include_devices` and `exclude_devices` are exclusive. Forward refuses a subset
that would be empty (HTTP 400, "would result in an empty snapshot"). A subset
limited with `only="CONFIG"` is an empty archive for a device with no CLI
configuration files, such as a cloud account, which is not an error. Observed on
one network: a cloud account's subset was empty, and a device with CLI
configuration gave a real archive, so `only` does combine with the subset.

`on_progress(bytes_received, total_bytes)` reports a download as it arrives. The
total is `None` when Forward sends no length, which it may not for an archive
built on the fly. A server that refuses the representation the client asks for
answers 406; the SDK asks for any media type on exports, and a 406 now says which
`Accept` header was refused.

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

## Progress, logs and retention

```python
client.snapshots.progress(snapshot_id)  # a snapshot that is still processing
client.snapshots.process_estimate(snapshot_id)  # Forward's estimate of how long
client.snapshots.processing_queue()  # what is waiting for capacity
client.snapshots.collection_exceptions(snapshot_id)

for chunk in client.snapshots.logs(snapshot_id, device_name="edge-01", level="ERROR"):
    ...
for chunk in client.snapshots.collection_log(snapshot_id):
    ...
```

The logs are large (one live snapshot's came to hundreds of megabytes), so they
stream, and `device_name` and `level` narrow them on Forward's side.

`favorite()` and `unfavorite()` keep a snapshot out of, or return it to, the
network's automatic clean-up, and `set_note()` replaces its note. The clean-up
itself is the retention policy:

```python
policy = client.snapshots.retention_policy()
client.snapshots.set_retention_policy({**policy.model_dump(by_alias=True), "lastWeek": 10})
client.snapshots.preview_retention()  # what the policy would delete; deletes nothing
```

Every field of the policy is required when you set it. The preview is the only
call that reaches Forward's trigger route, and it always passes `dryRun=true`.
Without that, the same route deletes the snapshots. These were checked against
Forward's source and, for the reads, a live account. The preview itself needs the
DELETE_SNAPSHOT permission and was not run, nor were setting the policy and
favouriting.

## Cancelling a snapshot's jobs

```python
result = client.snapshots.cancel_jobs(snapshot_id)
result.matched, result.canceled, result.already_done
```

Reads Forward's active jobs and cancels each row that belongs to the snapshot:
snapshot-level work such as Protocol Modeling, merging, NQE, diffs and path searches.
Per-device processing rows are network-level and carry no snapshot id, so they are never
matched. It needs Forward's `ADMINISTER_SYSTEM` permission, and it cancels **everyone's**
jobs on the snapshot, not only yours; cancelling work a prediction is waiting on can fail
that prediction. Cancelling is cooperative, so read the active jobs again to confirm.

A single job that had already finished is counted in `already_done`. A group of jobs that
Forward answers 404 for raises, since that is not known to mean they finished. Each job's
`cancelLink` is already percent-encoded by Forward and is used as given. From Forward's
source and the Go SDK; not run against a live instance, which needs that permission.
