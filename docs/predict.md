# Rehearsing a change

Forward Predict answers "what would the network look like if I made this
change" without touching a device. The SDK exposes the whole loop: stage device
changes against a base snapshot, commit them, run Predict to produce a snapshot
of the network as it would be, then compare that snapshot to the base.

Most endpoints here are unpublished (change sets, diffs, firewall Predict, BGP
advertisements); the checks, NQE and configuration routes it also uses are in
Forward's API description. The unpublished shapes come from Forward's server
source. Treat a part as run against an instance only where it says it was seen
live, observed or ran live (the version is named where it was recorded). The traps below cost a real
integration real time before they were written down.

```python
from forward_sdk import ForwardClient

client = ForwardClient.from_env(network_id="115")

base = client.snapshots.latest_processed()  # excludes predictions; see below
change = client.change_sets.create("Core uplink change", snapshot_id=base.id)

change.set_commands("dc-core", "interface Ethernet1\n shutdown")
change.commit("staged for review")
predicted = change.predict_and_wait("rehearsal 1")

diffs = client.snapshot_diffs
connectivity = diffs.wait_for_subnet_connectivity(base.id, predicted.id)
print(connectivity.newly_isolated_subnet_pairs, "site pairs lose connectivity")
print(diffs.counts(base.id, predicted.id))  # what changed, per area
```

## Choosing the base

`latest_processed` excludes predicted snapshots by default. Forward processes a
snapshot for every Predict run, so once Predict is in use the newest processed
snapshot is very often a prediction rather than a state the network was ever
in, and basing a change set on one predicts a change against a change. On one
Predict-enabled network, four of five processed snapshots were predictions.

