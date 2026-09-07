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

The published `POST /snapshots/{id}?action=computeAdvancedReachability` does the
same work as the reachability job without progress polling; prefer it when you
do not need progress.

Two fields on the NQE execution request, `sortKeys` and `columnFilters`, are
also absent from the published description. They are sent only when you supply
them.

The Forward AI endpoints carry a second condition beyond being unpublished: they
require the `AI_ALLOWED` organization property, so most organizations are
refused with a 403 whatever their licence otherwise covers. See
[Forward AI](ai.md).
