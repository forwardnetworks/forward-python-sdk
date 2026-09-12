# The API groups

Every operation in Forward's API is reachable from the client. Services are
grouped the way Forward's own documentation groups them.

## Curated services

These are hand-written, because they are where integrations spend their time.
Their methods have names chosen for readability and add behaviour the raw
endpoints do not, such as waiting for a snapshot to finish processing.

| Attribute | Covers |
| --- | --- |
| `client.networks` | Networks and workspaces, plus `client.version()` |
| `client.snapshots` | Snapshots: listing, health, upload, export, waiting |
| `client.devices` | Devices and their collected files |
| `client.device_tags` | Tags and their device membership |
| `client.nqe` | Running queries, and `client.nqe.repo` for the query library |
| `client.ai` | Forward AI, asking questions about a network in plain language |

See [snapshots](snapshots.md) and [the NQE guide](nqe/index.md).

## Generated services

The remaining groups are generated from Forward's API description. Their method
names are the operation ids in Python casing, so each maps directly onto
Forward's API documentation: `getLocations` becomes `get_locations`.

| Attribute | Group | Notes |
| --- | --- | --- |
| `client.aliases` | Aliases | |
| `client.checks` | Checks | |
| `client.classic_devices` | Classic Devices | |
| `client.collection_schedules` | Collection Schedules | |
| `client.collector_tasks` | Collector Tasks | |
| `client.credentials` | Credentials | Admin role |
| `client.data_connectors` | Data Connectors | Admin role |
| `client.encryptors` | Encryptors | |
| `client.endpoint_profiles` | Endpoint Profiles | Admin role |
| `client.internet_node` | Internet Node | |
| `client.intranet_nodes` | Intranet Nodes | |
| `client.jump_servers` | Jump Servers | Admin role |
| `client.l2vpns` | L2VPNs | |
| `client.l3vpns` | L3VPNs | |
| `client.legacy_collection` | Legacy Collection | Deprecated by Forward |
| `client.network_endpoints` | Network Endpoints | |
| `client.network_locations` | Network Locations | |
| `client.network_topology` | Network Topology | |
| `client.path_search` | Path Search | |
| `client.system_administration` | System Administration | Licence, deployment, admin |
| `client.user_accounts` | User Accounts | Admin role |
| `client.vulnerability_analysis` | Vulnerability Analysis | Licence |
| `client.wan_circuits` | WAN Circuits | |
| `client.change_sets` | Change Sets | Unpublished; `PREDICT_MODELING` org property |
| `client.snapshot_diffs` | Snapshot Diffs | Unpublished; connectivity needs `LOCATION_CONNECTIVITY_DIFFS` |
| `client.firewall_predict` | Firewall Predict | Unpublished; `FIREWALL_PREDICT` org property |
| `client.webhooks` | Webhooks | Unpublished; writes need `OUTBOUND_CONNECTIONS` |
| `client.configuration` | Configuration | Unpublished; org admin or Forward support |
| `client.snapshot_diff_details` | Snapshot Diff Details | Unpublished; per-area RBAC |
| `client.bgp_advertisements` | BGP Advertisements | Unpublished; `PREDICT_MODELING` org property |
| `client.change_set_directories` | Change Set Directories | Unpublished; `PREDICT_MODELING` org property |
| `client.predict_assist` | Predict Assist | Unpublished; `AI_ALLOWED` and `PREDICT_AI_ASSIST` |
| `client.snmp_credentials` | SNMP Credentials | Unpublished; device-credential permissions |
| `client.collector_binding` | Collector Binding | Unpublished; `VIEW_COLLECTORS` to read, collection settings to write |
| `client.cloud_accounts` | Cloud Accounts | Unpublished; collection-source permissions |
| `client.user_events` | User Events | Unpublished; any user |
| `client.adjacent_networks` | Adjacent Networks | Unpublished; collection-source permissions |
| `client.tapi_network_containers` | TAPI Network Containers | Unpublished; collection-source permissions; hand-written (multipart upload) |
| `client.dashboards` | Dashboards | Unpublished; network view to read, edit to write |
| `client.nqe_panels` | NQE Panels | Unpublished; `NQE_DASHBOARD_PANELS` to write, `NQE_DASHBOARD_METRIC_PANELS` for metric values |
| `client.security_zones` | Security Zones | Unpublished; `VIEW_SECURITY_ANALYSIS` |
| `client.security_matrix_filters` | Security Matrix Filters | Unpublished; `VIEW_SECURITY_ANALYSIS` to read, `EDIT_SECURITY_MATRIX_FILTERS` to write |
| `client.security_matrix` | Security Matrix | Unpublished; `VIEW_SECURITY_ANALYSIS` |
| `client.resource_pools` | Resource Pools | Unpublished; `VIEW_SECURITY_ANALYSIS` |
| `client.blast_radius` | Blast Radius | Unpublished; `VIEW_SECURITY_ANALYSIS` |
| `client.internet_exposure` | Internet Exposure | Unpublished; `VIEW_SECURITY_ANALYSIS` |

