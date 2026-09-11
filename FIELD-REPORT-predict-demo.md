# Field report: migrating a Predict demo onto forward-sdk 0.1.5

**From** the forward-change-demo session, 2026-09-11. Left here as a file because three
cross-session messages to this session expired awaiting approval.

Nine scripts were moved off raw urllib onto this SDK, all verified against a live
from-source instance (`1.0.0-260908-6aa8ff0c29ba`). This is what was missing and what got
in the way. Delete this file once it has been read.

## What was straightforwardly better

Worth saying first, because bug reports outnumber the other kind:

- `nqe.repo.publish` replaced four hand-written functions — directory creation on a first
  publish, add-versus-edit per path, pinning each edit to the version it was read from,
  refusing to trample someone else's uncommitted draft, and discarding its own staging
  when the commit fails. The code it replaced could leave a half-staged library behind on
  an error, and the next run would commit a mix of two attempts.
- `dry_run_snapshot_id` on that call would have caught three queries that did not compile
  before they were published. Several needed three attempts; without it, each attempt was
  published.
- `checks.deactivate_check` is exactly the operation being approximated with a raw PATCH
  of `{"enabled": false}`.
- `ai.ask` plus `answer_of` collapsed a start/poll/extract dance and the several spellings
  Forward has used for the answer field.
- Typed errors let the feature-flag script's "403 means org-scoped, retry against the org"
  branch read as what it is.

## Two rough edges

**`nqe.repo.publish` raises when nothing changed.** If every submitted path already
matches HEAD, Forward answers 409 `INVALID_CHANGE_PATH` with "User has no changes at the
following paths: ...". Re-running an idempotent publisher with nothing to publish is the
normal case in a pipeline. The workaround is to catch the conflict and check whether the
message names *every* submitted path (a no-op) or only a subset (something genuinely
disagrees, re-raise) — which is string-matching an error message. A distinct exception, or
a report saying nothing was committed, would be better.

**No "newest snapshot that is not a prediction".** `latest_processed_id` returns predicted
snapshots once Predict is in use, and basing a change set on one predicts a change against
a change. `latest_collected_id` answers a different question and costs a query per
candidate.

The subtlety is worth encoding if this is ever added: the filter must **exclude
`PREDICT`**, not **require `COLLECTION`**. A reprocessed snapshot carries
`processingTrigger: REPROCESS` and is still real collected data — reprocessing is how a
feature-flag or query change is picked up. Requiring `COLLECTION` silently skips it and
selects the *previous* collection, which during a rehearsal is the snapshot taken while
the change was still applied. Everything downstream then reports green against the wrong
state of the network. That happened once here before it was caught.

---

## The seven endpoints with no service

The consuming repo sends these on the SDK's own transport via a `RequestSpec` — keeping its retries,
throttling and typed errors rather than opening a second HTTP stack.

**This file is the handover note for that.** Every `raw()` call names the gap it covers,
so when a service lands, `grep -r 'gap="change-sets"'` finds every site that should move.

Written against **forward-sdk 0.1.5** and a from-source instance
(`1.0.0-260908-6aa8ff0c29ba`). Everything below was verified live.

> **Not yet sent to the SDK team.** Two attempts to message that session were not
> approved before expiry, so treat this as unreported. It is written out here so the
> detail survives; sending it is still to do.

---

## 1. Change sets and Predict — `gap="change-sets"`

The largest gap, and the one the demo is built on.

```
POST   /api/networks/{net}/change-sets                    {name, description, snapshotId, tags[]}
PUT    /api/networks/{net}/change-sets/{cs}/draft/devices/{dev}/commands    text/plain, CLI lines
POST   /api/networks/{net}/change-sets/{cs}/commits?note=...
POST   /api/networks/{net}/change-sets/{cs}?action=predict&note=...
GET    /api/networks/{net}/change-sets/{cs}/commits       -> deviceToChanges{device}{commands}
POST   /api/networks/{net}/change-sets?action=delete      {changeSetIds: [...]}
```

Traps worth encoding in any future signature:

- `note` is a **required query parameter** on the predict action. Omitting it returns 400
  "Required request parameter 'note' ... is not present".
- The delete action wants `changeSetIds`, not `ids`. `ids` returns 400 "Non-empty
  'changeSetIds' is required".
- Some builds want `networkId` in the create body as well as in the path.

## 2. PAN-OS firewall Predict — `gap="security-rules"`

**CLI-driven Predict does not work on PAN-OS.** A change-set draft of `set` lines
produces a predicted snapshot that processes to `FAILED`, with
`BadRequestException: Device dc-fw OS pan_os not supported` at
`PredictComputeWorkerService/ConfigGen`. Firewall Predict takes structured rule edits:

