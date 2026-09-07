# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/) and the project uses SemVer.

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
