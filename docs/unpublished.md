# Unpublished endpoints

Most of this SDK is generated from the OpenAPI description Forward produces from
its own server code. A few endpoints are not in that description but are
supported here anyway.

## Why

They have no published equivalent, and real integrations depend on them.
Publishing NQE queries from code is the main example: it is how an integration
keeps the queries it ships in step with the code that consumes them.

## What that means for you

- They may change or disappear in any Forward release, without notice.
- They are not covered by Forward's API compatibility expectations.
- The SDK tolerates the response shape variations observed in the field, but
  cannot promise to keep doing so.

They are kept in their own modules, described in `spec/unpublished.yaml`, and
marked `stability="unpublished"` in the operation table, so nothing is
unpublished by accident.

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

The published `POST /snapshots/{id}?action=computeAdvancedReachability` does the
same work as the reachability job without progress polling; prefer it when you
do not need progress.

Two fields on the NQE execution request, `sortKeys` and `columnFilters`, are
also absent from the published description. They are sent only when you supply
them.
