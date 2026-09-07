# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/) and the project uses SemVer.

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
