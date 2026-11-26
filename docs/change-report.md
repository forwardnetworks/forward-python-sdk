# Change reports

`client.change_reports.compare(base, after)` compares two snapshots and
returns one report: a verdict, then what changed, section by section. `after`
is a Predict prediction (the Predict case) or a later collection (before and
after a change). The Go SDK (`ChangeReports.Compare`) builds the same report. The
shared rules for its JSON and Markdown are still being agreed: schema_version `"1"`
is a draft until both SDKs ship it.

```python
from forward_sdk.change_report import Flow
from forward_sdk.trace import Endpoint, PathQuery, device, subnet

report = client.change_reports.compare(
    base_id,
    predicted_id,
    change_set_id=change_set.id,  # lets the security rules be read
    flows=[
        Flow(
            "app to db",
            PathQuery(from_=Endpoint(subnet("10.10.10.0/24")), to=Endpoint(device("db-1"))),
        )
    ],
    state=True,  # BGP sessions, LLDP, interface admin state, ACL entry counts
)
print(report.result)  # "regression", "no_regression" or "incomplete"
pr_comment = report.to_markdown()
archive = report.to_json()
```

## Arguments

`compare()` also takes:

- `network_id`: the client's configured network when omitted.
- `after_kind`: `"predicted"` or `"collected"`. When omitted, the report is a Predict
  comparison if the after snapshot's processing trigger is `PREDICT`.
- `section_timeout` (seconds, default 300): one budget for the checks wait, shared
  by all check types, and a separate one of the same length for the wait on the
  connectivity comparison.
- `created_at`: overrides the report's creation time.

A repeated flow name, an unknown `expectation`, `max_items` below 1 or an invalid
`after_kind` raises `ValueError` before any read. `compare()` first looks up the after
snapshot; an error there is raised too (`ForwardNotFoundError` if it is not in the
network), not reported as a section. In a Predict report without `change_set_id`,
`security_rules` is `unavailable`.

## The verdict

- `regression`: a check went from PASS to FAIL or ERROR, a flow expected to
  keep a path lost every valid path, or a requested state item was lost (a BGP
  session no longer established, an LLDP adjacency gone, an interface no
  longer admin-up).
- `incomplete`: something that decides the verdict could not be read or
  evaluated (the checks were refused, a check that passed before was not
  evaluated after (`TIMEOUT`, `NONE` or `REQUIRES_ADDITIONAL_SNAPSHOT_PROCESSING`),
  a flow's search did not finish). A refused or unfinished read never reads as
  `no_regression`, with one exception: a check type whose diff the Forward build
  does not serve is skipped without a reason, and the checks section stays `ok`.
  `incomplete` wins over `regression`: a real regression found alongside an
  unreadable section shows only in `reasons`.
- `no_regression`: everything that decides the verdict was read, and nothing
  regressed.

Only a check that was `PASS` can regress. One that goes from `PASS` to `SKIPPED` or
`UNLICENSED` is listed as `other` and does not affect the verdict; the checks
`counts` include `skipped`, `unlicensed` and `not_evaluated`.
`compare()` has no way to mark a state loss as expected, so every lost BGP session,
LLDP adjacency or admin-up interface counts as a regression.

Changes in counts (connectivity pairs, diff families, vulnerabilities, paths)
are reported but never decide the verdict.

## Sections

| Section | What it shows |
| --- | --- |
| checks | every check's status before and after, joined by check id |
| flows (opt-in) | per named flow, its paths by outcome before and after |
| state (opt-in) | BGP, LLDP, interface admin and ACL changes |
| connectivity | subnet pairs whose reachability changed, per location pair |
| routing | per device, routes added, removed and modified |
| config | devices whose configuration files changed |
| devices | devices added, removed and modified |
| security_rules (Predict) | firewall rules the change set adds, removes or modifies |
| vulnerabilities | CVEs introduced and resolved |
| diff_counts | how much changed per family, with whether the count is complete |
| predict (Predict) | the predicted snapshot's processing state |

A section that cannot be read appears as `unavailable` with its reason (`forbidden`,
`feature_gated`, `not_served`, `timeout` or `error`), and the Markdown footer lists it.
A check still `PROCESSING` when the budget runs out makes the whole checks section
`unavailable` (`timeout`). Data the report's rules do not allow, such as an unknown
check status, a repeated row or a check side with no status, makes that section
`unavailable` (`error`, "unexpected data from Forward"). `predict` and `security_rules`
read `not_applicable` when the after snapshot is a collection, and an opt-in section
that was not asked for reads `not_requested`. `ai_summary` is a reserved opt-in section
that `compare()` never reads, so it is always `not_requested`. Connectivity needs the
`LOCATION_CONNECTIVITY_DIFFS` org property, which only Forward support can turn on. Each
list is capped (`max_items`, default 100). Checks, flows and state are ordered by
severity first, so there a cap only ever drops the least severe rows; the other lists
are ordered by name.

Flows take an `expectation`: `"valid"` (the default; it must keep a path),
`"none"` (it is meant to have none, as for an isolation intent) or `"any"`. Only
`"valid"` can cause a regression. `"none"` is not enforced: like `"any"`, it does not
flag an isolated flow that gains or keeps a valid path, so check its row yourself. A
flow whose read does not finish makes the verdict `incomplete` whatever the
expectation.

Built from Forward's API, not the Predict UI's report. Preview: the report's
schema_version 1 is new, and its reads were checked against Forward's source
and a live read-only run on a collected pair of snapshots; a Predict comparison
has not yet run live.

## Source

- `src/forward_sdk/change_report.py`
- `src/forward_sdk/trace.py`
- `src/forward_sdk/_async/services/change_reports.py`
- `tests/fixtures/change_report/RULES.md`
