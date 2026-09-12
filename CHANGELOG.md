# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/) and the project uses SemVer.

## [0.1.13] - 2026-09-12

### Added

- Deployment configuration on `client.configuration`: list every deployment
  property, and read, set or clear one. The third scope beside org and
  global, and the only route to properties such as `OUTBOUND_CONNECTIONS`,
  which gates webhook creation, and `PROBE_LLM_AVAILABILITY`, which makes
  Forward AI appear on a self-hosted deployment. Keys come back in upper case
  here, unlike the org routes; `config_value()` reads either. Verified live,
  including a write of a property's current value back to itself. Reported by
  the change-demo integration as the last item of its third report.

## [0.1.12] - 2026-09-11

### Fixed

- `nqe.repo.publish` with a dry run refused every clean change. Forward's dry
  run returns `newErrors` as a map from every path examined to that path's
  diagnostics, and a path that compiles maps to an empty list; the SDK read the
  map's size as an error count, so a changed query that compiled was refused as
  "1 new error". The recommended path, dry run before commit, could therefore
  not publish a changed query at all. Older than any of the reports: the line
  dates from the NQE layer's first commit, and its test fixture was a list, the
  shape the parser believed, so the two agreed while both were wrong.

  Diagnostics are now read by severity, as Forward's own commit dialog reads
  them: an `ERROR` refuses the publish and puts Forward's message and the path
  in the exception, since diagnostics like "Mismatched input 'this'. Reminder:
  record fields are comma-separated." are what the caller needs to see; a
  `WARNING` does not block and is returned on `CommitReport.warnings`. Verified
  live: a broken edit is refused quoting the diagnostic with no draft left and
  the library unchanged. Reported by the change-demo integration in its third
  report, found while verifying the previous fix.

### Added

- `devices.upsert_classic()`, which runs Forward's batch upsert and then reads
  the same names back, returning the devices as stored. The upsert itself
  answers 201 with nothing worth reading, so confirming what was written took a
  second call every consumer had to write. Verified live; a second run with the
  same manifest returns the same devices.

## [0.1.11] - 2026-09-11

### Fixed

- `nqe.repo.dry_run` drops paths with nothing staged and retries, exactly as
  `commit` does. A dry run is the same request with a flag and Forward refuses
  it for the same reason, so `publish(dry_run_snapshot_id=...)` raised on an
  unchanged corpus where `publish()` alone reported it. The documented
  recommendation to dry-run before publishing and the no-op case could not be
  used together. Reported by the change-demo integration in its third report
  and verified live: 23 unchanged paths with a dry run now publish as 23
  skipped, nothing raised, no drafts left.

## [0.1.10] - 2026-09-11

### Added

- `client.user_events.stream()`, the calling user's live events as Server-Sent
  Events, parsed into typed `UserEvent` values with keepalives consumed. It is
  the only source of per-device collection progress; polling a collector task
  gives the task, not each device. Verified live: a change set created and
  deleted while subscribed arrived as `CHANGE_SET_DELETED` with its id. The
  parser is pure and shared by both clients.
- `client.cloud_accounts`: seventeen operations for cloud collection sources,
  list, create, update, delete, credential rotation, sub-account discovery
  before and after creation, connectivity tests, the AWS assume-role external
  id, and the region and environment lookups. Bodies are selected by cloud
  type and the per-type fields are documented on each schema; secrets are
  accepted on input and never returned. Verified live for the reads.

That completes the change-demo integration's second report. Everything Forward
serves for Predict, credentials, collectors, cloud sources and live events is
now an SDK call.

## [0.1.9] - 2026-09-11

### Added

- `client.snmp_credentials`: list, create, update and delete a network's SNMP
  credentials, which the published description leaves out although classic
  devices reference them by id. Verified live with a credential created,
  renamed and deleted. Secrets never come back: the community string and V3
  passwords are returned as the numeric id of the stored secret.
- `client.collector_binding`: which collector serves a network, its connection
  and health status, its version and update state, plus binding and unbinding.
  The published `/collector/status` is the deprecated busy flag; this is the
  route a pre-flight wants. Verified live, where the collector reported
  `CONNECTED` and `UNHEALTHY`. Its `createdAt` is epoch milliseconds, which the
  new response error caught on the first live call.
