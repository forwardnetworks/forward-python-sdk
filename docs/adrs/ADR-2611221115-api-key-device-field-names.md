# ADR-2611221115: An API-key classic device takes the SDK's field names as well as Forward's

**Status:** Completed
> 2026-11-22 Completed: landed on main at ec2fe84; gate `uv run mypy && uv run pytest -q` passed
**Date:** 2026-11-22
**Drivers:** A review of `networks.create_classic_device_with_api_key` (0.1.41):
a device mapping written with the SDK's own field name `http_credential_id`
passed the check that refuses a device naming its own HTTP credential, and the
request then carried both `http_credential_id` and `httpCredentialId`.

## Context

`networks.create_classic_device_with_api_key(credential, device, *, network_id=None)`
in `src/forward_sdk/_async/services/networks.py` takes each argument as a
request model (`NewHttpCredential`, `NewClassicDevice`) or a plain mapping. A
model is serialized with Forward's field names (`httpCredentialId`,
`loginType`). A mapping is sent as it is, and its checks read Forward's names
only. Elsewhere the SDK accepts either spelling in a mapping
(`networks.ensure_locations` validates through the request model), so a caller
writing `{"http_credential_id": ...}` here has reason to expect it to work.

Each model field has a Python name and, where they differ, an API name:
`NewHttpCredential.login_type` / `loginType`, `auto_associate` /
`autoAssociate`; `NewClassicDevice.http_credential_id` / `httpCredentialId`,
`cli_credential_id` / `cliCredentialId`, and so on. `name`, `host`, `type` and
`password` are spelled the same both ways.

## Decision

1. A credential or device given as a mapping may name each field by its Python
   name (as on `NewHttpCredential` or `NewClassicDevice`) or by its API name.
   The request Forward receives names each field by its API name only.
2. A mapping that names the same field by both spellings (`http_credential_id`
   and `httpCredentialId`) is refused with `ValueError` before any request is
   made, whether or not the two values agree.
3. The existing checks hold whichever spelling a caller uses: a device with no
   name or no host, a credential whose type is not `API_KEY`, and a device that
   names an HTTP credential are each refused with `ValueError` before any
   request.
4. A key that is neither a Python nor an API name of the model's fields is sent
   unchanged, as today; the SDK does not refuse fields Forward may accept but
   the model does not describe.
5. A request model argument is serialized as today (`to_api()`), so a caller
   passing a model sees no change.

## Consequences

**Good.** The same dict works here as in the SDK's other writes, and the check
that guards the leftover-credential failure can no longer be bypassed by a
spelling.

**Bad.** A mapping that today sends a Python-named field verbatim (Forward
ignores it) now sends it under its API name, so Forward reads it. That is the
intent, but it changes what such a request does.

## Implementation

In `src/forward_sdk/_async/services/networks.py`, `_as_body` (used only by this
method) maps each Python field name of the target model to its API name, and
refuses a field named both ways. The sync client is generated from it.

**Built:** decisions 1 to 5 on 2026-11-22 at ec2fe84. Scenarios S01, S03 to S08, S10 and S14 were
red at 2bfc8fb and pass at ec2fe84 (`tests/_async/test_api_key_device_names.py`, sealed). The
other eleven pin behaviour this ADR keeps and passed before the change.

## References

- `docs/specs/` — this ADR's spec.
- ADR-2611221107 — the spec-first process this change follows.
