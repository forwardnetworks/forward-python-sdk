# Snapshots

A snapshot is Forward's model of your network at a point in time. Most reads are
against one.

## Choosing a snapshot

Leaving an optional `snapshot_id` as `None` means the client's default
`snapshot_id` if you set one, and otherwise the network's latest processed
snapshot. For the latter, the SDK omits the parameter and Forward chooses, so it
costs no extra request. The `device_tags` methods are the exception: they send
only an ID you pass them.

```python
client.snapshots.list(network_id="101")
client.snapshots.latest_processed("101")
client.snapshots.latest_processed_id("101")
```

`latest_processed` reads the snapshot listing rather than Forward's dedicated
endpoint for it, which is deprecated. The client can reuse a resolved "latest"
for `snapshot_cache_ttl` seconds (default `0`, off; `"lifetime"` never expires).
Uploading through the client clears the cache; call `client.snapshots.clear_cache()`
after an upload from another process or when a snapshot finishes processing
during the run.

"Latest" is the newest by creation date among processed snapshots, the rule
Forward uses for its own latest snapshot, so it agrees with the UI and `fwdctl`.
It is not the snapshot that finished processing last: reprocessing an older
snapshot updates its `processed_at` in place and must not make it the latest.
Drafts, and forks (snapshots with a parent) other than predictions, are never chosen.
Predictions are skipped unless you pass `include_predicted=True`.

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
that has a device whose collection completed (`FORWARD_CUSTOM` devices do not
count, and `include_tags` narrows the devices), raising `ForwardNotFoundError` if none does.

## Health

```python
metrics = client.snapshots.metrics(snapshot_id)
client.snapshots.is_complete(snapshot_id)  # no collection or processing failures
```

`metrics` raises `ForwardNotFoundError` ("Snapshot has no metrics", HTTP 404) for
a snapshot that is unprocessed, still processing or predicted. When looping over
`list()`, filter to `state == "PROCESSED"` first or catch the error.

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

`wait_until_processed` polls the network's snapshot listing every 15 seconds
(`poll_interval`), because Forward has no endpoint for a single snapshot. It
raises `ForwardExecutionError` if the snapshot ends `FAILED`, `CANCELED`,
`TIMED_OUT` or `RESTORE_FAILED`, and `ForwardTimeoutError` if your `timeout` (one
hour by default) passes first, in which case processing continues on Forward.
Pass `on_poll` to see each look, called as
`on_poll(snapshot, elapsed_seconds)`:

```python
client.snapshots.wait_until_processed(
    info.id, on_poll=lambda snap, secs: print(f"{secs:5.0f}s  {snap.state}")
)
```

A progress or poll callback that raises is logged and ignored. A bug in a status
line must not abort a transfer that has been running for an hour.

### Recording where a snapshot came from

`forward_sdk.provenance.describe_archive(path)` hashes a snapshot ZIP (SHA-256,
streamed) and lists its devices and files without unpacking it. Its `.note()`
is a one-line note naming the archive and its full hash, to upload with it, so
the ZIP and the snapshot can always be tied together:

```python
from forward_sdk.provenance import describe_archive

archive = describe_archive("collection.zip")
print(len(archive.devices), "devices,", archive.file_count, "files")
snapshot = client.snapshots.upload("collection.zip", note=archive.note("CHG-1234 pre-change"))
```

Files named `"<device>,<file>"`, the naming Forward lists a snapshot's raw files
under, are grouped by device, ignoring any directory in the entry's path;
anything else is listed in `other_files`. A missing file raises
`FileNotFoundError`, and a file that is not a ZIP raises
`ForwardConfigurationError`.

## Collecting

`collect()` starts a network collection and waits for the snapshot it produces:

```python
run = client.snapshots.collect(network_id="101")
run.snapshot_id  # the new snapshot; it may still be processing
client.snapshots.wait_until_processed(run.snapshot_id)
```

To wait for a collection someone else started, pass `start=False`: it joins the newest queued or running
collection on the network and raises `ForwardExecutionError` (outcome `NO_COLLECTION`) when there is none.

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
`ForwardTimeoutError`, and the collection carries on in Forward. Pass `on_poll`
to watch the task: `on_poll(task, elapsed_seconds)`.

The result also has `task_id` and `status`. With `wait=False`, `collect()`
returns without polling the task: `status` is `"QUEUED"` and `snapshot_id` is
`None`, so there is no snapshot to pass to `wait_until_processed`.

## Raw files

`files(snapshot_id)` lists every raw file Forward stored, named `"<device>,<file>"`. From there:

```python
name = "edge-1,configuration.txt"
client.snapshots.file(snap, name)  # the whole file as text
client.snapshots.file_info(snap, name)  # id, size, creation time; no content
client.snapshots.file_excerpts(snap, name, [(0, 9), (40, 49)])  # line ranges, 0-based, inclusive
client.snapshots.search_file(snap, name, "ospf")  # matching line numbers
client.snapshots.file_access()  # which devices' files you may read
```

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

Forward 26.11 also leaves predicted snapshots out of `list()` unless you pass
`include_predicted=True` (older Forward lists them either way). The SDK asks for
them where it has to: `wait_until_processed` looks a snapshot up in the listing,
and a Predict run waits on a predicted one.

`list()` also takes `state` and `limit`, and leaves out archived snapshots unless
you pass `include_archived=True`.

`list()` takes the same idea as a filter: `exclude_triggers=["PREDICT"]` drops
predictions from a listing. Forward does not filter by trigger, so the SDK does,
and when a `limit` is also given the listing is fetched without a server limit
and cut afterwards, so you get up to `limit` matching snapshots rather than a
short page. That costs a larger response.

## Reprocessing

```python
change = client.snapshots.reprocess(snapshot_id)
change.previous_state, change.state  # state is as of acceptance, not the final one
client.snapshots.wait_until_processed(snapshot_id)
```

`reprocess()` rebuilds a snapshot in place from its collected data; by default it
invalidates and requests processing in one call. `invalidate(snapshot_id)` only
discards the model, unless you pass `reprocess=True`. Until processing finishes
the snapshot cannot answer queries. Both are unpublished and return a
`SnapshotStateChange`.

## Reading an exported package

`forward_sdk.snapshot_package` reads the `sources.json` index of an exported
snapshot ZIP. It is a preview: the entry's shape was read from a real package,
not from Forward's source.

```python
from pathlib import Path

from forward_sdk.snapshot_package import read_sources, redact_log

sources = read_sources("snapshot.zip")
print(sources.devices_without_snapshots())

safe = redact_log(Path("cli-execution.log").read_text())
```

`devices_without_snapshots()` lists devices generated for a collection source
that have no snapshot of their own, which can explain a snapshot that processed
with no devices. Forward writes a Kubernetes bearer token into
`cli-execution.log` unmasked, so run `redact_log` before you store or share a
package log. It masks authorization headers and `Bearer` or `Basic` tokens; it
does not prove that no secret remains.

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

## Source

- `src/forward_sdk/provenance.py`
- `src/forward_sdk/snapshot_package.py`
- `src/forward_sdk/snapshot_state.py`
- `src/forward_sdk/collection.py`
- `src/forward_sdk/progress.py`
- `src/forward_sdk/_async/services/snapshots.py`
- `src/forward_sdk/_async/services/devices.py`