"Notes" says what is known to gate a group. It is a hint for interpreting a
refusal, not a guarantee; see [availability](gating.md).

### One name that misleads

`network_locations` returns the **organisation's** locations, not the sites
present in a given network. It is an address book that an org maintains and that
devices may reference, so it is neither scoped to the network you pass nor
limited to places that have equipment. On one live org it held 402 locations for
a 37-device network, 389 of them with no device at all.

If what you want is where a network's devices actually are, derive it from the
devices rather than from this list.

## Common conventions

**Network.** Any method taking `network_id` falls back to the client's network,
so you can set it once and stop repeating it.

```python
client = ForwardClient.from_env(network_id="101")
client.network_locations.get_locations()
```

**Snapshot.** Any method taking `snapshot_id` runs against the network's latest
processed snapshot when you omit it.

**Parameter names.** Query parameters use Python casing: `maxResults` becomes
`max_results`. Two of Forward's names are Python keywords, so `with` and `from`
become `with_` and `from_`.

**Bodies.** Methods that send a body take it as `body`, passed through as JSON.
The API reference for each operation gives its shape.

**Streaming.** Methods whose response is not JSON, such as exporting the CVE
index, return an iterator of bytes.

## Security analysis

Forward's blast-radius, security-matrix and internet-exposure views sit
entirely outside the published API; only `client.vulnerability_analysis`
(CVE-driven device impact) is published. The group mirrors the app closely:

- **Security zones.** `security_zones.get_security_zones()` maps every device
  to its zones; `get_security_zone(device_name, zone_name)` gives one zone's
  VRFs, interfaces, subnets and a small topology sketch scoped to it.
- **Resource pools.** A `ResourcePool` is one of three shapes, chosen by
  `type`: `DEVICE_ZONE` (`device` + `zone`), `ON_PREM` (`devices` + `vrfs` +
  `subnets`), or `CLOUD` (`subnets` + `securityGroups`). It is the unit
  everything else here compares: the security matrix runs on a list of them,
  blast radius reads from one, and `resource_pools.analyze_resource_pool()`
  reports which device/VRF/subnet combinations a set of filters actually
  matches before you commit to a pool.
- **Security matrix.** `security_matrix_filters` is full CRUD over saved
  `{name, resourcePools, protocolExclusions, timeoutMins}` filters;
  `security_matrix.get_security_matrix(filter_id=...)` runs a saved one,
  `get_anonymous_security_matrix(body=...)` runs the same shape without
  saving it. Both return a `matrix[i][j]` of `connectivityLevel` plus a
  `sampleQuery` illustrating it, `NO_ROUTE` through `OPEN`.
- **Blast radius.** `blast_radius.get_blast_radius(body={"source": ...,
  "dstSubnets": [...]})` samples connectivity from one `LocationFilter`
  location to a set of IPv4 subnets. `source` reuses the published
  `LocationFilter` union (`DeviceFilter`, `HostFilter`, `SecurityZoneFilter`,
  and so on) already used elsewhere in the SDK. `get_host_centric_blast_radius`
  takes the same body and breaks the result out per destination host, with
  vulnerability counts when a scanner is configured; it shares its path with
  the plain form, dispatched by a fixed query Forward adds automatically.
  `get_host_centric_blast_radius_report` returns the same computation as an
  XLSX workbook, streamed like any other binary export.
- **Internet exposure.** `internet_exposure.get_internet_exposure()` lists the
  interfaces the internet node receives traffic on, or why that could not be
  computed (`error`, e.g. `PENDING_ADVANCED_REACHABILITY` -- trigger it with
  `client.snapshots.compute_advanced_reachability()` and poll). The exposed-
  hosts family hangs off the snapshot directly rather than the network:
  `get_internet_exposed_hosts(snapshot_id=...)` lists hosts reachable from the
  internet, with per-scanner vulnerability counts when Rapid7 or Tenable is
  connected; `get_internet_exposed_host_connectivity(snapshot_id, exposed_host_id)`
  samples the path to one of them.

Every result here can come back `timedOut: true` on a large or slow snapshot;
the shape you asked for is still there, just possibly incomplete.

## Synthetic devices

