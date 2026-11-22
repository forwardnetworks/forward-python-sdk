# ADR-2611221107: Changes are decided, specified and tested before they are built

**Status:** Accepted
**Date:** 2026-11-22
**Drivers:** The owner's decision to develop the SDK spec-first, after its code
was given hexagonal layers (ports for the transport and adapters).

## Context

The SDK mirrors the Go SDK and Forward's API, and most of its defects so far
were behaviour nobody wrote down before building it: a body sent as JSON where
Forward reads form fields, a log redactor that split lines differently from Go,
a body shape Forward refuses. Each was found after release. A test written after
the code tends to check what the code does, not what was asked.

## Decision

1. A change that adds or changes behaviour starts as an ADR in `docs/adrs/`,
   with numbered decisions under `## Decision`. A fix with no decision in it (a
   typo, a dependency bump, a refactor with no behaviour change) does not need
   one.
2. Each such ADR gets a spec in `docs/specs/`: scenarios, each an example and a
   negative, drafted from the ADR and the files it names, never from the code.
   The repository owner accepts the spec before tests are written; an agent
   never accepts one.
3. A test is written for each scenario from the spec alone, committed, and
   shown to fail at that commit before the code is changed. The tests are
   ordinary pytest tests in `tests/`, bound to their scenarios and sealed: a
   later change to a sealed test goes through the ADR and the spec, not an edit.
4. The code is built until every sealed test passes. The gate for every commit
   is `uv run mypy && uv run pytest -q`: types first, then the whole suite.
5. ADRs and specs are public design records. The tooling that drives this
   process is local to each developer and is not committed.

## Consequences

**Good.** The decision and the definition of "correct" exist before the code,
in a form a reviewer can read without reading the code. A requirement changes
in one place, the ADR, and every test that depends on it is found by its spec.

**Bad.** A small behaviour change costs an ADR, a spec and an acceptance before
any code. Specs can only be accepted by the owner, so work waits on that step.

## Implementation

The process is run with local tooling (ADR authoring, spec drafting and
checking, test sealing and verification). Its configuration and hooks are kept
out of the repository by each developer's local git exclude list. CI is
unchanged: it runs the same tests.

## References

- `docs/specs/` — the specs, one per ADR.
- `tests/` — the sealed scenario tests, run by CI with the rest of the suite.
