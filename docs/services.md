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
