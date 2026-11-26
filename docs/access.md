# What an account may do

Forward has no call that answers "may this account do X on network Y?". It
answers by refusing: a 403 with `Missing permission: NetworkOperation.X`. To
decide ahead of time whether to offer an action, the SDK works the answer out the
way Forward's enforcer does, from the roles Forward reports for the account.

```python
access = client.access("101")  # one read of GET /users/current

access.can("NetworkOperation.TRIGGER_SNAPSHOT_COLLECTION")  # True / False / None
access.can("NetworkOperation.EDIT_NETWORK_SETTINGS", "202")  # another network
access.at_least("OPERATOR")  # the role ladder
```

Operations are named as Forward names them, so the name in a refusal can be pasted
straight into `can()`. The bare name (`TRIGGER_SNAPSHOT_COLLECTION`) works too.
`describe_operation(name)` and `operations()` list what Forward says about each:
read or write, the licence facet it needs, and the lowest network role that holds it
(`doc` is the `///` comment above the constant in Forward's source, empty when there is none).
A name the table does not define raises `ValueError` with a "did you mean" hint, so a
typo is not an answer of `None`.

```python
from forward_sdk import describe_operation

# Needs the table: without one this raises. See below.
describe_operation("NetworkOperation.TRIGGER_SNAPSHOT_COLLECTION")
# OperationInfo(name='TRIGGER_SNAPSHOT_COLLECTION', scope='network',
#   access_type='WRITE', license_facet='BASE',
#   lowest_network_role='WORKSPACE_OPERATOR', doc='Trigger or cancel a snapshot collection')
```

If you already hold the user, skip the call: `Access.from_roles(user.roles, network_id)`.
For an impersonated session also pass `impersonator=` (the support account's name from
`/users/current`); without it the impersonation guard below does not apply and a "no"
can be returned where `None` is right.

`may()` says why and which role the answer rested on; `can()` is its `allowed`. A
`Decision` is not a truth value (`if decision:` raises), because `None` would read
as no.

The roles are read once and kept on the client; `client.access(refresh=True)` reads
them again (the role table is a separate matter; see the end of this page). `GET /users/current` is unpublished, and some accounts are refused it
(403) or find it absent (404). That does not raise: every answer is `None` and the
reason says the roles could not be read. Bad credentials and server errors still raise.

## What the answers mean

- **`True`**: the account's roles allow it.
- **`False`**: they do not.
- **`None`**: the roles cannot settle it. That is the answer for roles that came back
  empty, for an account with a Forward-internal role, and for a role this table
  does not know. An empty answer is not a refusal.

`True` is not a promise that the call will work. A licence facet, an organization
property, or the state of the thing being changed can still refuse it, and Forward's
own refusal is the last word (see [availability](gating.md)). The facet is in
`describe_operation` so you can tell when a licence could be the reason.

## Sessions the roles do not describe

Two kinds of session get more than their roles show, so a "no" would be wrong and the
answer is `None` instead (a "yes" stays a yes, since they only add):

- An **impersonated** session. A Forward support account impersonating a user gains a
  few operations beyond that user's own. `/users/current` names the impersonator, and
  `client.access()` passes it on.
- A **Forward support** account (`supportedOrgIds` in its roles). Forward lets these
  through support-only routes in the orgs they support without checking an operation.

One limit the SDK cannot see: an org ADMIN is admin of **its own org** only, and `Access`
knows nothing of which org a network belongs to. Asking about another org's network gives
a wrong `True`.

## The rules, from Forward's source

- An **org admin** holds every operation; an org **read-only** role holds every read.
- A **network role** holds what its list says, and each role adds to the one below:
  `LIMITED_READ_ONLY`, `READ_ONLY`, `OPERATOR`, `WORKSPACE_OPERATOR`, `ADMIN`.
  `WORKSPACE_OPERATOR` can only be given on a workspace network.
- A **network operation** is checked against the role on that network.
- An **org operation** is allowed when the org role allows it or when **any** network
  role the account holds does.
- Some org operations are held by no network role. Only an org admin has those, except
  that an org read-only role also holds the ones that are reads.

The roles are the **session's** roles, as `GET /users/current` reports them. For
SAML and LDAP accounts they include roles from access control groups, and when
Forward's workspace role propagation is on (it is by default) a workspace shows its
parent's role if it has none of its own. Local and TACACS accounts get direct grants
only.

## Where the table comes from

From Forward's own source, which is the only place it exists: Forward serves no
endpoint for it and its API description carries no roles. The SDK reads three files
of the access package (`NetworkOperation`, `OrgOperation` and `NetworkRole`) from
a checkout. **The table is not shipped with the SDK**; it is Forward's data, so you
point the SDK at your copy:

```
export FORWARD_SOURCE=~/src/fwd              # a checkout, read directly
# or, where the checkout is not available:
python -m forward_sdk.rbac_dump --fwd ~/src/fwd > rbac.json
export FORWARD_RBAC_TABLE=rbac.json
```

or pass `table=RoleTable.from_checkout(...)` / `RoleTable.from_json(...)`
(`RoleTable` is in `forward_sdk.rbac`) to `client.access()`, `describe_operation()`
or `operations()`. If both variables are set, `FORWARD_RBAC_TABLE` wins and
`FORWARD_SOURCE` is ignored. A checkout must hold the files under
`app/src/main/java/com/forwardnetworks/cv/access`. Reading a checkout (including
with `rbac_dump`) also runs `git` to record its commit, so `git` must be on the
`PATH`.

A path that is set but wrong raises `ForwardConfigurationError`. So does a checkout the reader cannot follow: a
`NetworkRole.java` that builds a role with a stream, `filter(`, set arithmetic or
`allOf(` (the SDK refuses rather than guess), a role constant that is missing, a role
that names an operation that does not exist, or no operations found. A change in
Forward's source can break the reader until it is updated.

With no table set, `may()` and `can()` answer `None` and the reason says how to supply
one; `describe_operation()` and `operations()` raise `ForwardConfigurationError`. The
table records where it was read from (`table.source`: for a checkout, normally its
short `HEAD` commit, which does not show uncommitted edits). It is only as current as the
source it came from, and the environment lookup is cached for the life of the process
(per pair of variable values): after Forward changes a role, restart the process or pass
`table=` explicitly. `refresh=True` does not re-read it.

The role ladder (`at_least`) needs no table, treats an org admin as holding every network
role, and raises `ValueError` when no network id was given. `can()` raises the same for a network operation, except where the answer needs no
network (an org admin, or an org read-only role asking about a read), and answers `None`
when there is no table. `operations(scope)` takes `"network"`, `"org"` or nothing; any other
value returns an empty list. System-level operations (those of
Forward's own staff) are not modelled.

## Source

- `src/forward_sdk/access.py`
- `src/forward_sdk/rbac.py`
- `src/forward_sdk/rbac_dump.py`
- `src/forward_sdk/_access.py`
- `src/forward_sdk/_role_table.py`
- `src/forward_sdk/_sync/client.py`