The filter excludes `PREDICT` rather than requiring `COLLECTION`, and the
difference matters: a reprocessed snapshot reports `REPROCESS` and is real
collected data. See [Snapshots](snapshots.md#predicted-snapshots).

## Staging changes

`set_commands` sends the CLI text as plain text, not JSON. It replaces whatever
was staged for that device. `validate_commands` checks text without staging it,
and `draft()` returns what is staged or `None` when nothing is.

**PAN-OS firewalls cannot be changed this way.** A change set of `set` lines on
a PAN-OS device produces a predicted snapshot that fails to process with
"Device dc-fw OS pan_os not supported". Forward's own documentation lists CLI
commands as unsupported for PAN-OS and a configuration file as supported, and its
configuration generator has no PAN-OS vendor, so staging accepts the text and
prediction is where it fails. Firewall changes go through structured rule edits
instead:

```python
fw = client.firewall_predict
diff = fw.get_security_rules_diff(network_id="115", change_set_id=change.id, device_name="dc-fw")
editable = [r for r in diff.rulebases if r.editable]  # only LOCAL / PRIMARY
rule = diff.entries[0].b.definition  # the shape a write takes
fw.add_security_rule(
    network_id="115",
    change_set_id=change.id,
    device_name="dc-fw",
    scope_id="LOCAL",
    rulebase_id="PRIMARY",
    body={"definition": {**rule.model_dump(by_alias=True, exclude_none=True), "name": "allow-dns"}},
)
```

Two more PAN-OS traps, the first from Forward's source and the second observed live by an integration: a
`DROP` rule predicts as `DENY`, because Forward models every non-allow action as deny; and a rule whose action is
`RESET_CLIENT`, `RESET_SERVER` or `RESET_CLIENT_AND_SERVER` stages fine but breaks the prediction, which then
shows an empty rulebase with every flow permitted. Use `DENY` when predicting.

Only scope `LOCAL` with rulebase `PRIMARY` accepts writes; everything else
Forward returns is read-only and refuses with "Rulebase 'X' in scope 'Y' is not
editable". The feature is gated on the `FIREWALL_PREDICT` org property. The
definition objects the diff returns are the shape the write operations take, so
read one and mirror it rather than building one from scratch.

## Predicting

`predict()` starts the run and returns the new snapshot's id. `predict_and_wait()`
also waits for it to finish processing and returns the processed snapshot,
whose id is the `after` side of every diff. `note` is required on both, because
Forward requires it: omitting it is a 400 there, not a convenience here.

A snapshot-ready webhook fires the instant the prediction finishes. If you drive
a pipeline from that webhook, read the next section before trusting the first
diff you fetch.

## Reading the diffs

The diffs state blast radius without being asked a question. Three behaviours
are worth knowing before reading them.

**Subnet connectivity is computed after the snapshot is ready.** The first read
after a prediction finishes very often lands inside the window before the
computation starts, and comes back with every count at zero and
`is_partial_result` true. Those zeros mean "not finished". They are
indistinguishable from "nothing changed" unless you check the flag, and a
webhook-driven pipeline hits this almost every time. `wait_for_subnet_connectivity`
polls until the flag clears.

**NQE check diffs answer early too.** `GET /diffs/{a}/{b}/checks?type=NQE`
returns at once with rows still marked `PROCESSING`; the other check types block
in Forward until computed. `diffs.wait_for_check_diffs(base.id, predicted.id,
["NQE"])` reads again until no row (or side of one) is processing, and
`max_poll_interval` makes the wait back off.

**A settled zero pairs count means no forwarding change.** Forward compares
only the subnet pairs that a route, ACL or NAT change between the two snapshots
could affect, so `total_subnet_pairs` of zero means it found no such change: a
real "no reachability change". The one degenerate case is a network with no
device subnets, which also gives zero. Seen live on 26.10.6: a change whose route diff
was empty gave zero pairs, six route changes gave 42, and restoring a link gave
168 with 14 subnet pairs gained. (Earlier versions of this page said zero meant
the network had no locations; that was wrong.)

**Routing loops and vulnerabilities** are not among the `counts()` areas below.
`diffs.routing_loops(base.id, predicted.id)` gives `count` and `complete`;
`complete` false means the search stopped early, so `count` is a lower bound and a
zero does not clear the change. `diffs.vulnerability_counts()` gives
`new_cve_count`, the only field present on every deployment.
`routing_loops_bidirectional()` and `vulnerabilities_bidirectional()` return
`warnings` and `improvements`; the latter counts with `cve_count`, not
`new_cve_count`. `file_summary()` reports the devices and locations touched,
directly and indirectly.

**Gating differs per diff.** Connectivity needs the `LOCATION_CONNECTIVITY_DIFFS`
org property. The bidirectional views, which split a change into what it makes
worse and what it improves, need `PREDICT_DIFF_INSIGHT_ENHANCEMENTS`. The
one-directional routing-loop and vulnerability counts need neither. An org's
own admin cannot turn `LOCATION_CONNECTIVITY_DIFFS` on: `set_org_config_value`
answers 403 "Permission denied for location_connectivity_diffs" (seen live on
26.10.6). A Forward support account sets it with `set_org_scoped_config_value`.

**Per location pair**, `diffs.wait_for_location_pairs(base.id, predicted.id,
"ADDED")` waits for the comparison to settle and then reads the changed subnet
pairs per location pair. Read too early, Forward answers `{"counts": []}`, which
looks the same as no change. It returns `.pairs` and the settled `.comparison`;
`.no_forwarding_change` is true when Forward found no route, ACL or NAT change
to compare at all (`.nothing_compared` is the 0.1.38 name for it). Devices with
no location are not counted in `.pairs`. `type` is `ADDED`, `DELETED` or `MODIFIED` (case and surrounding spaces are
ignored); anything else, `ALL` included, raises `ValueError` before any request.
`loc` restricts the result to one location. These waits stop after 300 seconds
(600 for `wait_for_check_diffs`) with `ForwardTimeoutError`, whose `.partial` holds
the last result read.

**Locations** for that comparison: `client.networks.ensure_locations({"Dayton
DC": {"lat": 39.81, "lng": -84.27}})` creates or updates locations by name
(trimmed, ignoring case, as Forward matches them) and sends only the read when
they already match, and `client.networks.set_device_locations({"edge-1":
"Dayton DC"})` places devices by location name. The first placement pins a
device that a dynamic rule had placed, as any move in Forward does. Forward
places only devices it knows as collection sources: on an upload-only network,
register them first with `client.networks.register_devices(["r1", "r2"])`, which
adds them as classic devices Forward never collects from (the name doubles as
the host unless you pass a name-to-host mapping) and skips the ones it knows.
Otherwise Forward refuses the whole update with a 400, and
`error.unrecognized_devices` lists the devices it did not know.
`ensure_locations` and `set_device_locations` ran live on 26.10.6
(2026-11-15, the AutoCon6 workshop): four locations created, a second run
sending only the read, and devices placed by name. `register_devices` has not
run live.

`counts()` returns every per-area count at once, eight requests, keyed by area.
`complete` false on any of them means the count is a lower bound so far.

**The detail behind a count** is on the same service: per-device interface,
ACL, ARP, MAC and NAT differences, VLAN and topology changes, changed files,
added and removed devices, and check results. Each returns Forward's diff
entries, `a` before and `b` after with a `diffType`.

The same differences are also one NQE query away, with columns you choose,
since every one of those areas is in the NQE data model and `nqe.diff()`
compares any query across two snapshots:

```python
from forward_sdk import QueryRef

changed = client.nqe.diff(
    QueryRef.by_path("/Rehearsal/Interfaces"),  # foreach d in network.devices
    before=base.id,  # foreach i in d.interfaces
    after=predicted.id,  # select {device: d.name, name: i.name, admin: i.adminStatus}
)
```

Use the REST diffs when you want what the Forward UI shows; use an NQE diff
when you want your own columns or a filter Forward's view does not offer.

## Checking gating before you start

The properties above are org settings, and reading them has a trap of its own.

```python
from forward_sdk import config_value

cfg = client.configuration
config_value(cfg.get_global_config_value(property="FIREWALL_PREDICT"))  # False
config_value(cfg.get_org_config_value(property="FIREWALL_PREDICT"))  # True
```

The global route reports the fixed default and never an org's override, so it
keeps saying `False` after the org has been set to `True`. That is correct
behaviour and easily mistaken for a failed write. The org-effective routes
report what governs. Read from the scope you care about: `get_org_config_value`
for the calling user's org, or `get_org_scoped_config_value` for a named one.

To see everything at once rather than one property, list a scope. `filter` is
`OFF` (every property), `CONFIGURABLE` (those an org administrator may change),
`CONFIGURED` (those with a value set in this scope), `OVERRIDDEN` or `NONDEFAULT`.
The routes disagree about the default, so the SDK sends none: it is
`CONFIGURABLE` for `get_org_config_values()` and `get_global_config_values()`,
and `OFF` for `get_org_scoped_config_values(org_id=...)`, which needs a Forward
support account.

Forward does not say where a value comes from, so `describe_properties()` works
it out from three listings:

```python
from forward_sdk import describe_properties

cfg = client.configuration
sources = describe_properties(
    cfg.get_org_config_values(filter="OFF"),  # what governs
    cfg.get_org_config_values(filter="CONFIGURED"),  # what the org set
    cfg.get_global_config_values(filter="CONFIGURED"),  # what an administrator changed
)
sources["firewall_predict"]  # PropertySource(value=True, source="org")
```

The org wins if it set the property, then the global scope; otherwise the value is
the one compiled into Forward, which no route exposes on its own, so `default`
means "nobody changed it". On one organization this gave 15 `org`, 6 `global` and
175 `default` out of 196. The global scope had 11 set, but 5 of those were also
set by the org, so they report as `org`.

Deployment properties are a third scope, and the other routes cannot reach
them. `OUTBOUND_CONNECTIONS` gates webhook creation and cloud tests;
`PROBE_LLM_AVAILABILITY` is what makes Forward AI appear on a self-hosted
deployment. `get_deployment_config()` lists every one with its value, and the
`*_deployment_config_value` operations read, set and clear one. Setting them
needs a Forward administrator, or an org administrator on a self-hosted
deployment for the properties marked configurable there.

Network properties are another scope, a small subset of the org properties,
written with `set_network_config_property(property=..., value=...)` and cleared
with `delete_network_config_property(property=...)`.
`forward_sdk.configuration` has constants for those it knows, such as
`ADVANCED_REACHABILITY_ANALYSIS`. An unknown name, including an org-only one such
as `location_connectivity_diffs`, is a 400 (`error.unknown_network_property`).

Writes follow the same split. Properties declared org-scoped, including every
Predict gate, have an immutable global default: the global write refuses with
"Permission denied. Global default value cannot be changed for X", and the org
admin route refuses too because they are not org-admin-configurable. A Forward
support account sets them per org with `set_org_scoped_config_value`, and reads
them back from the same route.

## Intent checks

Critical flows can be checks Forward judges on every later snapshot,
predictions included, so a change that breaks one shows as a failing check.
`forward_sdk.checks` builds the definitions from a trace query:

```python
from forward_sdk import checks
from forward_sdk.trace import ChainHop, Endpoint, PathQuery, device, subnet

app = PathQuery(
    from_=Endpoint(subnet("10.10.10.10/32")),
    chain=[ChainHop("through", device("r2"))],
    to=Endpoint(subnet("10.20.20.20/32"), headers=[{"ip_proto": ["6"], "tp_dst": ["8080"]}]),
)
client.checks.add_check(
    snapshot_id=base.id,
    body={"definition": checks.existential(app), "name": "APP via r2"},
    persistent=True,
)
client.checks.add_check(
    snapshot_id=base.id, body={"definition": checks.nqe_check("Q_...")}, persistent=True
)
```

A `subnet()` in `to` returned no results in `trace.py`'s live probe of trace queries
(Forward 26.11, a two-device lab); whether a check
behaves the same is not confirmed, so read the check's result before relying on it.

`existential` passes when the flow has a path, `isolation` when it has none,
and `reachability` when all of it is delivered. An NQE check without
parameters takes its name from its query, so Forward answers 400 to a `name`
beside it; one with parameters needs a name; neither takes a note or tags
(`error.check_field_not_allowed`). A Predefined check takes no `name`, only path
checks (`existential`, `isolation`, `reachability`) take `tags`, and only
Existential checks take `perfMonitoringEnabled`.
`checks.validate_new_check(body)` applies these rules without a request;
`add_check` does not call it, so call it yourself. The three path builders also
take `noise_types` and `header_fields_with_defaults`, and `existential` takes
`return_path` (`"ANY"` or `"SYMMETRIC"`) to require a path back. Existential, Isolation and NQE checks of this shape ran
live on 26.10.6 (the AutoCon6 workshop).

## Webhooks

An event-driven pipeline exists because predicted snapshots fire
`SNAPSHOT_READY` like any other. Three event types exist: `SNAPSHOT_READY`,
`NQE_VERIFICATION_FAILURE` and `INTENT_VERIFICATION_FAILURE`.

For `SNAPSHOT_READY`, `eventParams.networkIds` must be present. An empty list
means every network in the org; omitting the key is refused with a 400 that
carries no message, so it looks like a mystery. Creating a webhook needs the
`OUTBOUND_CONNECTIONS` deployment property.

## BGP advertisements and directories

A change set can also inject or withdraw BGP routes at a device, as a Predict
input, through `client.bgp_advertisements`. The key of an advertisement is the
peer address, the prefix and the VRF; an injected route needs a next hop, an
origin and, for EBGP, a non-empty AS path. The draft's `?view=diffs` reports
each advertisement as `INJECTED`, `MODIFIED`, `DUPLICATED`, `WITHDRAWN` or
`ABSENT`.

Forward accepts a withdrawal of a key the base snapshot does not have, and
ignores it when the change set runs; the only sign is an `ABSENT` row. Read the
diffs back after staging:

```python
from forward_sdk.errors import ForwardBgpKeyMatchedNothingError
from forward_sdk.models import BgpAdvertisementKey

key = BgpAdvertisementKey(vrf="default", external_peer="198.51.100.2", prefix="192.0.2.0/24")
try:
    report = change_set.verify_bgp_staging("edge-1", staged=[advertisement], withdrawn=[key])
except ForwardBgpKeyMatchedNothingError as error:
    print(error.report.unmatched)  # ["withdraw 192.0.2.0/24 via 198.51.100.2 in VRF default"]
```

It raises when a withdrawal reads `ABSENT` or a key has no row at all. A host
prefix given as a bare address or as `/32` compares equal (Forward writes it
back bare). A staged
advertisement that reads `INJECTED` where you meant to replace a received route
is reported, not raised: that is how an EBGP/IBGP type mismatch shows, so check
`report.staged[i].change` if you expect `MODIFIED`.

Before staging, `client.change_sets.bgp_advertisement_readiness("edge-1")`
checks the three things that otherwise make Predict model nothing without an
error: the org properties `PREDICT_MODELING`, `PREDICT_MODEL_EXT_ADV` and
`PREDICT_MODEL_BGP`, the
device's collection setup (`collectBgpAdvertisements` on, and a table type that
includes ADJ_RIB_IN), and the network's BGP collection requirement groups.
For several devices, `bgp_advertisement_readiness_for([...])` makes the same
three reads once. A device with no classic collection source is unknown, not a
problem: a controller-managed device is configured elsewhere.
`report.ready` is true only when every check ran and found nothing;
`report.blocked` is true when one found a definite problem. A read you lack
permission for, a route this Forward does not serve
(`error.endpoint_not_served`), or a property the API does not return, goes in
`report.unknown` (with the reason for each in `report.unknown_reasons`) rather
than raising. Any other API error still raises.

