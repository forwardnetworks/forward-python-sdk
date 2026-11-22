"""ADR-2611221115: an API-key classic device takes the SDK's field names as well as Forward's.

One test per scenario of docs/specs/ADR-2611221115.json, written from the spec
alone. Each test function is named for its scenario.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from forward_sdk._async.client import AsyncForwardClient
from forward_sdk._generated.models import NewClassicDevice, NewHttpCredential
from tests.conftest import Recorder, json_response

pytestmark = pytest.mark.anyio

CREDENTIALS = "/api/networks/N1/http-credentials"
DEVICES = "/api/networks/N1/classic-devices"
CREDENTIAL = {"name": "k", "type": "API_KEY", "password": "secret"}
DEVICE = {"name": "edge-1", "host": "10.0.0.1"}


def recorder() -> Recorder:
    return (
        Recorder()
        .add(
            "POST",
            CREDENTIALS,
            json_response({"id": "H-7", "name": "k", "type": "API_KEY", "password": "ref"}),
        )
        .add("POST", DEVICES, json_response({"name": "edge-1", "host": "10.0.0.1"}))
    )


def make_client(rec: Recorder) -> AsyncForwardClient:
    return AsyncForwardClient(
        "https://forward.test",
        username="k",
        password="s",
        rate_limit_rpm=None,
        transport=rec.transport,
    )


async def send(credential: Any, device: Any) -> Recorder:
    rec = recorder()
    async with make_client(rec) as client:
        await client.networks.create_classic_device_with_api_key(
            credential, device, network_id="N1"
        )
    return rec


async def refused(credential: Any, device: Any) -> Recorder:
    rec = recorder()
    async with make_client(rec) as client:
        with pytest.raises(ValueError):
            await client.networks.create_classic_device_with_api_key(
                credential, device, network_id="N1"
            )
    return rec


def body(rec: Recorder, path: str) -> dict[str, Any]:
    matching = [r for r in rec.requests if r.url.path == path]
    assert len(matching) == 1, f"{path}: {len(matching)} requests"
    return dict(json.loads(matching[0].content))


def aliased(model: type[Any]) -> list[tuple[str, str]]:
    """Every field whose Python name differs from its API name, as (python, api)."""
    return [
        (name, field.alias)
        for name, field in model.model_fields.items()
        if field.alias and field.alias != name
    ]


def renamed(mapping: dict[str, Any], model: type[Any]) -> dict[str, Any]:
    """The same mapping with each of the model's fields switched to its other spelling."""
    swap = dict(aliased(model))
    swap.update({api: python for python, api in aliased(model)})
    return {swap.get(key, key): value for key, value in mapping.items()}


# §1 -------------------------------------------------------------------------


async def test_s01_python_named_fields_are_sent_under_their_api_names() -> None:
    rec = await send(
        {
            "name": "k",
            "type": "API_KEY",
            "password": "secret",
            "login_type": "API_KEY",
            "auto_associate": False,
        },
        {"name": "edge-1", "host": "10.0.0.1", "cli_credential_id": "C-1"},
    )
    cred, dev = body(rec, CREDENTIALS), body(rec, DEVICES)
    assert cred["loginType"] == "API_KEY" and cred["autoAssociate"] is False
    assert cred["name"] == "k" and cred["type"] == "API_KEY" and cred["password"] == "secret"
    assert dev["cliCredentialId"] == "C-1"
    assert dev["name"] == "edge-1" and dev["host"] == "10.0.0.1"


async def test_s02_api_named_fields_are_sent_as_they_are() -> None:
    rec = await send({**CREDENTIAL, "loginType": "API_KEY"}, {**DEVICE, "cliCredentialId": "C-1"})
    assert body(rec, CREDENTIALS)["loginType"] == "API_KEY"
    assert body(rec, DEVICES)["cliCredentialId"] == "C-1"