```
GET    /api/networks/{net}/change-sets/{cs}/devices/{dev}/security-rules-diff
POST   /api/networks/{net}/change-sets/{cs}/devices/{dev}/scopes/{scope}/rulebases/{rb}/security-rules
PATCH  .../security-rules/{uuid}
DELETE .../security-rules/{uuid}
```

Only `scopeId=LOCAL` + `rulebaseId=PRIMARY` are editable; everything else Forward returns
is read-only. Gated on the `FIREWALL_PREDICT` org property. The rule body shape is
whatever `security-rules-diff` returns under `a.definition` — read one and mirror it.

## 3. Snapshot diffs — `gap="diffs"`

In `spec/forward-openapi.yaml` but no service generated. The most valuable evidence in
the demo: these state blast radius without being asked a question.

```
GET /api/diffs/{a}/{b}/subnet-connectivity     newlyConnected/newlyIsolatedSubnetPairs, totalImpactedLocations
GET /api/diffs/{a}/{b}/vulnerabilities/counts  newCveCount, newExposedVulnerableDevicesCount
GET /api/diffs/{a}/{b}/routing-loop/count      count, complete
```

**Three behaviours that cost real time to work out**, all handled in
`scripts/publish_predict_evidence.py`:

1. `subnet-connectivity` is computed asynchronously. The first request returns
   `isPartialResult: true` with `evaluatedSubnetPairs: 0` — indistinguishable from
   "nothing changed" unless you poll. The webhook fires the instant a prediction
   finishes, so the first call lands inside that window almost every time.
2. A *settled* zero is ambiguous and the two readings are opposites: either the change
   altered no site-to-site connectivity (the reassuring answer a PROCEED deserves), or
   the network has no locations defined and the diff had nothing to compute over (a false
   all-clear). Only a locations lookup separates them.
3. Connectivity diffs need `LOCATION_CONNECTIVITY_DIFFS`; the bidirectional views need
   `PREDICT_DIFF_INSIGHT_ENHANCEMENTS`.

## 4. Webhooks — `gap="webhooks"`

`GET`/`POST /api/webhooks`. Only `SNAPSHOT_READY`, `NQE_VERIFICATION_FAILURE` and
`INTENT_VERIFICATION_FAILURE` exist.

- `eventParams.networkIds` must be **present**. An empty list means "all networks", but
  omitting the key NPEs the server: `Cannot invoke "Collection.isEmpty()" because
  "elements" is null`.
- **Predicted snapshots fire `SNAPSHOT_READY`.** Not obvious, and it is what makes an
  event-driven Predict pipeline possible at all.

## 5. Org and global configuration — `gap="config"`

`GET`/`PUT /api/global-config/{NAME}`.

- Some properties are org-scoped only: the global write returns 403 "Global default value
  cannot be changed" and must be retried against `/api/orgs/{org}/config/{NAME}`.
- Read back **from the scope that was written**. The global route keeps reporting the
  fixed global default even after an org value is set, which looks like a failed write.
- `DeploymentProperty.OUTBOUND_CONNECTIONS` is not an `OrgProperty` and cannot be read or
  set this way at all.

`forward_client.try_raw()` exists for this one: 403 and 404 are ordinary control flow
here, not exceptions.

---

## Two rough edges in services that do exist

**`nqe.repo.publish` raises on an unchanged corpus.** When every submitted path already
matches HEAD, Forward answers 409 `INVALID_CHANGE_PATH` with "User has no changes at the
following paths: ...". Re-running an idempotent publisher with nothing to publish is the
normal case in a pipeline. `scripts/publish_nqe_corpus.py` catches it and checks whether
the message names *every* submitted path (a no-op) or only a subset (something genuinely
disagrees, so it re-raises). That is string-matching an error message, which is worth
replacing with a distinct exception or a report saying nothing was committed.

**No "newest snapshot that is not a prediction".** `snapshots.latest_processed_id`
returns the newest processed snapshot, which once Predict is in use is very often a
predicted one — basing a change set on that predicts a change against a change.
`latest_collected_id` answers a different question and costs a query per candidate.

`forward_client.latest_collection()` fills this, and the subtlety is worth repeating: the
filter must **exclude `PREDICT`**, not **require `COLLECTION`**. A reprocessed snapshot
carries `processingTrigger: REPROCESS` and is still real collected data — reprocessing is
how a feature-flag or query change gets picked up. Requiring `COLLECTION` silently skips
it and selects the *previous* collection, which during a rehearsal is the snapshot taken
while the change was still applied. Everything downstream then reports green against the
wrong state of the network. That happened here once before it was caught.
