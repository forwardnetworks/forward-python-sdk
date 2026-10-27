# Network maps

A network map is a saved diagram of chosen devices: where each sits on the canvas,
how its card and links are drawn, and which folder it lives in. Forward redraws
it against every snapshot, so a map is a live diagram, not a picture. A network
can hold hundreds, such as one per branch.

These routes are **unpublished**: Forward's GUI uses them, but its documented API
does not list them. They are read-only here. Reading needs the
`VIEW_NETWORK_MAPS` role operation on the network (`client.access()` can tell
you ahead of time; see [What an account may do](access.md)).

```python
maps = client.network_maps.get_network_maps()  # summaries: id, name, pinned, who changed it
for info in maps.maps:
    print(info.id, info.name)

one = client.network_maps.get_network_map(map_id="12", with_="directory")
print(one.directory, [d.name for d in one.devices or []])  # the saved layout, no snapshot data

view = client.network_maps.get_network_map_snapshot(map_id="12")  # latest processed snapshot
print(len(view.devices or []), len(view.links or []), len(view.bgp_links or []))

tree = client.network_maps.get_network_map_directories()  # flat list, root "/" first
```

| Method | Returns |
| --- | --- |
| `get_network_maps` | Every map as a summary |
| `get_network_map` | One map as saved: device positions, display settings, rules. `with_="directory"` adds its folder |
| `get_network_map_snapshot` | One map joined with a snapshot: device inventory, physical links, interfaces, BGP links |
| `get_network_map_structures` | The inferred structure hierarchy of the map's devices in a snapshot |
| `get_network_map_directories` | The folders and which maps sit in each |
| `get_network_map_link_appearance_rules`, `get_network_map_link_appearance_rule` | How the map styles links |

- `snapshot_id` defaults to the network's latest processed snapshot, as elsewhere.
- Device names in a saved map are lower-case. A map device that is absent from the
  snapshot is left out of the snapshot view.
- Listing folders also needs the `NETWORK_MAP_DIRECTORIES` org property; with it off Forward answers 403.
- BGP links are empty when BGP is off for the organization or the map.
- `annotations` and `hiddenLinks` come back as plain dicts; their fields are not modelled.
