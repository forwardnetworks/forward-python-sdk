# The query library

!!! note "Unpublished, but stable"

    These operations use endpoints that are not part of Forward's published API
    description. They are stable and confirmed safe to depend on: Forward's own
    integrations use them, and publishing queries from code has no published
    alternative. Being outside the description simply means they are not covered
    by the generated reference, so the SDK models them by hand.

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
exists. Missing enclosing directories are created first, because Forward refuses
to stage a query whose directory does not exist. `dry_run_snapshot_id` validates
the queries against a real snapshot first, which catches a query that no longer
compiles before anyone else sees it. If anything fails, staged drafts and any
directories created for them are discarded, so a failed run does not leave
half-staged changes behind.

`publish()` also refuses when one of the paths already carries an uncommitted
draft, since a commit names paths rather than changes and would publish
someone else's half-finished edit alongside yours. Pass `overwrite_drafts=True`
to do it anyway.

Paths whose source is unchanged are reported as skipped rather than failing the
commit: publishing a directory where some files are identical is the normal case.

## Running an edit before committing it

Forward runs committed queries by ID, and inline source cannot pin its imports:
the synchronous endpoint resolves them against the library's head, and the
asynchronous one does not resolve them at all. So an edit to a helper that the
query imports several levels down cannot be tried without committing it, and a
baseline cannot be rerun once head has moved past it.

`bundle()` reads the query and everything it imports at one commit, replaces the
modules you supply, and merges the result into a single query that imports
nothing:

```python
from pathlib import Path

from forward_sdk import QueryRef

bundle = client.nqe.repo.bundle(
    "/MyOrg/Firewall Rules",
    commit_id=baseline_commit,
    overrides={"/MyOrg/Helpers/Policy Engine": Path("policy_engine.nqe").read_text()},
)
rows = client.nqe.run(QueryRef.inline(bundle.source, Device_Name_Equals="fw1"))
```

Modules keep private helpers under the same names, so each module's top-level
declarations are renamed with a per-module prefix and every reference is
rewritten to what it resolved to before. Anything the rewrite cannot rename
safely raises `NqeBundleError` rather than producing a query that means
something different.

Two things to check before trusting the numbers:

* Build a bundle of the unchanged commit and compare its rows with that commit
  run by ID. Identical rows show the merge preserved meaning on your query.
* Compare timings bundle against bundle. Forward compiles inline source on every
  request and caches the analysis of a committed query, so a bundle is slower
  than the same commit run by ID even when the query is identical.

Modules from Forward's own library (`@fwd/...`) are refused for now, because
bundling them would need that library's commit too.

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