Forward models what it cannot collect as synthetic devices: the internet, other
sites' intranets, provider L2VPNs and L3VPNs, WAN circuits, encryptors, adjacent
networks and T-API optical networks. The published CRUD for the first six is
generated as usual (`client.internet_node`, `client.intranet_nodes`,
`client.l2vpns`, `client.l3vpns`, `client.wan_circuits`, `client.encryptors`).
The rest of the surface is unpublished and sits on the same services:

- **Backdating.** A change to a synthetic device applies to the network's next
  snapshot. `backdate_*(snapshot_id=...)` applies the changes staged since the
  last snapshot to an existing one and every newer one, which invalidates them
  for reprocessing. It needs both the collection-source and the
  snapshot-invalidation permissions.
- **NQE-derived connections.** An internet node, intranet node, L2VPN, L3VPN or
  adjacent network may carry a `queryId`: a committed library query whose rows
  define its connections, recomputed on each snapshot. `compute_*_connections(
  query_id=...)` previews what that query produces on the latest processed
  snapshot without saving anything. Failure is reported in the body
  (`error.status`, one of `NO_LATEST_SNAPSHOT`, `QUERY_MISSING`,
  `QUERY_RUN_ERROR`, `MISSING_REQUIRED_COLUMNS`, `COLUMN_DATATYPE_MISMATCH`,
  `INVALID_IDENTIFIER`), not as an HTTP error.
- **Batch add.** `client.l2vpns.add_l2_vpns(body=[...])` adds several L2VPNs
  without replacing the existing ones.
- **Suggestions.** `client.internet_node.get_internet_connection_suggestions()`
  lists interfaces in the latest processed snapshot that look like internet
  uplinks; an empty object when none do.
- **Adjacent networks** (`client.adjacent_networks`) are networks Forward does
  not collect, reached over L3 WAN links, with the same connection model as
  intranet nodes plus `ownedSubnets`.
- **T-API containers** (`client.tapi_network_containers`) are built from
  uploaded T-API topology documents: `put(name, files)` takes paths or
  `(filename, bytes)` pairs and merges them into one model.

Not covered: the internet exposed-hosts reads under
`/snapshots/{id}/internetNode/exposed-hosts`, which belong to the security
analysis family (blast radius, security zones, exposure) and will land with it.

## Dashboards and NQE panels

An NQE panel is a committed library query with a presentation, tabular or
metric; a dashboard is a layout of widgets over panels.

```python
panel = client.nqe_panels.create_nqe_panel(
    body={
        "name": "bgp-sessions",
        "queryId": "Q_...",  # committed; query text is refused
        "config": {
            "type": "TABULAR",
            "columnOrder": ["device", "peer", "state"],
            "visibleColumns": [{"name": "device", "width": 200}],
        },
    }
)
dashboard_id = client.dashboards.create_dashboard(body={"name": "Operations"})  # a bare string
client.dashboards.update_dashboard(
    dashboard_id=dashboard_id,
    body={
        "layout": [
            {"type": "ROW", "x": 0, "y": 0, "w": 12, "h": 1, "title": "Core"},
            {"type": "PANEL", "x": 0, "y": 1, "w": 6, "h": 4, "panelId": f"NQE_PANEL_{panel.id}"},
        ]
    },
)
```

Things the shapes do not say:

- A layout replaces the whole layout. Each panel widget carries a
  server-assigned `id` after the first save; echo it to keep that embedding,
  omit it to create a new one.
- `PATCH /nqe-panels/{id}` is flat: configuration fields sit beside `name` and
  `description`, and a field for the other kind of panel is refused. The query
  and the kind cannot be changed.
- A panel in use cannot be deleted (409); `remove_panels_from_dashboards` takes
  it out of every dashboard in a network first.
- Metric values (`get_nqe_panel_metrics`, `preview_nqe_metric`) are computed
  over one execution's result, named by its result key. Forward's published
  execution status omits that key; `execution.result_key()` reads the variant
  that carries it. Metric values are gated by the
  `NQE_DASHBOARD_METRIC_PANELS` org property, which an org admin cannot set,
  so those two operations were verified against Forward's source rather than
  a live instance.
- Forward's own dashboards (`list_default_dashboards`) have negative ids and
  refuse edits.

## Path search

```python
result = client.path_search.get_paths(
    src_ip="10.0.1.5",
    dst_ip="10.0.2.7",
    ip_proto=6,
    dst_port="443",
    max_results=5,
)
for path in result.info.paths or []:
    print(path)
```

Bulk search takes many queries in one request, and `get_paths_bulk_seq` streams
results as they are found.

## Checks

```python
client.checks.get_checks(snapshot_id="101", type=["NQE", "Predefined"])
client.checks.get_available_predefined_checks()
```

Parameters that Forward accepts more than once take a list.
