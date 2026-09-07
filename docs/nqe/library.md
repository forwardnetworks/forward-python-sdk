# The query library

!!! warning "Unpublished"

    These operations use endpoints that are **not** part of Forward's published
    API. They may change without notice. They are supported because publishing
    queries from code has no published alternative, and because existing Forward
    integrations depend on them.

Reach them through `client.nqe.repo`.

## Reading

```python
client.nqe.repo.queries(repository="org")  # list
client.nqe.repo.queries(path="/NetBox/Devices", with_source=True)
client.nqe.repo.find("/NetBox/Devices")  # one, by path
client.nqe.repo.index()  # path -> query, cached
client.nqe.repo.head_commit_id()
client.nqe.repo.history("FQ_...")
```

`org` is your organization's library; `fwd` is the one Forward ships.

## Publishing

Publishing is staged, like a commit: changes are staged as drafts, optionally
validated, then committed together. `publish()` does the whole sequence.

```python
report = client.nqe.repo.publish(
    {
        "/MyOrg/Devices": load_query("queries/devices.nqe", for_execution=False),
        "/MyOrg/Interfaces": load_query("queries/interfaces.nqe", for_execution=False),
    },
    title="Update device and interface queries",
    dry_run_snapshot_id=snapshot.id,
)
report.committed_paths
report.skipped_paths  # unchanged, so nothing to commit
```

Each path is staged as an addition or an edit depending on whether it already
exists. `dry_run_snapshot_id` validates the queries against a real snapshot
first, which catches a query that no longer compiles before anyone else sees it.
If anything fails, staged drafts are discarded so a failed run does not leave
half-staged changes behind.

Paths whose source is unchanged are reported as skipped rather than failing the
commit: publishing a directory where some files are identical is the normal case.

## Lower-level staging

```python
client.nqe.repo.stage_add("/MyOrg/New", source)
client.nqe.repo.stage_edit("/MyOrg/Existing", source, query_id=..., commit_id=...)
client.nqe.repo.stage_directory("/MyOrg")
client.nqe.repo.drafts()
client.nqe.repo.discard("/MyOrg/New")
client.nqe.repo.dry_run(["/MyOrg/New"], snapshot_id="101")
client.nqe.repo.commit(["/MyOrg/New"], title="Add query")
```

The `basis` on an edit records which version you edited, so Forward can reject a
change built on a version that has since moved on.