To see what a device received before staging,
`client.bgp_advertisements.get_device_bgp_advertisements(device_name=,
vrf=, prefix=)` returns the received advertisements in the same record shape,
eBGP first. Both `vrf` and `prefix` are required; Forward has no unfiltered
form. Whether a predicted snapshot answers with the staged state is not
confirmed.

On an older Forward that does not map a newer action (`?action=bulkAdd`, say),
the call raises `ForwardBadRequestError` with `error.action_not_supported` set;
fall back to the one-row call.

Change sets live in a directory tree, through `client.change_set_directories`.
A directory path contains slashes and travels percent-encoded in the URL, which
the SDK does for you. Deleting a directory keeps the change sets inside it
unless `retain_change_sets` is passed as false.

## Things that look like failures

- A prediction over a stale base can fail with no reason through any route (observed). Reprocess the base
  snapshot and predict again.
- `protocol_modeling.list_predict_affected_devices` answered every device with an L3 address (49 of 49 on the one
  network observed), not only the devices the change touched. Do not read it as the change's blast radius; use the
  diffs for that.
- Deleting a change set, several at once, or a directory with `retain_change_sets=False`, also deletes their
  predicted snapshots.
- `validate_commands` errors seen live were all `INVALID` with line, columns and expected arguments. PAN-OS
  answers empty for anything, and EOS flags some real commands, so an empty or negative result is not proof.

