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

Round 4, below, is open after 0.1.13: two services the dashboard work needs, and two
rough edges in `device_tags`.

## Closed in 0.1.13: deployment configuration

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

**Closed in 0.1.13**, same day: `configuration.get_deployment_config()` /
`get_deployment_config_value(property=)` / `set_deployment_config_value(property=, value=)` /
`clear_deployment_config_value(property=)`. Verified live: the read returns
`{'PROBE_LLM_AVAILABILITY': True}`, and the property is now in
`apply_predict_feature_flags.py`'s managed set, so `make forward-flags` reports and sets it.
The curl it replaced was the last raw request in any runbook here.

**How it was handled before:** a one-time documented curl in `DEMO-PREDICT.md`
(`PUT .../PROBE_LLM_AVAILABILITY?value=true`, 2026-09-12); the value persists in the
management store, so nothing in the repo needs it and no raw HTTP was added.

## Round 4 — open after 0.1.13

Written 2026-09-12 while adding a Forward Dashboard, device tags and a collection
schedule to the demo.

### 1. No `dashboards` / `nqe_panels` services

A Forward Dashboard is NQE panels embedded in a per-network dashboard; both are plain
REST (`DashboardController.java`, `NqePanelController.java`) and neither has an SDK
service, so the demo's dashboard was built by hand in the UI.

```
GET    /api/nqe-panels                                  -> [NqePanel]
POST   /api/nqe-panels                                  NqePanelDef -> NqePanel (201)
PATCH  /api/nqe-panels/{panelId}                        NqePanelPatch
DELETE /api/nqe-panels/{panelId}
POST   /api/nqe-panels?action=delete                    {panelIds: [...]}
POST   /api/networks/{net}/nqe-panels?resultKey=...     metrics for a panel on a snapshot
POST   /api/networks/{net}/nqe-panels?action=preview    preview a definition

GET    /api/networks/{net}/dashboards[?type=DEFAULT]    -> [Dashboard]
GET    /api/networks/{net}/dashboards/{id}
POST   /api/networks/{net}/dashboards                   {name, description?} -> id
PATCH  /api/networks/{net}/dashboards/{id}              {name?, description?, layout?: [widget]}  (layout replaces all)
POST   /api/networks/{net}/dashboards?action=removePanels {panelIds}
DELETE /api/networks/{net}/dashboards/{id}
GET/PATCH /api/networks/{net}/dashboards/{id}/display-settings
```

`NqePanelDef`: `name` (required), `displayName?`, `description?`, `queryId` (a
GlobalQueryId -- an org-library `Q_...` or a Forward-library `FQ_...`; query *text* is
not accepted), `queryParams?`, `config` polymorphic on `type`:
`{"type":"TABULAR","columnOrder":[...],"visibleColumns":[{"name","width"}],
"frozenColumns":[...]}` or `{"type":"METRIC","columnName","aggregation","unit?",
"decimalPrecision?","thresholds?":[...]}`.

Widgets: `{"type":"ROW","x","y","w","h","title","children":[...]}` and
`{"type":"PANEL","x","y","w","h","panelId":"NQE_PANEL_<id>"}`; the NQE panel widget
also takes `override` (`displayName`, `queryParams`, `config`) and echoes a
server-assigned per-embedding `id`. Panels are gated on the org property
`NQE_DASHBOARD_PANELS` (default false).

**Ask:** `nqe_panels.list/create/update/delete/preview/metrics` and
`dashboards.list/get/create/update/delete` with the widget shapes typed. The demo
then replaces a hand-built dashboard with `scripts/apply_forward_dashboard.py`,
idempotent by dashboard name and panel name, with query ids from the publisher's index.

### 2. `device_tags.list` returns the response wrapper's keys

`GET /networks/{net}/device-tags` answers `{"tags": [...]}` (`DeviceTags`); the SDK does
`list(payload or [])` and returns `['tags']`. Same for `with_devices=True`
(`DeviceTagsWithDevices`). `get(tag_name)` is correct, so the demo checks each tag it
wants by name.

### 3. `device_tags.add_to_devices` sends the wrong body

It posts a bare JSON array to `POST /networks/{net}/device-tags/{tag}`; Forward wants
`DeviceSet`, `{"devices": [...]}`, and answers 400 "Cannot deserialize value of type
DeviceSet from Array value". The batch `add_tags_to_devices(body={"devices": [...],
"tags": [...]})` (`?action=addBatchTo`, `DevicesAndTags`) works and is what the demo uses.
`remove_from_devices` likely shares the bug (not exercised).

### 4. For the record: internet exposure needs an Internet node, and TEST-NET-3 is not "public"

Not an SDK gap, a Forward modelling fact met on the way. `vulnerability_analysis.
get_vulnerabilities(internet_addressable=True)` answers 400 `INTERNET_NODE_NOT_DEFINED`
until `internet_node.add_internet_node_connection` has been called. A connection on
the firewall's untrust port with `subnets: ["0.0.0.0/0"]` swallowed the `internet`
host at 203.0.113.10 (paths became `DELIVERED_TO_INCORRECT_LOCATION`, 20 intents red);
`subnetsToExclude: ["203.0.113.0/24"]` is refused -- "can only contain public
addresses", and RFC 5737 space is not public -- and the 24-prefix complement did not
help either. The demo keeps its host-based internet intents and no Internet node.
Also: `add_internet_node_connection` with `subnetAutoDiscovery: "NONE"` requires
`subnets`, and `advertisesDefaultRoute` is only valid with `IP_ROUTES`.



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
