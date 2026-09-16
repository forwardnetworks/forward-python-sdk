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

Round 4, below, closed the same day: two services the dashboard work needed, two rough
edges in `device_tags`, and a status read for advanced reachability.

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

## Closed in 0.1.14: dashboards, NQE panels, device tags, internet exposure

Written 2026-09-12 after adding a Forward Dashboard, device tags and a collection
schedule to the demo; closed the same day.

**`dashboards` / `nqe_panels`.** A Forward Dashboard is NQE panels embedded in a
per-network dashboard; both are now typed services (`client.dashboards`,
`client.nqe_panels`) matching the shapes this repo asked for -- `NqePanelDef.queryId` a
library `Q_`/`FQ_` id, `config` polymorphic on `TABULAR`/`METRIC`, dashboard `layout` a
whole-replace list of `ROW`/`PANEL` widgets. `scripts/apply_forward_dashboard.py`
replaces the hand-built dashboard: idempotent by dashboard name and panel name, query
ids from the NQE library index, and it normalizes the layout Forward echoes back
(server-assigned `id`/`createdAt`/`createdBy` on every widget, child order not
guaranteed to match position) before comparing, or a correct dashboard would look
changed on every run.

**`device_tags.list()`** now returns the tags (`{"tags": [...]}` unwrapped), not the
response envelope's keys -- `scripts/apply_device_tags.py` and the pre-flight now check
the whole tag set in one call instead of one `get()` per tag.

**`device_tags.add_to_devices()`** now sends `{"devices": [...]}`, not a bare array --
the batch `add_tags_to_devices` workaround is gone.

**Internet exposure has a status to read.** `internet_exposure.get_internet_exposure`
answers `{"error": None, "trafficReceivingInterfaces": [...]}` once the advanced-
reachability DAG has run, or `{"error": "PENDING_ADVANCED_REACHABILITY"}` (also
`INTERNET_NODE_NOT_DEFINED`, `REACHABILITY_COMPUTATION_DISABLED`) while it has not --
exactly the three states `InternetExposureError` names in Forward's own source.
`scripts/compute_internet_exposure.py` polls this instead of treating any
`vulnerability_analysis.get_vulnerabilities(internet_addressable=True)` exception as
"pending", which could not tell a real error from one.

**Security matrix and blast radius, used in anger.** `security_matrix.
get_anonymous_security_matrix(body={"name", "resourcePools", "timeoutMins"})` and
`blast_radius.get_blast_radius(body={"source": LocationFilter, "dstSubnets",
"timeoutSecs"})` are what `scripts/segmentation_delta.py` publishes on every prediction.
Shapes worth knowing: an ON_PREM pool needs `devices: []` and `vrfs: []` present even
when empty; a DEVICE_ZONE pool's `name` is computed by Forward ("<device> <zone>") and
ignored on write, so match results by position; `matrix[i][j].sampleQuery` is absent on
PARTIAL/NO_ROUTE cells and, on ACL_BLOCKED cells, names the destination as an
`InterfaceFilter` with no `ipv4_dst`, so a caller wanting a concrete flow fills the
missing side itself; blast radius returns only the *delivered* header regions
(`details[].headers.ipv4_dst`, comma-separated), so "reaches nothing" is an empty
list, not an error. Both need `ProcessingStage.REACHABILITY`, which predicted snapshots
reach; neither needs the DAG. Under a second each on 26 devices.

### Internet-node facts, for the record

Not a gap -- `internet_node` / `intranet_nodes` were already complete as of round 3 --
but Forward semantics that took a day to learn, because the API docs do not say them and
every one changes what goes in the body:

- **`subnets` are what the site advertises to the internet**, not the internet's ranges
  (`L3SyntheticWanConnection`: "subnets advertised by the gateway device"). The node
  forwards traffic *to* those prefixes down the uplink and treats everything else public
  as itself. `["0.0.0.0/0"]` therefore means "this site is the whole internet".
- **Public only.** `subnets` and `subnetsToExclude` refuse RFC 1918, CGNAT
  (100.64.0.0/10) *and* the RFC 5737 documentation ranges (198.51.100.0/24,
  203.0.113.0/24): "'subnets' can only contain public addresses".
- **`subnetAutoDiscovery: "NONE"` requires `subnets`; `advertisesDefaultRoute` is only
  valid with `IP_ROUTES`; `peerIps` only with `BGP_ROUTES`.**
- **The node drops private sources** (implicit "Drop private addresses" rules on the
  synthetic device), so an outbound intent from a private range is only true once
  something NATs it -- which is also what the internet does.
- **The uplink port stops being a segment.** A host learned on that port leaves the
  model; a next-hop *address* on the segment ends a path at the device that answers for
  it, an interface-only default route reaches the node.
- **Once stored, a synthetic device named after the node appears in every snapshot**
  (vendor `FORWARD_CUSTOM`) and cannot be removed by API. Estate reports filter
  `device.platform.vendor != Vendor.FORWARD_CUSTOM`.
- Checks can name the node: `{"type": "DeviceFilter", "value": "internet"}` is a valid
  `filters.from.location` / `.to.location` -- "from the internet" should be that, not a
  `SubnetLocationFilter` on the perimeter segment, which a rule could permit by name.


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

## Round 4 (2026-09-16)

- **A stored credential secret cannot be read back, and the SDK gives no way to know
  it has drifted.** `snmp_credentials.list_snmp_credentials` (and the CLI equivalent)
  echoes only the secret's numeric id, never its value, so an `apply` that matches an
  existing credential by name and leaves it alone has no way to notice the stored value
  no longer matches the manifest -- which is exactly what happened here: every
  SNMP-enabled device read "invalid snmp credentials" in the GUI while answering the
  manifest's community fine over the wire. There is no drift check possible client-side;
  the only safe idempotent behavior is to *rewrite* the credential on every apply
  (`update_snmp_credential`/the CLI equivalent), which is itself idempotent from
  Forward's side. `ensure_snmp_credentials` in `apply_forward_sources.py` now does this.
- **A device or endpoint's connectivity-test result is not exposed anywhere in the SDK.**
  The GUI's "Test connectivity" column reads from `POST .../connectivityTests/bulkStart`
  and `GET .../connectivityTests`, neither of which forward-sdk 0.1.14 wraps; and the
  `testResult`/`test_result` field that *is* on `ClassicDevice` and the `NetworkEndpoint*`
  read models is never populated by `get_classic_devices`/`get_network_endpoints` no
  matter how recently a test ran (confirmed live: field stays `None` across a bulk test
  covering all 26 devices, before and after). The only working proxy is a real
  collection: zero `collectionErrors` in the pre-flight, or an actual value where the
  credential is used, is stronger evidence than the GUI's own connectivity-test button.
- **`network_endpoints` and `endpoint_profiles` are complete in 0.1.14** -- create/get/
  delete/patch, CLI/HTTP/SNMP -- and needed no SDK changes to onboard the lab's Alpine
  "endpoint"-role hosts as Network Endpoints (a `CLI` profile naming `commandSets:
  ["UNIX"]`, one endpoint per host). One body-shape trap: `create_cli_network_endpoints`
  deserializes its body as a bare `ArrayList<NewCliNetworkEndpoint>` -- pass the list
  directly, not `{"endpoints": [...]}` (a natural guess from the read shape, and a 400
  with a Jackson stack trace if you get it wrong). Bulk `patch_network_endpoints` takes
  `{names, update}` -- one `update` object applied to every named endpoint -- which does
  not fit per-endpoint field differences (different hosts); delete-then-recreate by name
  is simpler and just as idempotent for that case.