## Listing, tagging and cleaning up

`client.change_sets.list()` returns the network's change sets, each with its
predicted snapshots newest first. `client.change_sets.tag(ids, add=...,
remove=...)` edits tags; with `remove_all=True` it ignores `add` and `remove`.
`create()` also takes `description`, `tags` and `dir_path` (its directory).

`handle.delete()` removes a change set. `client.change_sets.delete([...])`
removes several, using the bulk action whose body key is `changeSetIds`; Forward
refuses `ids`, which is the obvious guess.

## Commits, copies and whole-device configurations

A change-set handle also reads and manages what has been committed:

```python
handle = client.change_sets.handle("CHG-1")
handle.commit_detail(commit_id)  # a commit with its device changes
handle.edit_commit_note(commit_id, "reviewed")
handle.restore_commit(commit_id)  # a new head commit with that commit's changes
handle.restore_commit_as_new(commit_id, name="redo")
handle.delete_commit(commit_id)  # also deletes the predicted snapshots
# restore_commit first saves a pending draft as its own commit, so it can add two.
handle.duplicate(name="copy")
handle.duplicate(target_network_id="202", base_snapshot_id="55")
```

Beside staging commands, a device's whole configuration can be read and
replaced, and a command list previewed without staging it:

```python
handle.draft_device_config("edge-1")
handle.save_draft_device_config("edge-1", config_text)  # at most 2 MB
handle.committed_device_config(commit_id, "edge-1")
handle.preview_device_config("edge-1", "interface Ethernet1\n shutdown")
```

All of these are from Forward's source and its tests; the account used for live
checks cannot list change sets (403), so none has been run.

## Source

- `src/forward_sdk/_sync/client.py`
- `src/forward_sdk/nqe/query_ref.py`
- `src/forward_sdk/configuration.py`
- `src/forward_sdk/checks.py`
- `src/forward_sdk/trace.py`
- `src/forward_sdk/errors.py`
- `src/forward_sdk/models/`
- `src/forward_sdk/_async/services/change_sets.py`
- `src/forward_sdk/_async/services/diffs.py`
- `src/forward_sdk/_async/services/snapshots.py`
- `src/forward_sdk/_async/services/networks.py`
- `src/forward_sdk/_async/services/nqe.py`
- `src/forward_sdk/_ops/`
- `src/forward_sdk/bgp.py`
- `src/forward_sdk/_async/services/_generated/firewall_predict.py`
- `src/forward_sdk/_sync/services/_generated/firewall_predict.py`
- `src/forward_sdk/_async/services/_generated/bgp_advertisements.py`
- `src/forward_sdk/_async/services/_generated/change_set_directories.py`
- `src/forward_sdk/_async/services/_generated/checks.py`
- `src/forward_sdk/_generated/models.py`