- `snapshots.list(exclude_triggers=[...])`, applied after the response since
  Forward does not filter by trigger, with the limit applied after filtering so
  the answer is not a short page.

### Noted

- The second gap report from the change-demo integration, written against
  0.1.7, listed the unchanged-corpus publish failure and the predicted-snapshot
  default as still open. Both are fixed in 0.1.7 and verified again here on the
  published package: 23 unchanged paths publish as 23 skipped, and predictions
  are excluded by default. It also asked for a classic-device upsert, which the
  SDK has had all along as `classic_devices.put_classic_devices()` and the
  per-device `put_classic_device()` and `patch_classic_device()`, generated from
  the published description under Forward's operation names.

## [0.1.8] - 2026-09-11

### Added

- The rest of Forward Predict's surface, 51 more unpublished operations, so
  everything the diff and change-set controllers serve is reachable. Nine
  firewall object listings, which return the scoped identifiers the rule
  writes expect. Four Forward AI assists. BGP advertisement injection,
  withdrawal, restore and diffs. The change-set directory tree, with directory
  paths percent-encoded for you. And `client.snapshot_diff_details` for every
  remaining diff view: check, file, device and cloud-object diffs, per-device
  interface, ACL, NAT, routing, ARP and MAC entries, topology, VLANs and the
  four connectivity views. All verified against a live instance of the current
  build, writes included: a directory created, renamed and deleted; a BGP
  route injected, reported as `INJECTED`, restored and gone.

  The curated `snapshot_diffs` service keeps the summaries and the connectivity
  wait; the details service is generated from the description like every
  published group. An NQE diff over the data model reaches the same per-device
  differences with columns of your choosing, and the Predict page says when
  each route is the better one.

### Fixed

- A positional enum alias in the public models is resolved from the field that
  uses it rather than from a literal number, which shifted the moment an inline
  enum was added earlier in the description.

## [0.1.7] - 2026-09-11

### Added

- Forward Predict, as five new groups of unpublished operations: 50 in all,
  described from Forward's server source and verified against a live instance
  of the current build. Requested by the change-demo integration, which had
  been reaching them through a raw request helper.

  `client.change_sets` covers the rehearsal loop with a handle per change set:
  create against a base snapshot, stage device commands as plain text, validate,
  commit, `predict_and_wait()` for the processed predicted snapshot, checks, and
  delete. `client.snapshot_diffs` compares two snapshots: subnet connectivity
  with `wait_for_subnet_connectivity()` for the asynchronous computation,
  vulnerability and routing-loop counts, their bidirectional views, a file
  summary, and `counts()` for every per-area count at once.
  `client.firewall_predict` holds the structured security-rule edits PAN-OS
  needs, since CLI-driven Predict does not work there. `client.webhooks` and
  `client.configuration` round it out, with `config_value()` to read a
  configuration response whatever its key.

  The traps a consumer met while working around these are encoded rather than
  documented: `note` is required on predict and commit, the bulk delete key is
  `changeSetIds`, the create body carries no `networkId` because Forward
  rejects unknown properties, and the first connectivity read after a
  prediction returns zeros that mean "not finished". See the new "Rehearsing a
  change" page for the ones a signature cannot carry, including the global
  configuration route reporting a default that the org route contradicts.

  Two claims in the consumer's notes were wrong and are corrected here: the
  create body must not carry `networkId`, and an omitted webhook network list
  is a 400 rather than a server crash.

- `HAND_WRITTEN_TAGS` is now one list, on the operation definitions module,
  read by the service generator and the coverage test alike, so the two cannot
  disagree about which groups are generated.

### Fixed

- The unpublished-endpoints page said the NQE execution request's sort and
  filter fields were absent from the published description. They are in it.

