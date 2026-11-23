# ADR-2611230746: The spec-first process is checked by the repository's own tests

**Status:** Proposed
**Date:** 2026-11-23
**Drivers:** ADR-2611221107 decided the spec-first process in terms of what people
do (decide, accept, write red tests), which no test can check, so it has no spec.
This ADR restates each of its decisions as a claim about the repository's files
that a test can check, and supersedes it.
**Supersedes:** ADR-2611221107

## Context

The process leaves records in the repository: ADRs in `docs/adrs/`, one spec per
ADR in `docs/specs/<id>.json`, and the tests each spec binds. A spec records its
acceptance as `accepted: {"by": <person>, "sha256": <hex>}`, and each scenario
bound to a test names it as `test: "<file>::<function>"` with the commit it failed
at as `red: "<commit>"`. CI is `.github/workflows/ci.yml`. If these records can
be checked, the process is enforced by the test suite, not by memory.

## Decision

1. Every ADR in `docs/adrs/` whose status is Accepted or Completed, other than
   ADR-2611221107, has a spec at `docs/specs/<ADR id>.json`, and the spec's
   `adr` field names that ADR.
2. A spec that binds any scenario to a test has an `accepted` record naming a
   person (`by`, not empty) and a 64-character hexadecimal `sha256`.
3. Every scenario bound to a test names a file under `tests/` that exists and
   defines a test function of the named name, and records a `red` commit id of 7
   to 40 hexadecimal characters.
4. The CI workflow runs the type checker (`uv run mypy`) and the test suite
   (`uv run pytest`) on every push to the default branch and on every pull
   request.
5. The only tracked paths whose first segment starts with a dot are
   `.gitattributes`, `.github`, `.gitignore`, `.pre-commit-config.yaml` and
   `.python-version`: no tool's local configuration is committed.

## Consequences

**Good.** A change that skips a step (an ADR accepted with no spec, a scenario
bound to a test that was renamed, a tool's local folder committed by accident)
fails CI instead of being noticed later. Each decision has a spec and a test.

**Bad.** Decision 5's list must be edited, through an ADR, to commit a new dot
file. Decision 3 checks that a red commit is named, not that the test failed
there; that proof needs the history and is run where the work is done.

## Implementation

A test module, `tests/unit/test_design_records.py`, reads `docs/adrs/`,
`docs/specs/`, `tests/` and `.github/workflows/ci.yml`, and lists tracked paths
with `git ls-files`. ADR-2611221107 is marked Superseded by this ADR.

## References

- ADR-2611221107 — the process this ADR restates.
- ADR-2611221115 — the first change made under it.
