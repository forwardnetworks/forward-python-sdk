# Unpublished endpoints

Most of this SDK is generated from the OpenAPI description Forward produces from
its own server code. Many endpoints are not in that description (the operation
table marks hundreds of them `stability="unpublished"`) but are supported here
anyway. The table below lists the main groups, not every operation.

## Why

They have no published equivalent, and real integrations depend on them.
Publishing NQE queries from code is the main example: it is how an integration
keeps the queries it ships in step with the code that consumes them.

## What that means for you

Forward's own integrations depend on some of these endpoints (the Forward NetBox
and Nautobot plugins use the NQE repository ones), and the SDK's tests record
an example response shape for each of them. That is
not a stability promise for every row: Forward does not publish or version them.
Rows marked "from source, not run live" were read from Forward's source and have
not been exercised against an instance. Some response fields are labelled
Preview in the spec because Forward binds them but leaves them out of its API
description.

Being unpublished means only that they are absent from the OpenAPI description
Forward generates from its server, so:

- The SDK describes their shapes by hand, in `spec/unpublished.yaml`, rather
  than deriving them.
- They will not appear in Forward's generated API reference.
- A change to one would not show up in a spec diff, so it would not be caught by
  the drift check that covers the published API. Instead, an example response
  shape is recorded in the spec for each unpublished operation, and
  `tests/spec/test_unpublished_shapes.py` checks that every one has an example and that
  the NQE repository ones parse with the SDK's models; the rest are checked as plain
  dictionaries.

