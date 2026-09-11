# Forward SDK: after 0.1.10

Written 2026-09-11 against **forward-sdk 0.1.10** and the from-source Forward instance
(`1.0.0-260908-6aa8ff0c29ba`). Third and, on the evidence, last round.

Round 2 listed five gaps. 0.1.9 and 0.1.10 closed every one of them, and the SDK's
changelog was right to point out that two were already closed and one had never been
open — the SDK had `put_classic_devices` all along, under Forward's operation name, and I
did not find it. What follows is what this repo did with each, what was verified live,
and the one thing that is still not quite right.

---

## Closed, and how this repo now uses each

| Round-2 gap | 0.1.10 | Used by |
| --- | --- | --- |
| SNMP credentials | `snmp_credentials.list/create/update/delete` | `apply_forward_sources.py` — SNMP collection is **back on** for the nine EOS/XR/IOL devices; the PA-VM opts out in the netlab source |
| Collector binding | `collector_binding.get/set/unbind_network_collector` | `apply` binds from `FWD_COLLECTOR_USERNAME`; `collect` refuses unless `connectionStatus == CONNECTED` and prints health, version and any open issue |
| Classic-device upsert | `classic_devices.put_classic_devices` | reconciliation is upsert-in-place; no delete-and-re-add window |
| Publish 409 on unchanged corpus | handled in `nqe.repo.commit` → `CommitReport.skipped_paths` | see the one open item below |
| Newest non-predicted snapshot | `snapshots.latest_processed_id` excludes `PREDICT` by default; `list(exclude_triggers=)` | `forward_client.latest_collection` is now a thin wrapper over it |
| User event stream (low) | `user_events.stream()` | not needed; polling the task is enough here |
| Cloud accounts (low) | `cloud_accounts` (17 ops) | not needed since the Azure profile was removed |

Live verification, in order: `apply` matched the existing `containerlab-snmp` credential
by name (the community string comes back as the id of a stored secret, so name is the
only thing to match on), reported the binding, and upserted nine devices to turn SNMP on
— then reported `unchanged=10` on the second run. `collect` printed
`connection=CONNECTED health=UNHEALTHY` with the two LOW_MEMORY / LOW_DISK issues both
closed, and produced snapshot 664 with SNMP data on nine devices.

Two things that were true before are now true in the SDK rather than in this repo, which
is the point: the newest-non-predicted filter, and the collector pre-flight that used to
be a curl in a runbook.

---

## Still open: `publish(dry_run_snapshot_id=…)` raises on an unchanged corpus

`nqe.repo.commit` catches Forward's 409 `INVALID_CHANGE_PATH`, strips the unchanged
paths and retries — verified: the full 23-query corpus, unchanged, publishes as
`committed: 0, skipped: 23`, and leaves no drafts behind.

But `publish` calls `dry_run` *before* `commit` when `dry_run_snapshot_id` is given, and
`dry_run` is the same commit request with `dryRun=true`. Forward answers it with the
same 409, and `dry_run` does not have the handling. So:

```
publish(files, title=…)                              -> CommitReport(skipped_paths=23)   ok
publish(files, title=…, dry_run_snapshot_id="663")   -> ForwardConflictError 409         raises
```

The docstring recommends the dry run, and it is the right recommendation — it is what
catches a query that no longer compiles before it becomes a check that silently errors.
The recommended path and the no-op path cannot currently be used together.

**Ask:** apply the same unchanged-path stripping in `dry_run` (or have `publish` compute
the changed set once, before both). `discard_on_failure` does clean up after the raise —
drafts were 0 before and after — so nothing is left broken, it just fails.

**How we are handling it:** `publish_nqe_corpus.py` keeps comparing `repo.source(path)`
against the local text and submits only what differs, with the dry run. It is exact and
it makes the no-change case zero writes, so it would stay even after the fix.

---

## For the record

- `snapshots.latest_processed_id` documents the `REPROCESS`-vs-`COLLECTION` trap in its
  own docstring now, in the same words this repo used to carry. The wrapper here keeps
  the name `latest_collection` because it says what the caller means.
- `collector_binding.get_network_collector` returns `health.issues[]` with `startTime`
  and `endTime`; an issue with no `endTime` is open. The pre-flight prints only those.
- `put_classic_devices` takes the same body as `add_classic_devices` — a list of
  devices — and returns nothing useful; read the devices back to confirm.
- The manifest's `collector.username` had been `client` since the first render, which
  belonged to a different org and would have failed the bind with "Collector and
  network are in different orgs". It is now `collector.usernameEnv`, resolved from
  `.state/collector/binding.env`, because which collector serves a network is a fact
  about the instance, not the lab.
