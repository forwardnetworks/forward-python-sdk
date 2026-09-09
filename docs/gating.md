# Availability: why a call can be refused

Not every endpoint is available to every caller. Three separate things can make
Forward refuse a request:

- **Licensing.** Some capabilities are licensed separately, and vulnerability
  analysis is the clearest example.
- **Deployment.** Forward runs both as a hosted service and self-hosted, and
  some features exist in only one. Forward AI is the clearest example: it is a
  hosted-service and bring-your-own-model capability, gated on an organization
  property, and absent from the published REST API. The SDK supports it as an
  [unpublished surface](unpublished.md); an organization without it is refused
  with a 403 carrying Forward's own explanation.
- **Role-based access control.** A user may not see a network, or may not hold
  the administrative role that user accounts, credentials and system settings
  require.

## The SDK cannot tell them apart

Forward reports all three the same way: an ordinary `401`, `403` or `404` with
its usual error body. There is no distinct status code, and no documented
machine-readable reason code that separates "your licence does not include this"
from "you may not see this" or "there is nothing here".

So the SDK does not pretend to distinguish them. There is no
`FeatureUnavailableError`, and the client never probes your instance at
construction to discover what it supports. Status maps to exception, and the
server's own explanation is handed to you:

```python
from forward_sdk import ForwardClient, ForwardPermissionError, ForwardNotFoundError

try:
    vulns = client.vulnerabilities.list(network_id="101")
except (ForwardPermissionError, ForwardNotFoundError) as error:
    print(error.status)  # 403 or 404
    print(error.error_info.message)  # Forward's own explanation
    print(error.reason)  # a reason code, when Forward sends one
    print(error.gating)  # ("license",) — what is known to gate this
```

`error.gating` is a **documentation hint** drawn from the table below, not a
diagnosis. An empty tuple does not mean the failure had some other cause, and a
non-empty one does not prove licensing was the reason. When you need certainty,
the answer is in your Forward instance: check the licence, the deployment, and
the user's role.

## What gates what

| Group | Licence | Deployment | Admin role |
| --- | :---: | :---: | :---: |
| Vulnerability analysis | ✅ | | |
| Forward AI | ✅ | ✅ | |
| System administration (CVE index) | ✅ | ✅ | ✅ |
| User accounts | | | ✅ |
| Credentials | | | ✅ |
| Jump servers | | | ✅ |
| Endpoint profiles | | | ✅ |
| Data connectors | | | ✅ |

Everything else in the published API is available to any authenticated user with
access to the network in question.

This table lives in `spec/gating.yaml` and is checked against the API
description by the test suite, so it cannot name a group that no longer exists.

## A licence that has expired

When an organization's licence lapses, Forward enters a read-only grace period:
existing data can still be read and queries still run, but nothing new is
collected. After the grace period, only an organization administrator can sign
in, and everyone else fails authentication. In the SDK that surfaces as
`ForwardAuthError` on requests that worked the day before, which is worth
distinguishing from a bad credential when you are diagnosing a sudden failure.

## Telling one refusal from another

A refusal carries no machine-readable code. Forward's access enforcer builds its
error body with `reason` set to `null`, so a status code cannot separate a
permission the account lacks from a feature its licence does not cover from a
setting that is switched off.

Its wording does separate them, and `error.denial` reads it:

```python
from forward_sdk import ForwardPermissionError

try:
    client.vulnerability_analysis.get_vulnerabilities(network_id="101")
except ForwardPermissionError as error:
    if error.denial:
        print(error.denial.kind, error.denial.detail)
```

| `kind` | What Forward said | What to tell an operator |
| --- | --- | --- |
| `rbac` | `Missing permission: X.Y` | The account lacks permission `X.Y` |
| `license` | `Unlicensed operation: X.Y` | The licence does not cover `X.Y` |
| `license_expired` | the licence has expired | Renew, or grant licence management |
| `org_setting` | `NAME is off for your organization` | An org admin controls `NAME` |
| `deployment_setting` | `NAME is off for your deployment` | A deployment admin controls `NAME` |
| `authority` | `... authority required` | A Forward-side role is needed |

These are the enforcer's own format strings, so they are as stable as anything
undocumented gets. They are still prose. Use `denial` to tell someone what to
fix, and not to decide what your code does next: `None` means the wording was
not recognised, which is not the same as the refusal having no cause. Branch on
the status code, and treat the kind as the explanation you show a human.
