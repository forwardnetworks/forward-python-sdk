# Unpublished endpoints

Most of this SDK is generated from the OpenAPI description Forward produces from
its own server code. A few endpoints are not in that description but are
supported here anyway.

## Why

They have no published equivalent, and real integrations depend on them.
Publishing NQE queries from code is the main example: it is how an integration
keeps the queries it ships in step with the code that consumes them.

## What that means for you

These endpoints are confirmed stable and safe to use. Forward's own integrations
depend on them, and they are exercised by this SDK's test suite like any other.

Being unpublished means only that they are absent from the OpenAPI description
Forward generates from its server, so:

- The SDK describes their shapes by hand, in `spec/unpublished.yaml`, rather
  than deriving them.
- They will not appear in Forward's generated API reference.
- A change to one would not show up in a spec diff, so it would not be caught by
  the drift check that covers the published API. The SDK tolerates the response
  shape variations seen in the field for exactly that reason.

They are kept in their own modules and marked `stability="unpublished"` in the
operation table, so nothing lands here by accident.

## The list

| Operation | Endpoint | Reached through |
| --- | --- | --- |
| List repository queries | `GET /nqe/repos/{repository}/commits/{commitId}/queries` | `client.nqe.repo.queries()` |
| Head commit | `GET /nqe/repos/org/commits/head` | `client.nqe.repo.head_commit_id()` |
| Query history | `GET /nqe/queries/{queryId}/history` | `client.nqe.repo.history()` |
| List drafts | `GET /users/current/nqe/changes` | `client.nqe.repo.drafts()` |
| Stage a change | `POST /users/current/nqe/changes` | `client.nqe.repo.stage_add()` and friends |
| Discard a draft | `DELETE /users/current/nqe/changes` | `client.nqe.repo.discard()` |
| Commit or dry-run | `POST /nqe/repos/org/commits` | `client.nqe.repo.commit()`, `dry_run()` |
| Start reachability | `POST /networks/{id}/snapshots/{id}/reachability` | `client.snapshots.start_reachability_job()` |
| Poll reachability | `GET /networks/{id}/snapshots/{id}/reachability/{jobKey}` | `job.wait()` |
| Current user and roles | `GET /users/current` | `client.user_accounts.get_current_user()` |
| Network data files | `GET /networks/{id}/data-files` | `client.data_files.get_data_files()` |
| List chats | `GET /ai-chats` | `client.ai.list()` |
| Start a chat | `POST /ai-chats` | `client.ai.start()` |
| Get a chat | `GET /ai-chats/{id}` | `client.ai.get()` |
| Rename a chat | `PATCH /ai-chats/{id}` | `conversation.rename()` |
| Delete a chat | `DELETE /ai-chats/{id}` | `conversation.delete()` |
| Ask a follow-up | `POST /ai-chats/{id}/messages` | `conversation.ask()` |
| Read the answers | `GET /ai-chats/{id}/messages` | `conversation.messages()` |
| Export a transcript | `GET /ai-chats/{id}/transcript` | `conversation.transcript()` |
| Change sets: create, list, tag, delete | `/networks/{id}/change-sets` and `?action=delete` | `client.change_sets` |
| Change set: summary, update, delete | `/networks/{id}/change-sets/{id}` and `?view=summary` | `handle.summary()`, `update()`, `delete()` |
| Stage, validate, discard device changes | `.../draft/devices/{name}/commands`, `.../commands?action=validate` | `handle.set_commands()`, `validate_commands()`, `discard_device()` |
| Draft, commits, head | `.../draft`, `.../commits`, `.../commits/head` | `handle.draft()`, `commits()`, `head()` |
| Commit | `POST .../commits?note=` | `handle.commit()` |
| Predict | `POST .../change-sets/{id}?action=predict&note=` | `handle.predict()`, `predict_and_wait()` |
| Predicted snapshots | `GET .../predicted-snapshots` | `handle.predicted_snapshots()` |
| Checks: add, list, delete | `.../checks`, `.../checks/{id}` | `handle.add_check()`, `checks()`, `delete_check()` |
| Firewall rules diff and edits | `.../devices/{name}/security-rules-diff`, `.../scopes/{s}/rulebases/{r}/security-rules` | `client.firewall_predict` |
| Snapshot diffs, curated | `/diffs/{a}/{b}/...` (connectivity with a wait, vulnerabilities, routing loops, per-area counts, file summary) | `client.snapshot_diffs` |
| Snapshot diffs, everything else | `/diffs/{a}/{b}/...` (checks, files, devices, cloud objects, per-device interface, ACL, NAT, routing, ARP and MAC entries, topology, VLANs, connectivity views) | `client.snapshot_diff_details` |
| Firewall object listings | `.../devices/{name}/{address-objects,zones,...}` | `client.firewall_predict.list_firewall_*()` |
| BGP advertisements | `.../draft/devices/{name}/bgp-advertisements?action=...` and `?view=diffs` | `client.bgp_advertisements` |
| Change-set directories | `/networks/{id}/change-set-directories[/{path}?action=...]` | `client.change_set_directories` |
| Predict AI assists | `.../cli-assists`, `.../overview-assists`, `/diffs/{a}/{b}/{config,impact}-summary-assists` | `client.predict_assist` |
| Webhooks | `/webhooks`, `/webhooks/{name}`, `?action=test`, `/webhook-types/{type}?view=default-templates` | `client.webhooks` |
| Org and global configuration | `/config/{p}`, `/global-config/{p}`, `/orgs/{org}/config/{p}` | `client.configuration`, `config_value()` |

The published `POST /snapshots/{id}?action=computeAdvancedReachability` does the
same work as the reachability job without progress polling; prefer it when you
do not need progress.

The Predict families are described in [Rehearsing a change](predict.md),
including the traps a consumer met while working around their absence.

The Forward AI endpoints carry a second condition beyond being unpublished: they
require the `AI_ALLOWED` organization property, so most organizations are
refused with a 403 whatever their licence otherwise covers. See
[Forward AI](ai.md).
