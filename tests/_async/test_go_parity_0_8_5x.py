"""Go SDK v0.8.51 to v0.8.58: the routes and composites Python mirrors.

Expectations come from Forward's controllers (paths, methods, parameters) and
the documented behaviour, not from the Python code.
"""

from __future__ import annotations

import json

import httpx
import pytest

from forward_sdk._async.client import AsyncForwardClient
from forward_sdk.classic_devices import ClassicDeviceWithCredential
from forward_sdk.errors import ForwardBadRequestError
from tests.conftest import Recorder, json_response

pytestmark = pytest.mark.anyio

TAGS = "/api/networks/101/device-tags"


def make_client(recorder: Recorder) -> AsyncForwardClient:
    return AsyncForwardClient(
        "https://forward.test",
        username="k",
        password="s",
        rate_limit_rpm=None,
        network_id="101",
        transport=recorder.transport,
    )


@pytest.mark.parametrize(
    ("method", "action", "args"),
    [
        ("add_tags_to_devices", "addBatchTo", ({"devices": ["r1"], "tags": ["t"]},)),
        ("remove_tags_from_devices", "removeBatchFrom", ({"devices": ["r1"], "tags": ["t"]},)),
        ("remove_all_from_devices", "removeAllFrom", (["r1"],)),
        ("replace_all_for_devices", "replaceAllFor", ({"devices": ["r1"], "tags": ["t"]},)),
    ],
)
async def test_tag_writes_can_take_effect_from_a_snapshot(
    method: str, action: str, args: tuple[object, ...]
) -> None:
    recorder = Recorder().add("POST", TAGS, httpx.Response(204))
    async with make_client(recorder) as client:
        await getattr(client.device_tags, method)(*args, snapshot_id="s9")
        await getattr(client.device_tags, method)(*args)

    with_snapshot, without = recorder.requests
    assert with_snapshot.url.params["action"] == action
    assert with_snapshot.url.params["snapshotId"] == "s9"
    assert "snapshotId" not in without.url.params


async def test_tags_with_devices_can_be_read_as_of_a_snapshot() -> None:
    recorder = Recorder().add(
        "GET", TAGS, json_response({"tags": [{"name": "t", "devices": ["r1"]}]})
    )
    async with make_client(recorder) as client:
        tags = await client.device_tags.list(with_devices=True, snapshot_id="s9")
        with pytest.raises(ValueError, match="with_devices"):
            await client.device_tags.list(snapshot_id="s9")

    assert tags == [{"name": "t", "devices": ["r1"]}]
    assert len(recorder.requests) == 1
    params = recorder.requests[0].url.params
    assert params["with"] == "devices" and params["snapshotId"] == "s9"


@pytest.mark.parametrize(
    ("method", "kind"),
    [("delete_controller_device", "controllers"), ("delete_managed_device", "managed-devices")],
)
async def test_one_member_of_a_controller_managed_setup_can_be_removed(
    method: str, kind: str
) -> None:
    path = f"/api/networks/101/controller-managed-setups/aci-1/{kind}/apic-2"
    recorder = Recorder().add("DELETE", path, httpx.Response(204))
    async with make_client(recorder) as client:
        await getattr(client.collection_sources, method)(setup_name="aci-1", name="apic-2")

    assert recorder.requests[-1].method == "DELETE"
    assert recorder.requests[-1].url.path == path


async def test_cve_detection_logic_is_read_by_an_upper_case_id() -> None:
    recorder = Recorder().add(
        "GET",
        "/api/cves/CVE-2024-20399/detection-logic",
        json_response(
            {
                "cveId": "CVE-2024-20399",
                "sourceType": "NQE_QUERY",
                "source": "foreach d in network.devices\n",
            }
        ),
    )
    async with make_client(recorder) as client:
        logic = await client.vulnerability_analysis.get_cve_config_detection_logic(
            cve_id=" cve-2024-20399 "
        )

    assert logic.source_type == "NQE_QUERY"
    assert logic.source == "foreach d in network.devices\n"


async def test_a_cve_without_configuration_dependent_impact_has_no_logic() -> None:
    recorder = Recorder().add(
        "GET", "/api/cves/CVE-2021-0001/detection-logic", json_response({"cveId": "CVE-2021-0001"})
    )
    async with make_client(recorder) as client:
        logic = await client.vulnerability_analysis.get_cve_config_detection_logic(
            cve_id="CVE-2021-0001"
        )

    assert logic.source_type is None and logic.source is None


@pytest.mark.parametrize(
    "bad", ["", "2024-20399", "CVE-24-20399", "CVE-2024-399", "CVE-2024-20399x"]
)
async def test_a_malformed_cve_id_is_refused_before_a_request(bad: str) -> None:
    recorder = Recorder()
    async with make_client(recorder) as client:
        with pytest.raises(ValueError, match="CVE id"):
            await client.vulnerability_analysis.get_cve_config_detection_logic(cve_id=bad)
    assert recorder.requests == []


