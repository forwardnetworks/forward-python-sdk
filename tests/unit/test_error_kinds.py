"""The refusals ``ForwardAPIError`` classifies from Forward's message (Go SDK v0.8.39-v0.8.42)."""

from __future__ import annotations

import json

import pytest

from forward_sdk.errors import ForwardAPIError


def error(status: int, message: str) -> ForwardAPIError:
    return ForwardAPIError(message, status=status, text=json.dumps({"message": message}))


@pytest.mark.parametrize(
    "message", ["ServiceNow instance is hibernating", "ServiceNow instance is suspended."]
)
def test_a_sleeping_servicenow_instance(message: str) -> None:
    assert error(409, message).servicenow_instance_asleep


@pytest.mark.parametrize(
    ("status", "message"),
    [
        (409, "Unknown Error"),  # a woken instance's answer, cause unconfirmed: not this
        (500, "ServiceNow instance is hibernating"),
        (409, "the ServiceNow instance is hibernating, it said"),  # anchored at the start
        (409, "ServiceNow instance is hibernating; also the token expired"),  # and at the end
    ],
)
def test_other_answers_are_not_a_sleeping_instance(status: int, message: str) -> None:
    assert not error(status, message).servicenow_instance_asleep


def test_an_unknown_network_property() -> None:
    message = (
        "No enum constant com.forwardnetworks.cv.config.NetworkProperty.LOCATION_CONNECTIVITY_DIFFS"
    )
    assert error(400, message).unknown_network_property
    org = "No enum constant com.forwardnetworks.cv.config.OrgProperty.NOPE"
    assert not error(400, org).unknown_network_property


@pytest.mark.parametrize(
    "message",
    [
        "'name' not allowed for non-parameterized NQE checks",
        "'note' not allowed for NQE checks",
        "'tags' not allowed for NQE checks",
        "'name' not allowed for Predefined checks",  # CheckType's JSON name, capital P
        "'name' required for parameterized NQE checks",
        "'tags' not allowed for Predefined checks",
        "'perfMonitoringEnabled' not allowed for Isolation checks",
    ],
)
def test_a_field_the_check_type_refuses(message: str) -> None:
    assert error(400, message).check_field_not_allowed
    assert not error(409, message).check_field_not_allowed


def test_unrecognized_devices_are_named() -> None:
    refused = error(400, "Unrecognized devices cannot be moved: [r1, r2, edge-3]")
    assert refused.unrecognized_devices == ["r1", "r2", "edge-3"]
    assert error(400, "Bad location id").unrecognized_devices is None
    assert error(404, "Unrecognized devices cannot be moved: [r1]").unrecognized_devices is None


def test_an_org_the_key_cannot_reach() -> None:
    assert error(403, "Org 9001 is not accessible to user 77").org_not_accessible
    assert not error(403, "Access denied").org_not_accessible
    assert not error(404, "Org 9001 is not accessible to user 77").org_not_accessible


def test_a_field_message_with_trailing_text_is_not_classified() -> None:
    assert not error(400, "'name' not allowed for NQE checks, and more").check_field_not_allowed