async def test_s03_no_python_name_reaches_forward() -> None:
    rec = await send(
        {**CREDENTIAL, "login_type": "API_KEY", "auto_associate": True},
        {**DEVICE, "cli_credential_id": "C-1"},
    )
    assert len(rec.requests) == 2
    for request in rec.requests:
        sent = json.loads(request.content)
        assert not {"login_type", "auto_associate", "cli_credential_id"} & set(sent)


async def test_s04_both_spellings_produce_the_same_request() -> None:
    checked = 0
    for model, path in ((NewHttpCredential, CREDENTIALS), (NewClassicDevice, DEVICES)):
        for python, api in aliased(model):
            if python == "http_credential_id":
                continue
            value = f"v-{api}"
            if model is NewHttpCredential:
                by_python = await send({**CREDENTIAL, python: value}, DEVICE)
                by_api = await send({**CREDENTIAL, api: value}, DEVICE)
            else:
                by_python = await send(CREDENTIAL, {**DEVICE, python: value})
                by_api = await send(CREDENTIAL, {**DEVICE, api: value})
            first, second = body(by_python, path), body(by_api, path)
            assert first[api] == value and second[api] == value, (model.__name__, python)
            assert python not in first and python not in second, (model.__name__, python)
            assert first == second, (model.__name__, python)
            checked += 1
    assert checked > 10  # every aliased field of both models, not none


# §2 -------------------------------------------------------------------------


async def test_s05_a_device_field_named_both_ways_with_different_values_is_refused() -> None:
    rec = await refused(
        CREDENTIAL, {**DEVICE, "cli_credential_id": "C-1", "cliCredentialId": "C-2"}
    )
    assert rec.requests == []


async def test_s06_a_field_named_both_ways_is_refused_even_when_the_values_agree() -> None:
    rec = await refused({**CREDENTIAL, "login_type": "API_KEY", "loginType": "API_KEY"}, DEVICE)
    assert rec.requests == []


async def test_s07_mixed_spellings_each_used_once_are_not_refused() -> None:
    rec = await send({**CREDENTIAL, "login_type": "API_KEY", "autoAssociate": True}, DEVICE)
    cred = body(rec, CREDENTIALS)
    assert cred["loginType"] == "API_KEY" and cred["autoAssociate"] is True


async def test_s08_any_field_under_both_spellings_is_refused() -> None:
    checked = 0
    for model in (NewHttpCredential, NewClassicDevice):
        for python, api in aliased(model):
            for v1, v2 in (("a", "a"), ("a", "b")):
                if model is NewHttpCredential:
                    rec = await refused({**CREDENTIAL, python: v1, api: v2}, DEVICE)
                else:
                    rec = await refused(CREDENTIAL, {**DEVICE, python: v1, api: v2})
                assert rec.requests == [], (model.__name__, python)
                checked += 1
    assert checked > 20


# §3 -------------------------------------------------------------------------


async def test_s09_a_valid_credential_and_device_are_sent() -> None:
    rec = await send(CREDENTIAL, DEVICE)
    assert [r.url.path for r in rec.requests] == [CREDENTIALS, DEVICES]


async def test_s10_a_device_naming_an_http_credential_by_python_name_is_refused() -> None:
    rec = await refused(CREDENTIAL, {**DEVICE, "http_credential_id": "H-1"})
    assert rec.requests == []


async def test_s11_a_device_naming_an_http_credential_by_api_name_is_refused() -> None:
    rec = await refused(CREDENTIAL, {**DEVICE, "httpCredentialId": "H-1"})
    assert rec.requests == []


async def test_s12_a_credential_that_is_not_api_key_is_refused() -> None:
    rec = await refused({**CREDENTIAL, "type": "LOCAL", "login_type": "API_KEY"}, DEVICE)
    assert rec.requests == []


async def test_s13_a_device_with_no_name_or_no_host_is_refused() -> None:
    for device in (
        {"host": "10.0.0.1", "cli_credential_id": "C-1"},
        {"name": "edge-1", "cliCredentialId": "C-1"},
    ):
        rec = await refused(CREDENTIAL, device)
        assert rec.requests == [], device