- `nqe.repo.publish` no longer raises when nothing has changed. Forward
  terminates its rejection with a full stop, `User has no changes at the
  following paths: /a/q1, /a/q2.`, and the parser kept it, so the last path read
  as `/a/q2.` and matched nothing. The caller then stripped every path but that
  one and retried a commit Forward refused for the same reason, and the second
  refusal escaped. Re-running an idempotent publisher with nothing to publish is
  the normal case in a pipeline, and it reported failure. A single-path commit
  was affected too, since that path is also the last one.

  A second refusal of the stripped set is now also read as a no-op rather than
  raising, so a message this parser cannot read cannot turn a no-op into an
  error again. Found by reading Forward's own source, which builds that string,
  and confirmed on an instance of the current build: re-publishing a committed
  query's own source returns a report with it skipped, commits nothing and
  leaves no draft behind.

### Changed

- `latest_processed` and `latest_processed_id` exclude predicted snapshots.
  Forward creates and processes one for every Predict run, so on a network using
  Predict the newest processed snapshot is very often a prediction rather than a
  state the network was ever in, and basing a change set on one predicts a
  change against a change. Pass `include_predicted=True` for the old behaviour.

  The filter excludes `PREDICT` rather than requiring `COLLECTION`. A reprocessed
  snapshot reports `REPROCESS` and is real collected data, and on one live
  network 20 of 43 processed snapshots were reprocessed; requiring `COLLECTION`
  would skip those and select the previous collection, which during a rehearsal
  is the snapshot taken while the change was still applied. Reported by the
  change-demo integration, which had hit exactly that.

  Confirmed against a Predict-enabled instance: on a network there, 4 of 5
  processed snapshots were predictions, so the previous default picked one four
  times in five.

## [0.1.6] - 2026-09-11

### Added

- Paging no longer reports a stall on a query whose rows are identical. The
  guard compared each page's first and last row, so `select {n: 1}` produced the
  same signature on every page whether the offset advanced or not, and raised
  "paging is not advancing" at `repeat_limit * page_size` rows on a query that
  was paging correctly. A page whose first and last rows match is now skipped as
  carrying no evidence, and the message names the other possibility.

  Reported as a Forward-side cap at 250,000 rows. It is not one: 250,000 is the
  default `repeat_limit` of 25 times a 10,000-row page. Verified against a
  545,464-row result, which now pages to completion in 55 pages, and where
  Forward returns 3 rows at offset 545,461 and 0 at 545,464, so the offset is
  honoured to the last row.

  A genuinely stalled server returning uniform rows is no longer diagnosed by
  this guard. It still terminates, bounded by `max_rows`, `max_pages` and the
  reported total.
- A failed NQE query now says what was wrong with it. Forward's message is the
  fixed string "Error encountered while executing the NQE query" for every
  failure, whatever went wrong; the detail lives in a separate `errors` list
  that the SDK carried on the exception but left out of its message, so every
  query failure read identically in a log. Up to three diagnostics, with line
  and column, now reach the message, and the full list stays on `query_errors`.
- `CounterSnapshot` reads as a mapping, so `dict(snapshot)` and `**snapshot`
  work. It always had `as_dict()`, but nothing signalled that, and a consumer
  who tried the obvious thing concluded it could not be serialized.
- `error.denial` on an API error, which says what kind of refusal it is. Read
  from Forward's server source rather than inferred: its access enforcer sets
  `reason` to `null` on every refusal, so no code distinguishes a missing
  permission from an unlicensed feature from a setting switched off, but its
  wording does. Six kinds are recognised, each from the enforcer's own format
  string. This is prose rather than a contract, so it is documented as something
  to show an operator rather than something to branch on.

  This narrows a question the SDK previously called unanswerable. Denials remain
  indistinguishable by status code; they are not indistinguishable in practice.
- `use_latest_data_files` on `nqe.execute()`. A published field on the execution
  request that the SDK never sent, so a caller could not evaluate a query
  against the most recently uploaded data files instead of the versions the
  snapshot captured.
- `snapshot_cache_ttl="lifetime"`, which resolves a snapshot once and keeps that
  answer for the life of the client. A number of seconds was the wrong shape for
  the case the setting was added for: a run that has pinned one point in time
  does not want the answer to change underneath it, and a TTL expiring mid-run
  lets the next resolution return a different snapshot, so the run straddles two
  moments with nothing raising and the data real on both sides.

  Seconds remain right for repeated independent reads, and the documentation now
  says which shape suits which caller rather than leaving it to be discovered.
  `"lifetime"` still yields to an upload through the same client, because that is
  an event that makes the answer wrong rather than merely old. Suggested by the
  Nautobot integration, whose own cache is lifetime-scoped for this reason.

