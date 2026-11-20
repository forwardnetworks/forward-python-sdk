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
    vulns = client.vulnerability_analysis.get_vulnerabilities(network_id="101")
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
| Access control groups and device access labels | | | ✅ |
| Audit logs | | | ✅ |
| Organizations | | | ✅ |
| SAML settings | | | ✅ |
| Backend jobs | | | ✅ |
| Licensing | | | ✅ |
| Backups | | ✅ | ✅ |

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

## NQE data and the licence tier

An organization's licence tier also limits what NQE can read, and for parts of
the data model it does so **without an error**: Forward empties the restricted
fields before the query runs, so the query succeeds and the rows simply hold
nothing there. A sync that reads CVE findings on a tier without them gets an
empty list, not a refusal, so an empty answer from these paths is not proof
that there is nothing to find.

| Tier | Paths read as empty |
| --- | --- |
| B | `network.cveDatabase`, `cisDatabase`, `stigDatabase`, `endpoints`, `dataConnectors`, `extensions`, `externalSources`; on each device `cveFindings`, `cisFindings`, `aclEntries`, `natEntries`, `bgpRib`, `files`, `outputs`, `networkInstances`, `stp`, `securityPolicy`, `securityZones`, `hosts.securityZones`, `interfaces.acls` (and subinterfaces), `platform.osSupport`, `platform.components.support` |
| N, NP | `network.cveDatabase`, `cisDatabase`, `stigDatabase`; on each device `cveFindings`, `cisFindings`, `securityPolicy`, `securityZones`, `hosts.securityZones` |
| S (and no tier recorded) | `network.dataConnectors`, `extensions`, `externalSources`; on each device `bgpRib`, `files`, `outputs`, `networkInstances`, `stp` |
| NS, NSP | nothing |

Read from Forward's `NqeLicenseTierPolicyService`; the API does not report an
organization's tier. A whole query the tier does not cover, by contrast, is
refused with HTTP 403 ("Query ... is not permitted for this organization's
license tier", or "Custom Query ..." for one written in place), which
`error.denial` reads as kind `license` with the query's path, or its id
(`FQ_...` or `Q_...`) when Forward did not resolve a path, in `detail`.

Some of these fields hold data only for some devices even on a full tier:
`platform.osSupport` and `platform.components.support` are optional dates
(`lastMaintenanceDate`, `lastVulnerabilityDate`, `lastSupportDate`, with an
`announcementUrl`) that Forward fills only for vendors it has support data for,
and `cveFindings` lists CVEs ruled out as well as those that apply, so filter on
`isVulnerable`.

`device.securityPolicy` is behind an organization setting as well as the tier:
it exists in the data model only when `NQE_SECURITY_RULES_PANOS` or
`NQE_SECURITY_RULES_FORTIOS` is on (both default off; they can be set for the
deployment or, on premises, by an organization administrator), and holds
rules only for PAN-OS or FortiOS devices respectively. With both off, a query that names it does not
compile, which is why the data-model schema vendored here
(`spec/nqe-network-schema.json`, Forward's documentation build) has no such field.

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
| `rbac` | `Missing permission: X.Y`, or `No permission for network operation X` | The account lacks permission `X.Y` |
| `license` | `Unlicensed operation: X.Y` | The licence does not cover `X.Y` |
| `license` | `Query Q is not permitted for this organization's license tier` | The licence tier does not cover NQE query `Q` |
| `license_expired` | the licence has expired | Renew, or grant licence management |
| `org_setting` | `NAME is off for your organization` | An org admin controls `NAME` |
| `deployment_setting` | `NAME is off for your deployment` | A deployment admin controls `NAME` |
| `authority` | `... authority required` | A Forward-side role is needed |

These are the enforcer's own format strings, so they are as stable as anything
undocumented gets. They are still prose. Use `denial` to tell someone what to
fix, and not to decide what your code does next: `None` means the wording was
not recognised, which is not the same as the refusal having no cause. Branch on
the status code, and treat the kind as the explanation you show a human.

## Reading a setting before you call

`org_setting` refusals can be anticipated by reading the property. The config
listings expose it by lowercase name, but only under some filters: the default
(`CONFIGURABLE`) listing omits `ai_allowed`, while `filter="OFF"` lists every
property with its effective value, and `CONFIGURED` or `NONDEFAULT` list it only
when the organization has set it.

```python
cfg = client.configuration
cfg.get_org_config_values(filter="OFF")  # {"ai_allowed": False, ...}
```

An organization's own value wins over the global one (`describe_properties()`
resolves this). `ai_allowed` is the only thing the chat endpoints check at the
door (with the network's `VIEW_AI_CHATS` / `EDIT_AI_CHATS` permission), so with it
off every one of them is refused with `denial.kind == "org_setting"`; this was
seen live on one hosted organization, where it was `False` and the global value
`True`. Whether a model is actually available is decided separately, from the
`custom_ai_models` property and a configured provider, and Forward's source does
not expose that as an endpoint. So `ai_allowed` on does not promise an answer,
and the SDK cannot tell you in advance. That second half is from the source and
has not been seen live, since no checked organization had `ai_allowed` on.

To decide ahead of a call whether an account's roles allow it, see
[what an account may do](access.md).
