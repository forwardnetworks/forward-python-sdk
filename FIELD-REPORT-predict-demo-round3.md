# Forward SDK: after 0.1.10

Written 2026-09-11 against **forward-sdk 0.1.10**, updated the same day for **0.1.11** and **0.1.12**, and the from-source Forward instance
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

## Closed in 0.1.12: `publish` refused every clean dry run of a *changed* query

Found while verifying the 0.1.11 fix, and older than any of the reports: `publish`
tested the dry run's `newErrors` map for truth, but Forward keys that map by every path
examined (`Map<QueryPath, Set<Diagnostic>>`, empty set for a clean one), so a changed
query that compiled was refused as "1 new error". The SDK's own test fixture was a list,
the shape the parser believed, so the suite was green while both were wrong.

0.1.12 reads diagnostics by severity as Forward's commit dialog does: an `ERROR` refuses
the publish with Forward's message and path in the exception; a `WARNING` does not block
and is on `CommitReport.warnings`. It also adds `devices.upsert_classic()`, the batch
upsert followed by a read-back of the same names, because the upsert itself answers 201
with nothing.

Verified here on the live library, all four paths with a dry run: full corpus unchanged
→ 0 committed / 23 skipped; one clean edit → 1 committed / 22 skipped, no warnings, a
commit id; one broken edit → refused, "Mismatched input 'this'. Reminder: record fields
are comma-separated", no drafts left; restore → committed, source matches the file.

**What it removed from this repo:** the whole read-back-and-compare in
`publish_nqe_corpus.py`, and the local stage/dry-run/commit that stood in for `publish`
for one release. The publisher is one call. `apply_forward_sources.py` uses
`upsert_classic` and logs what Forward stored rather than what was sent.

One small thing is open after 0.1.12: the deployment-config scope, below.

## Open after 0.1.12: deployment configuration

`GET/PUT/DELETE /api/deployment-config/{PROPERTY}` (`DeploymentConfigController`) has no
SDK service. `configuration` covers org and global properties; deployment properties are
a third scope — `PROBE_LLM_AVAILABILITY`, `OUTBOUND_CONNECTIONS` and friends — and the
first of those is what makes the Forward AI page appear in the UI on an on-prem box
(off by default; when on, the server probes Bedrock once and caches the answer).
Round 1 noted `OUTBOUND_CONNECTIONS` "cannot be read or set this way at all"; this is the
route that reads and sets it.

```
GET    /api/deployment-config                      -> {PROPERTY: value, ...}
GET    /api/deployment-config/{PROPERTY}           -> {PROPERTY: value}
PUT    /api/deployment-config/{PROPERTY}?value=... -> {PROPERTY: newValue}
DELETE /api/deployment-config/{PROPERTY}           -> back to the default
```

**Ask:** `configuration.get_deployment_config_value(name)` /
`set_deployment_config_value(name, value)` / `clear_deployment_config_value(name)`, with
the same typed errors the org and global scopes have.

**How we are handling it:** a one-time documented curl in `DEMO-PREDICT.md`
(`PUT .../PROBE_LLM_AVAILABILITY?value=true`, 2026-09-12); the value persists in the
management store, so nothing in the repo needs it and no raw HTTP was added.

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