## [0.1.5] - 2026-09-09

### Changed

- The rate-limiting documentation now describes Forward's actual limiter, read
  from the server source rather than inferred from a plugin's constant. Three
  corrections matter. The budget is per authenticated user per minute, not
  per account, per host or per client, so every process using one set of
  credentials spends from one allowance. Exceeding it does not slow a request,
  it blocks the user for a lockout of one to sixty minutes, answered with 429
  and a `Retry-After` carrying the remaining block. And the ceiling is an org
  setting defaulting to 2000 and adjustable from 1 to 10,000, disabled entirely
  by default on self-hosted deployments, none of which the SDK can query. The
  `"auto"` default of 1800 is unchanged but is now described as headroom
  against a default rather than as a reading of your ceiling.

### Added

- `to_api()` on `RepositoryQuery` and `DraftChange`. These are frozen
  dataclasses rather than pydantic models, so they never had `model_dump`, and a
  consumer calling it saw their query index come back empty instead of failing:
  their normalisation swallowed the `AttributeError`, and an empty library is a
  plausible answer, so the symptom surfaced two calls later. The method is named
  to match `ForwardModel.to_api` so serializing something the SDK returned does
  not require knowing which of the two kinds it is.

  Note also that `forward_sdk._generated.models` declares its own
  `RepositoryQuery`, the wire schema these are built from. That one is a
  pydantic model and is not what the library methods return. The docstrings now
  say so.
- `ForwardResponseError`, so a response the SDK cannot parse stays inside the
  exception tree. A body that failed validation previously raised pydantic's
  `ValidationError`, which is not a `ForwardError` and therefore travelled
  straight through every consumer's error handling: a sync died with a
  validation traceback in a job log rather than a failure it could classify and
  report. Reported by the NetBox integration, which hit it on a `Network`
  missing `orgId`.

  It carries the unparsed `payload` and the `model_name` the SDK tried to build,
  and keeps the original `ValidationError` as `__cause__` for per-field detail.
  The wrapping is done once on the model base rather than at the 118 call sites
  that parse a response, because a caller's `except ForwardError` has to cover
  every one of them or it covers none.

  Required fields are unchanged and stay strict. They come from Forward's own
  generated description, and a model with a hole in it would carry a wrong
  response silently into whatever the caller writes next. The exposure is also
  smaller than it looks: 170 of 276 models require at least one field, 379 in
  total, and `SnapshotInfo`, `Device`, `NqeRunResult` and `ApiVersion` require
  none. The live suite passes unchanged against fwd.app, so real traffic carries
  what the description promises on every path it covers.
- `snapshot_cache_ttl`, an opt-in cache for snapshot resolution, off by default.
  Covers `latest_processed` and `latest_collected_id`, keyed by the exact
  question so two tag scopes stay two answers, and cleared when an upload
  through the same client makes it wrong. `client.snapshots.clear_cache()`
  handles what the SDK cannot see.

  It exists for the rate limit rather than for latency. Forward's budget is per
  authenticated user and exceeding it blocks the user, so a sync resolving a
  snapshot once per slice across a thread pool spends a large share of an
  allowance on a question with one answer. It stays off by default because a
  stale snapshot does not raise, it returns real data from the wrong moment, and
  only the caller knows how long "latest" should stay true.
- `.github/workflows/live.yml` runs the live tests weekly against a configured
  instance and opens an issue on failure. The unpublished endpoints have no
  generated description to check against, so nothing in CI could notice Forward
  changing one of their shapes; every such defect so far was reported by a
  consumer whose sync broke.
- Six live tests under `TestShapesWithNoCiBackstop`, each pinning a shape that
  has been wrong at least once: rows surviving unrewritten, one page agreeing
  with all rows, committed source reaching only from a concrete commit, source
  without a path being refused before it is sent, a query carrying its commit,
  and the current user parsing.

## [0.1.4] - 2026-09-07

### Added