async def test_s14_the_checks_give_the_same_verdict_whichever_spelling() -> None:
    base_credential = {**CREDENTIAL, "login_type": "API_KEY"}
    base_device = {**DEVICE, "cli_credential_id": "C-1"}
    cases: list[tuple[dict[str, Any], dict[str, Any], bool]] = [
        (base_credential, base_device, False),
        (base_credential, {"host": "10.0.0.1", "cli_credential_id": "C-1"}, True),
        (base_credential, {"name": "edge-1", "cli_credential_id": "C-1"}, True),
        ({**base_credential, "type": "LOGIN"}, base_device, True),
        (base_credential, {**base_device, "http_credential_id": "H-1"}, True),
    ]
    for credential, device, refuse in cases:
        for cred in (credential, renamed(credential, NewHttpCredential)):
            for dev in (device, renamed(device, NewClassicDevice)):
                if refuse:
                    rec = await refused(cred, dev)
                    assert rec.requests == [], (cred, dev)
                else:
                    rec = await send(cred, dev)
                    assert len(rec.requests) == 2, (cred, dev)
    for name in ("http_credential_id", "httpCredentialId"):
        rec = await refused(CREDENTIAL, {**DEVICE, name: "H-1"})
        assert rec.requests == []


# §4 -------------------------------------------------------------------------


async def test_s15_a_key_the_model_does_not_describe_is_sent_unchanged() -> None:
    rec = await send(CREDENTIAL, {**DEVICE, "vendorHint": "x"})
    assert body(rec, DEVICES)["vendorHint"] == "x"


async def test_s16_unknown_keys_are_neither_refused_nor_renamed() -> None:
    rec = await send(CREDENTIAL, {**DEVICE, "some_extra_field": 1})
    dev = body(rec, DEVICES)
    assert dev["some_extra_field"] == 1 and "someExtraField" not in dev


async def test_s17_every_unknown_key_passes_through_unchanged() -> None:
    for key, value in (
        ("vendorHint", "x"),
        ("some_extra_field", 1),
        ("Mixed_Case", [1, 2]),
        ("x", None),
    ):
        rec = await send({**CREDENTIAL, key: value}, {**DEVICE, key: value})
        assert body(rec, CREDENTIALS)[key] == value, key
        assert body(rec, DEVICES)[key] == value, key


# §5 -------------------------------------------------------------------------


async def test_s18_models_are_serialized_with_to_api() -> None:
    credential = NewHttpCredential(name="k", type="API_KEY", password="secret")
    device = NewClassicDevice(name="edge-1", host="10.0.0.1")
    rec = await send(credential, device)
    assert body(rec, CREDENTIALS) == credential.to_api()
    dev = body(rec, DEVICES)
    assert {key: dev[key] for key in device.to_api()} == device.to_api()


async def test_s19_a_model_is_not_rekeyed_into_python_names() -> None:
    credential = NewHttpCredential(
        name="k", type="API_KEY", password="secret", login_type="LOCAL", auto_associate=True
    )
    rec = await send(credential, NewClassicDevice(name="edge-1", host="10.0.0.1"))
    cred = body(rec, CREDENTIALS)
    assert "login_type" not in cred and "auto_associate" not in cred
    assert cred["loginType"] == credential.to_api()["loginType"]
    assert cred["autoAssociate"] is True


async def test_s20_a_credential_model_body_is_exactly_to_api() -> None:
    for model in (
        NewHttpCredential(name="k", type="API_KEY", password="secret"),
        NewHttpCredential(name="k2", type="API_KEY", password="p", auto_associate=False),
        NewHttpCredential(
            name="k3", type="API_KEY", password="p", login_type="LOCAL", auto_associate=True
        ),
    ):
        rec = await send(model, DEVICE)
        assert body(rec, CREDENTIALS) == model.to_api()
