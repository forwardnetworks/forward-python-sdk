# Forward SDK: after 0.1.10

Written 2026-09-11 against **forward-sdk 0.1.10**, updated the same day for **0.1.11**, and the from-source Forward instance
(`1.0.0-260908-6aa8ff0c29ba`). Third round.

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

## Closed in 0.1.11: `publish(dry_run_snapshot_id=…)` on an unchanged corpus

`dry_run` now strips unchanged paths and retries, as `commit` does. Verified on 0.1.11:
the full unchanged corpus with a dry run publishes as `committed: 0, skipped: 23`, no
drafts left. Same-day turnaround.

## Open in 0.1.11: `publish` refuses every clean dry run of a *changed* query

Found while verifying the fix above, and it is older than the fix — the line dates from
the NQE layer's first commit.

`publish` decides whether the dry run found errors with:

```python
errors = report.get("newErrors") or []
if errors:
    raise ForwardConflictError(f"... would introduce {len(errors)} new error(s) ...")
```

But `newErrors` is `Map<QueryPath, Set<Diagnostic>>` (`CommitDryRunInfo.java`), keyed
by **every path in the dry run**, with an empty set for a path that compiles. So a
non-empty map means "the dry run looked at something", and `len(errors)` is the number
of paths, not errors. Verified live: one query edited by adding a trailing comment,
`dry_run` returns `{"newErrors": {"/ChangeAssurance/hygiene_interface_descriptions": []}}`,
and `publish(..., dry_run_snapshot_id="664")` raises "publishing 1 queries would
introduce 1 new error(s); nothing was committed".

The consequence is that the recommended path — dry run before commit — cannot publish
a changed query at all. The unchanged case works only because there is nothing to dry
run.

Two more things about the shape, from Forward's commit dialog
(`NqeCommitModal.svelte`), which is the behaviour to mirror:

- A diagnostic carries `severity`, `ERROR` or `WARNING`. The dialog disables Commit
  only when there is at least one `ERROR`; warnings turn the button into "Commit
  anyway". The Java comment on `newErrors` says "errors (or warnings)". A warning-only
  dry run should publish, or at least be distinguishable.
- The dialog also treats "snapshot not processed" from the dry run as "cannot check",
  and offers Commit anyway rather than failing.

**Ask:** `errors = {p: [d for d in ds if d["severity"] == "ERROR"] for p, ds in
report["newErrors"].items()}`, raise only if any list is non-empty, and put the paths
and messages in the exception — Forward's diagnostics are good ("Mismatched input
'this'. Reminder: record fields are comma-separated") and are the thing the caller
needs to see.

**How we are handling it:** `publish_nqe_corpus.py` carries `publish_checked()` —
stage, dry run, count ERROR diagnostics per path, commit, discard on any failure —
which is `publish` with the check done as the dialog does it. Verified: a clean edit
commits, a broken one is refused quoting Forward's diagnostic, no drafts survive a
refusal. It collapses back to one `publish` call when the fix lands.

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