- `nqe.diff_page()`, one page of a snapshot diff, the counterpart to `run()`
  for diffs. `diff()` pages to completion, which is the wrong shape for showing
  an operator the first rows that changed: it fetches the whole diff to display
  fifty rows. Page guards cannot stand in, because a page ceiling raises rather
  than stopping, which is right for a ceiling and wrong for a limit. Requested
  by the NetBox integration, which was slicing a complete diff.

### Fixed

- NQE result rows are no longer rewritten. `rows()` and `stream()` stripped a
  `fields` wrapper that Forward never sends. Its serializer holds a row in a
  field it calls `fields` internally and writes the row's own entries at the top
  level, confirmed against the server source, the API description and a live
  instance. The stripping silently rewrote the result of
  `select {fields: {...}}`, dropping a level and a key name, and it could not be
  detected downstream because a row with one key named `fields` and the envelope
  it was mistaken for are identical on the wire.

  This also settles a shape disagreement reported by an integration: `run()` and
  `result_page()` never stripped, so asking for one bounded page returned
  different rows than iterating all of them. All four paths now agree.

- `nqe.repo.queries()` honours `path` and `with_source` at `head`. Forward
  ignores both there and returns the whole library without source, so a path
  filter looked applied and a source audit read every query as
  source-unavailable and passed vacuously. `head` is now resolved to its commit
  whenever either argument is used.

- `attempts_per_minute` counts intervals, not attempts. The window runs from the
  first attempt to the most recent, which spans one fewer interval than there
  are attempts, so the rate was overstated by `n / (n - 1)`: 100% at two
  attempts, 5% at twenty. The error was pessimistic, so a release gate comparing
  it against Forward's published ceiling could fail a build on a rate that was
  within the limit.

- Forward's error body is parsed on its own merits rather than on the
  `content-type` it arrives with. A proxy that rewrote or dropped the header
  turned a described denial into a bare status. That mattered because Forward
  exposes no endpoint for an account's entitlements, so a refusal's message is
  the only signal a licence-tier denial exists, and losing it fails silently:
  the denial looks like any other 4xx. A body that is not a JSON object still
  parses to nothing, so an HTML gateway page is unchanged. A test now pins that
  the message and the raw body reach the exception verbatim.

### Changed

- `nqe.repo.queries(with_source=True)` without a `path` now raises
  `ForwardConfigurationError`. Forward returns committed source one query at a
  time and rejects the combination with a message naming the parameters rather
  than the reason. Use `source()` for a single query's text.

## [0.1.3] - 2026-09-07

### Added

- `forward_sdk.nqe.enums`, the member names **NQE** accepts, generated from
  Forward's NQE data model rather than from the REST schema. 59 types, 659
  members, regenerated by the same sync that handles the OpenAPI description.

  This exists because the two are different namespaces, not one list lagging the
  other. NQE's `Vendor` has `AZURE` and `GENERAL_DYNAMICS` where the REST model
  has `MICROSOFT`, `GD` and `IBM`. NQE's `DeviceType` has 16 members describing
  what a device does; the REST model has 75 describing how it is deployed.
  Building a predicate from the REST names produces a query Forward rejects with
  *Unknown alternative*.

  ```python
  from forward_sdk.nqe.enums import members

  "AZURE" in members("Vendor")  # True
  "MICROSOFT" in members("Vendor")  # False, that is the REST name
  ```

### Changed

- `enum_one_of()` checks member names against the NQE data model, so a REST name
  fails locally with a message naming both namespaces instead of building a
  query Forward refuses. A type the data model does not cover is left alone.
- `enum_one_of()` no longer accepts a model class in place of the NQE type name.
  The NQE type name is not always the class name: `device.platform.os` has NQE
  type `OS` where the model class is `VendorOs`, and passing `VendorOs` fails
  with *Variable VendorOs not in scope*. This was added and withdrawn without
  ever being released.

### Documentation

- The NQE data model reference is cited with a URL that resolves, and named as
  the source for type and member names.
- How to run the live tests, and when they are worth running: after touching a
  predicate builder, a response parser, or anything under `spec/unpublished.yaml`.

## [0.1.2] - 2026-09-07

Everything here came from running the SDK against a real Forward instance
(26.8.4) for the first time. Seven defects, none of which any test suite in this
repository could have caught, because they were all wrong assumptions about what
Forward actually sends.

### Fixed

