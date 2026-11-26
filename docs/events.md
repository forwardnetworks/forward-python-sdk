# Live events

!!! warning "Unpublished"

    This stream is not part of Forward's published API.

Forward pushes Server-Sent Events to each user: per-device collection progress,
snapshot processing stages, change-set updates, collector tasks, connectivity
test results and more. The stream is the only source of per-device collection
detail; polling a collector task tells you about the task, not each device.

```python
async with AsyncForwardClient.from_env() as client:
    async for event in client.user_events.stream(types={"DEVICE_STATUSES_UPDATED"}):
        for device in event.data["deviceStates"]:
            print(device["deviceName"], device["collectionStatus"])
```

The sync client offers the same as a plain iterator.

## What arrives

Each `UserEvent` has a `type`, a decoded `data` payload, and `raw`, the `data:` text as
received (several `data:` lines are joined with newlines). `data` is `None`, with `raw`
kept and no error raised, when the payload is empty, is `0`, or is not JSON. A JSON
`null` payload also gives `None`; `raw` tells these cases apart. A message with no event
name (no `event:` line, or an empty one) has type `message`. The type is a string rather
than an enum because Forward adds names between releases; without `types=`, an unknown
name is delivered rather than dropped. The service has constants for many names
(`AsyncUserEventsService.NQE_PROGRESS` and so on, and the same on the sync
`UserEventsService`; see `src/forward_sdk/_async/services/events.py`). Neither those
constants nor the table below is a complete list, and the SDK does not check
payload shapes.

Payloads worth knowing:

| Type | Data |
| --- | --- |
| `DEVICE_STATUSES_UPDATED` | `{networkId, deviceStates: [{deviceName, collectionStatus, currentCommandStatus?, collectionStartTime?, collectionEndTime?, collectionFailed?, collectionError?}]}` |
| `SNAPSHOT_PROGRESS_REPORT` | `{networkId, snapshotId, stages: [{stage, operationState, startedAt?, updatedAt?}], done}` |
| `SNAPSHOT_PROCESS_EVENT` | `{networkId, snapshotId, type: STARTED or FINISHED}` |
| `CHANGE_SET_UPDATED`, `CHANGE_SET_DELETED` | A list of change-set ids |
| `COLLECTOR_TASK_QUEUED`, `_STARTED`, `_FINISHED` | `{taskId}` |
| `CLOUD_CONNECTIVITY_TEST_RESULT` | `{networkId, accountName, region?, testEpochMillis, error, unauthorizedCommands}` |
| `NQE_PROGRESS` | `{executionKey}` |
| `NQE_LIBRARY_COMMIT` | none |
| `USER_ROLES_CHANGED` | none (delivered as a string; not a constant on the service) |

Timestamps inside event payloads are epoch milliseconds.

## How it behaves

The stream never ends on its own, so iterate it inside a task you can cancel, or
return when you have seen what you were waiting for. A keepalive comment arrives
periodically and is consumed for you.

A dropped connection ends the iteration with a transport error. There is no
resumption: Forward keeps no cursor, so a caller that must not miss an event
re-reads the state it cares about after reconnecting rather than assuming the
gap was empty.

`types=` filters on the client: only the names you list are delivered (`None` or an
empty collection means no filter). The stream itself cannot be filtered, so every event
is still received; the argument saves a comparison, not bandwidth.

## Source

- `src/forward_sdk/events.py`
- `src/forward_sdk/_async/services/events.py`