They are marked `stability="unpublished"` in the operation table, so nothing
lands here by accident.

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
| Raw snapshot artifacts | `GET /snapshots/{id}/files[/{fileName}]` | `client.snapshots.files()`, `.file()` |
| Processing exceptions | `GET /snapshots/{id}/exceptions`, `?view=json` | `client.snapshots.exceptions()`, `.exceptions_text()` |
| Reprocess a snapshot in place | `POST /snapshots/{id}?action=invalidate[&reprocess=true]`, `?action=reprocess` | `client.snapshots.invalidate()`, `.reprocess()` |
| Tenable VM scanners: list, add, change, delete, collect, test (read from Forward source; the list and its row shape seen live on 26.11) | `/vulnerability-scanners[/{name}][?action=collect]`, `/vulnerability-scanner-tests` | `client.integrations.*_vulnerability_scanner(s)` |
| A network's scanners and include switch (read from Forward source; the list and its row shape seen live on 26.11) | `/networks/{id}/vulnerability-scanners[/{name}]` | `client.integrations.list_network_vulnerability_scanners()`, `set_network_vulnerability_scanner_include()` |
| Org-level HTTP credentials (read from Forward source; the list and one read seen live on 26.11) | `/http-credentials[/{credentialId}]` | `client.credentials.*_org_http_credential(s)` |
| MCP server (session-based; seen live on 26.10.6) | `POST`/`DELETE /mcp` | `client.mcp` |
| Export org library query sources (from source, not run live) | `POST /users/current/nqe/changes?action=export` | `client.nqe.repo.export_org_sources()` |
| Export Forward Library query sources (from source, not run live) | `POST /nqe/repos/fwd/commits/head/queries?action=export` | `client.nqe.repo.export_forward_sources()` |
| Software Central connection test, on-premises (from source, not run live) | `POST /fwd-app-connection?action=test` | `client.system_administration.test_fwd_app_connection()` |
| fwd.app proxy settings, on-premises (from source, not run live) | `GET /fwd-app-proxy` | `client.system_administration.get_fwd_app_proxy()` |
| CVE index self-update, on-premises (from source, not run live) | `POST /cve-index?action=update` | `client.system_administration.update_cve_index_from_software_central()` |
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
| An NQE check's rows (seen live on 26.11) | `POST /nqe/queryResults?snapshotId=&resultKey=` | `client.nqe.check_result()` |
| BGP collection requirements (served on 26.11, empty list seen live) | `GET /networks/{id}/bgp-collection-requirements` | `client.bgp_advertisements.get_bgp_collection_requirements()`, `client.change_sets.bgp_advertisement_readiness()` |
| Edit a check in place (read from Forward source, not run live) | `PATCH /snapshots/{id}/checks/{checkId}` | `client.checks.update_check()` |
| BGP advertisements | `.../draft/devices/{name}/bgp-advertisements?action=...` and `?view=diffs`; received: `/networks/{id}/devices/{name}/bgp-advertisements?vrf=&prefix=` (served on 26.11, empty list seen live; row shape from source) | `client.bgp_advertisements`, `handle.verify_bgp_staging()` |
| Change-set directories | `/networks/{id}/change-set-directories[/{path}?action=...]` | `client.change_set_directories` |
| SNMP credentials | `/networks/{id}/snmpCredentials[/{id}]` | `client.snmp_credentials` |
| Collector binding and health | `/networks/{id}/collector` | `client.collector_binding` |
| Cloud accounts | `/networks/{id}/cloudAccounts[...]`, discovery, tests, region lookups | `client.cloud_accounts` |
| User event stream | `GET /users/current/events` (Server-Sent Events) | `client.user_events.stream()` |
| Predict AI assists | `.../cli-assists`, `.../overview-assists`, `/diffs/{a}/{b}/{config,impact}-summary-assists` | `client.predict_assist` |
| Webhooks | `/webhooks`, `/webhooks/{name}`, `?action=test`, `/webhook-types/{type}?view=default-templates` | `client.webhooks` |
| Synthetic devices: backdate, NQE-derived connections, L2VPN batch add, internet suggestions | `.../{family}?op=backdate`, `?action=computeNqeBasedConnections`, `/l2-vpns?action=addBatch`, `/internet-node/connection-suggestions` | the family's service |
| Adjacent networks | `/networks/{id}/adjacent-networks[/{name}[/connections]]` | `client.adjacent_networks` |
| T-API optical containers | `/networks/{id}/tapi-network-containers[/{name}]` | `client.tapi_network_containers` |
| Dashboards and display settings | `/networks/{id}/dashboards[/{id}[/display-settings]]`, `?type=DEFAULT`, `?action=removePanels` | `client.dashboards` |
| NQE panels and metric values | `/nqe-panels[/{id}]`, `?action=delete`, `/networks/{id}/nqe-panels?resultKey=`, `?action=preview` | `client.nqe_panels` |
| Execution result key | `GET .../nqe-executions/{key}?for=ui` | `execution.result_key()` |
| Security zones | `/networks/{id}/security-zones[/...]` | `client.security_zones` |
| Security matrix filters (CRUD) | `/networks/{id}/securityMatrixFilters[/{id}]` | `client.security_matrix_filters` |
| Security matrix | `/networks/{id}/security-matrix` | `client.security_matrix` |
| Resource pool analysis and connectivity | `/networks/{id}/resource-pools?action=analyze`, `/networks/{id}/resource-pool-connectivity-details` | `client.resource_pools` |
| Blast radius, host-centric variant, XLSX report | `/networks/{id}/blast-radius[-report]?type=host-centric` | `client.blast_radius` |
| Internet exposure and exposed hosts | `/networks/{id}/internet-exposure`, `/snapshots/{id}/internetNode/exposed-hosts[/{id}]` | `client.internet_exposure` |
| Connectivity tests: start, stop, results, phase breakdown | `/networks/{id}/connectivityTests[/{name}][/start\|/stop]`, `?type=endpoint` | `client.connectivity_tests` |
| Listing org, global and support-scoped properties | `GET /config`, `/global-config`, `/orgs/{id}/config` (`?filter=`) | `client.configuration.get_*_config_values()`, `describe_properties()` |
| Org, global and deployment configuration | `/config/{p}`, `/global-config/{p}`, `/orgs/{org}/config/{p}`, `/deployment-config[/{p}]` | `client.configuration`, `config_value()` |
| One network's property | `/networks/{id}/config/{p}` (GET, PUT `?value=`, DELETE) | `client.configuration.get_network_config_property()` and the set and delete |
| Client packages (headless collector) | `GET /software/client?type=` | `client.client_software.download()`, `get_client_package()` |

The published `POST /snapshots/{id}?action=computeAdvancedReachability` does the
same work as the reachability job without progress polling; prefer it when you
do not need progress.

The Predict families are described in [Rehearsing a change](predict.md),
including the traps a consumer met while working around their absence.

The Forward AI endpoints carry a second condition beyond being unpublished: they
require the `AI_ALLOWED` organization property, so most organizations are
refused with a 403 on every Forward AI operation whatever their licence otherwise covers. See
[Forward AI](ai.md).

## Source

- `spec/unpublished.yaml`
- `src/forward_sdk/_generated/operations.py`
- `tests/spec/test_unpublished_shapes.py`
