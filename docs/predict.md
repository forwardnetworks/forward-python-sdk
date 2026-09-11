# Rehearsing a change

Forward Predict answers "what would the network look like if I made this
change" without touching a device. The SDK exposes the whole loop: stage device
changes against a base snapshot, commit them, run Predict to produce a snapshot
of the network as it would be, then compare that snapshot to the base.

Every endpoint here is unpublished. Their shapes come from Forward's server
source and were verified against a live instance of the current build, and the
traps below cost a real integration real time before they were written down.

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
"Device dc-fw OS pan_os not supported". Firewall changes go through structured
rule edits instead:

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

**A settled zero is still ambiguous, and the two readings are opposites.**
Either the change altered no site-to-site connectivity, which is the reassuring
answer, or the network has no locations defined and the comparison had nothing
to compute over, which is a false all-clear. `total_subnet_pairs` separates
them: zero pairs means nothing was compared.

**Gating differs per diff.** Connectivity needs the `LOCATION_CONNECTIVITY_DIFFS`
org property. The bidirectional views, which split a change into what it makes
worse and what it improves, need `PREDICT_DIFF_INSIGHT_ENHANCEMENTS`. The
one-directional routing-loop and vulnerability counts need neither.

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

Writes follow the same split. Properties declared org-scoped, including every
Predict gate, have an immutable global default: the global write refuses with
"Permission denied. Global default value cannot be changed for X", and the org
admin route refuses too because they are not org-admin-configurable. A Forward
support account sets them per org with `set_org_scoped_config_value`, and reads
them back from the same route.

## Webhooks

An event-driven pipeline exists because predicted snapshots fire
`SNAPSHOT_READY` like any other. Three event types exist: `SNAPSHOT_READY`,
`NQE_VERIFICATION_FAILURE` and `INTENT_VERIFICATION_FAILURE`.

For `SNAPSHOT_READY`, `eventParams.networkIds` must be present. An empty list
means every network in the org; omitting the key is refused with a 400 that
carries no message, so it looks like a mystery. Creating a webhook needs the
`OUTBOUND_CONNECTIONS` deployment property.

## Cleaning up

`handle.delete()` removes a change set. `client.change_sets.delete([...])`
removes several, using the bulk action whose body key is `changeSetIds`; Forward
refuses `ids`, which is the obvious guess.
