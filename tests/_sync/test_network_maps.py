# Generated from tests/_async/test_network_maps.py by scripts/unasync.py -- do not edit.
# Edit the async source and re-run: uv run python scripts/unasync.py

"""Network Maps reads: the routes, the parameters and the shapes Forward sends.

Fixtures are invented; they follow the shapes recorded in ``spec/unpublished.yaml``.
"""

from __future__ import annotations

from typing import Any

import pytest

from forward_sdk._sync.client import ForwardClient
from tests.conftest import Recorder, json_response

pytestmark = pytest.mark.anyio

MAPS = "/api/networks/101/maps"


def make_client(recorder: Recorder) -> ForwardClient:
    return ForwardClient(
        "https://forward.test",
        username="key",
        password="secret",
        rate_limit_rpm=None,
        network_id="101",
        transport=recorder.transport,
    )


ATTRIBUTION: dict[str, Any] = {
    "createdById": "11",
    "createdAt": "2026-01-02T03:04:05.123456789Z",
    "createdBy": "me@example.com",
}


class TestListing:
    def test_maps_parse_with_and_without_optional_fields(self, recorder: Recorder) -> None:
        recorder.add(
            "GET",
            MAPS,
            json_response(
                {
                    "maps": [
                        {"id": "0", "name": "Branch A", "pinned": True, **ATTRIBUTION},
                        {"id": "1", "name": "Branch B"},
                    ]
                }
            ),
        )
        with make_client(recorder) as client:
            listing = client.network_maps.get_network_maps()

        first, second = listing.maps
        assert (first.id, first.name, first.pinned, first.created_by) == (
            "0",
            "Branch A",
            True,
            "me@example.com",
        )
        assert second.pinned is None and second.created_by is None

    def test_directories_are_a_flat_list_root_first(self, recorder: Recorder) -> None:
        recorder.add(
            "GET",
            "/api/networks/101/map-directories",
            json_response(
                {
                    "directories": [
                        {"path": "/", "mapIds": ["1"]},
                        {"path": "/Branch", "mapIds": ["0"], **ATTRIBUTION},
                    ]
                }
            ),
        )
        with make_client(recorder) as client:
            result = client.network_maps.get_network_map_directories()

        assert [d.path for d in result.directories] == ["/", "/Branch"]
        assert result.directories[1].map_ids == ["0"]
        assert result.directories[0].created_by is None


class TestOneMap:
    def test_with_directory_is_sent_and_the_path_read(self, recorder: Recorder) -> None:
        recorder.add(
            "GET",
            f"{MAPS}/0",
            json_response(
                {
                    "id": "0",
                    "name": "Branch A",
                    "directory": "/Branch",
                    "devices": [{"name": "sw1", "x": 1.5, "y": 2.0}],
                    "deviceSettings": [{"name": "sw1", "x": 0.0, "y": 40.0, "showName": True}],
                    "bgpSettings": {"enabled": False},
                }
            ),
        )
        with make_client(recorder) as client:
            one = client.network_maps.get_network_map(map_id="0", with_="directory")

        assert recorder.query_for() == {"with": ["directory"]}
        assert one.directory == "/Branch"
        assert one.devices is not None and one.devices[0].x == 1.5
        assert one.device_settings is not None and one.device_settings[0].show_name is True

    def test_snapshot_view_defaults_to_the_latest_processed_snapshot(
        self, recorder: Recorder
    ) -> None:
        recorder.add(
            "GET",
            f"{MAPS}/0",
            json_response(
                {
                    "id": "0",
                    "name": "Branch A",
                    "devices": [{"name": "sw1", "type": "SWITCH", "x": 1.0, "y": 2.0}],
                    "links": [{"sourcePort": "sw1 e1", "targetPort": "rtr1 e1", "types": ["P"]}],
                    "bgpLinks": [
                        {
                            "node1": {"deviceName": "rtr1", "asNumber": 64512},
                            "node2": {"deviceName": "isp", "asNumber": 64513},
                            "peeringType": "EBGP",
                            "state": "ESTABLISHED",
                        }
                    ],
                }
            ),
        )
        with make_client(recorder) as client:
            view = client.network_maps.get_network_map_snapshot(map_id="0", snapshot_id="900")

        assert recorder.query_for() == {"view": ["snapshot"], "snapshotId": ["900"]}
        assert view.devices is not None and view.devices[0].type == "SWITCH"
        assert view.links is not None and view.links[0].source_port == "sw1 e1"
        assert view.bgp_links is not None and view.bgp_links[0].node2 is not None
        assert view.bgp_links[0].node2.as_number == 64513

    def test_snapshot_view_without_a_snapshot_sends_none(self, recorder: Recorder) -> None:
        recorder.add("GET", f"{MAPS}/0", json_response({"id": "0", "name": "Branch A"}))
        with make_client(recorder) as client:
            client.network_maps.get_network_map_snapshot(map_id="0")

        assert recorder.query_for() == {"view": ["snapshot"]}

    def test_structures_nest(self, recorder: Recorder) -> None:
        recorder.add(
            "GET",
            f"{MAPS}/0/structures",
            json_response(
                [
                    {
                        "type": "SITE",
                        "devices": ["a"],
                        "structures": [{"type": "RACK", "devices": ["a"]}],
                    }
                ]
            ),
        )
        with make_client(recorder) as client:
            roots = client.network_maps.get_network_map_structures(map_id="0", snapshot_id="900")

        assert recorder.query_for() == {"snapshotId": ["900"]}
        assert roots[0].structures is not None and roots[0].structures[0].type == "RACK"


class TestLinkAppearanceRules:
    RULE = {
        "id": "3",
        "selector": {"type": "BGP", "peeringType": "EBGP"},
        "strokeStyle": "DASHED",
    }

    def test_rules_are_wrapped(self, recorder: Recorder) -> None:
        recorder.add(
            "GET",
            f"{MAPS}/0/link-appearance-rules",
            json_response({"linkAppearanceRules": [self.RULE]}),
        )
        with make_client(recorder) as client:
            rules = client.network_maps.get_network_map_link_appearance_rules(map_id="0")

        assert rules.link_appearance_rules[0].selector.peering_type == "EBGP"

    def test_one_rule(self, recorder: Recorder) -> None:
        recorder.add("GET", f"{MAPS}/0/link-appearance-rules/3", json_response(self.RULE))
        with make_client(recorder) as client:
            rule = client.network_maps.get_network_map_link_appearance_rule(map_id="0", rule_id="3")

        assert rule.id == "3" and rule.stroke_style == "DASHED"