- `one_of()` produced invalid NQE for enum fields such as
  `device.platform.vendor`, failing at run time with *the type of lookup value
  Vendor is not equal to list element type String*. Added `enum_one_of()`, which
  emits `field == Vendor.CISCO`; `one_of()` is now documented as string-only.
- `nqe.repo.source()` always failed. Forward honours `path` and
  `with=sourceCode` only against a specific commit; asked at `head` it ignores
  both and returns the whole library without source. It now pins the commit.
- `publish()` could not create a query in a directory that did not exist yet,
  which Forward refuses. Missing enclosing directories are now created first.
- `discard()` could not remove a directory. Forward requires a trailing slash to
  create one and rejects it to discard one, so every directory a failed publish
  created was stranded.
- The `CurrentUser` shape was wrong: the account is nested under `user`, not
  spread at the top level.
- The Forward AI answer arrives as `finalAnswer` and is an object, not a string,
  so every message failed to parse. Added `answer_of()`, which reads whichever
  field a given deployment uses.
- Recorded that a repository listing returns a flat `lastCommitId` at `head` and
  a nested `lastCommit` against a pinned commit. Both are real; reading only one
  loses the commit pin.

### Confirmed against a live instance

- `contains` is rejected by Forward, so the predicate fix in 0.1.0 was necessary
  rather than cosmetic.
- The snapshot listing is genuinely unordered, in neither direction. Taking the
  first row of a one-row request, as the SDK once did, would have pinned a sync
  to an arbitrary snapshot.
- A resolved query path sends a full 40-character commit id to Forward.
- The dry-run report carries exactly the four fields modelled for it.

## [0.1.1] - 2026-09-07

### Added

- **Forward AI** (`client.ai`). Ask a question about a network in plain language
  and get an answer grounded in one snapshot, with the tools Forward used to
  reach it. A conversation stays pinned to its snapshot, so follow-up questions
  are answered against the same model as the first.

  This reverses an earlier decision to leave Forward AI out of scope. It is
  unpublished and gated twice over: absent from Forward's published API, and
  requiring the `AI_ALLOWED` organization property, so most organizations are
  refused with a 403. That refusal arrives as an ordinary
  `ForwardPermissionError` carrying Forward's own sentence rather than a status
  invented here.

- `client.nqe.repo.source(path)` returns a query's committed source, fetching
  with the flag a caller would otherwise have to remember, and failing loudly
  when Forward returns none. Previously `RepositoryQuery.source` was simply
  `None` when `with_source=True` had been forgotten, which reads like an empty
  query rather than a missing argument.

## [0.1.0] - 2026-09-07

First release.

### Added

- Every operation in Forward's API: 208 published, plus 11 endpoints Forward
  does not publish that real integrations depend on. All of them described, so
  responses are validated against a declared shape rather than read by hand.
- Synchronous and asynchronous clients with identical surfaces. The async one is
  the source; the sync one is generated from it, so their retry rules, poll
  loops and paging cannot diverge.
- NQE as the most developed surface: running a query inline or in the
  background, paging or streaming results, diffing across snapshots, and
  publishing queries to the library. Paging carries guard rails for the ways it
  fails in production: a result set that ends before its promised total, a
  server that stops advancing, a query larger than expected.
- Per-execution telemetry retained on the client, and request counters split by
  failure cause with an observed request rate.
- 364 models generated from the API description, tolerant of fields and enum
  values a newer Forward adds.
- Helpers for the work integrations were each doing themselves: loading `.nqe`
  files, stripping `@primaryKey`, inlining local imports, reading a query's
  column contract, and building escaped predicates.

### Notes

- Licensing, deployment and role-based denials arrive as the same status codes,
  so the SDK does not claim to tell them apart. It surfaces Forward's own
  explanation and a documented hint about what gates each group. See
  `docs/gating.md`.
- NQE result rows are `dict[str, Any]`. A query's columns are defined by the
  query, and column names are frequently not valid Python identifiers.
- Unpublished endpoints are isolated, described in `spec/unpublished.yaml` and
  marked in the operation table. They are stable and used in production, but a
  change to one would not appear in a spec diff, so this SDK's own tests are
  what guard them. See `docs/unpublished.md`.