CREDENTIALS = "/api/networks/101/http-credentials"
DEVICES = "/api/networks/101/classic-devices"


async def test_an_api_key_device_is_created_with_its_own_credential() -> None:
    recorder = (
        Recorder()
        .add(
            "POST",
            CREDENTIALS,
            json_response(
                {"id": "H-7", "name": "k8s token", "type": "API_KEY", "password": "H-7-secret-ref"}
            ),
        )
        .add(
            "POST",
            DEVICES,
            json_response(
                {"name": "k8s1", "type": "k8s_cni_control_plane_api", "host": "192.0.2.10"}
            ),
        )
    )
    async with make_client(recorder) as client:
        made = await client.networks.create_classic_device_with_api_key(
            {"name": "k8s token", "password": "bearer-token"},
            {"name": "k8s1", "type": "k8s_cni_control_plane_api", "host": "192.0.2.10"},
        )

    credential_request, device_request = recorder.requests
    assert json.loads(credential_request.content) == {
        "name": "k8s token",
        "password": "bearer-token",
        "type": "API_KEY",
    }
    assert json.loads(device_request.content)["httpCredentialId"] == "H-7"
    assert isinstance(made, ClassicDeviceWithCredential)
    assert made.credential.id == "H-7" and made.device.name == "k8s1"


async def test_a_failed_device_create_reports_the_credential_left_behind() -> None:
    recorder = (
        Recorder()
        .add(
            "POST",
            CREDENTIALS,
            json_response(
                {"id": "H-8", "name": "k", "type": "API_KEY", "password": "H-8-secret-ref"}
            ),
        )
        .add("POST", DEVICES, json_response({"message": "Invalid host"}, status=400))
    )
    async with make_client(recorder) as client:
        with pytest.raises(ForwardBadRequestError) as caught:
            await client.networks.create_classic_device_with_api_key(
                {"name": "k", "password": "t"},
                {"name": "k8s1", "type": "k8s_cni_control_plane_api", "host": "192.0.2.10"},
            )

    assert caught.value.http_credential.id == "H-8"  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("credential", "device", "message"),
    [
        ({"name": "k", "password": "t", "type": "LOGIN"}, {"name": "d", "host": "h"}, "API_KEY"),
        (
            {"name": "k", "password": "t"},
            {"name": "d", "host": "h", "httpCredentialId": "H-1"},
            "httpCredentialId",
        ),
        # Go's validateClassicDevice: no credential (it holds the token) for an
        # impossible device.
        ({"name": "k", "password": "t"}, {"name": "d"}, "name and a host"),
        ({"name": "k", "password": "t"}, {"name": " ", "host": "h"}, "name and a host"),
    ],
)
async def test_a_conflicting_request_is_refused_before_anything_is_created(
    credential: dict[str, str], device: dict[str, str], message: str
) -> None:
    recorder = Recorder()
    async with make_client(recorder) as client:
        with pytest.raises(ValueError, match=message):
            await client.networks.create_classic_device_with_api_key(credential, device)
    assert recorder.requests == []


async def test_remove_all_sends_the_device_set_forward_reads() -> None:
    recorder = Recorder().add("POST", TAGS, httpx.Response(204))
    async with make_client(recorder) as client:
        await client.device_tags.remove_all_from_devices(["r1", "r2"])

    # DeviceTagController.removeAllDeviceTagsFromDevices takes @RequestBody DeviceSet.
    assert json.loads(recorder.requests[-1].content) == {"devices": ["r1", "r2"]}


async def test_a_blank_snapshot_id_is_refused_rather_than_sent() -> None:
    recorder = Recorder()
    async with make_client(recorder) as client:
        with pytest.raises(ValueError, match="blank"):
            await client.device_tags.add_tags_to_devices(
                {"devices": ["r1"], "tags": ["t"]}, snapshot_id=" "
            )
    assert recorder.requests == []


async def test_a_cancelled_device_create_still_reports_the_credential() -> None:
    def cancel(request: httpx.Request) -> httpx.Response:
        raise KeyboardInterrupt  # stands in for any non-Forward interruption

    recorder = Recorder().add(
        "POST",
        CREDENTIALS,
        json_response({"id": "H-9", "name": "k", "type": "API_KEY", "password": "ref"}),
    )
    recorder.default = cancel
    async with make_client(recorder) as client:
        with pytest.raises(KeyboardInterrupt) as caught:
            await client.networks.create_classic_device_with_api_key(
                {"name": "k", "password": "t"},
                {"name": "k8s1", "type": "k8s_cni_control_plane_api", "host": "192.0.2.10"},
            )

    assert caught.value.http_credential.id == "H-9"  # type: ignore[attr-defined]
