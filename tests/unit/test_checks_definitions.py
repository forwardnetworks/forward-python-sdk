"""``forward_sdk.checks``: path and NQE check definitions, and the pre-check for refused fields."""

from __future__ import annotations

import pytest

from forward_sdk import checks
from forward_sdk.errors import ForwardConfigurationError
from forward_sdk.models import NewNetworkCheck
from forward_sdk.trace import ChainHop, Endpoint, PathQuery, device, subnet

FLOW = PathQuery(
    from_=Endpoint(subnet("10.10.10.10/32")),
    chain=[ChainHop("through", device("r2"))],
    to=Endpoint(subnet("10.20.20.20/32"), headers=[{"ip_proto": ["6"], "tp_dst": ["8080"]}]),
)


def test_a_path_check_carries_the_query_as_filters() -> None:
    definition = checks.existential(FLOW)
    assert definition == {"checkType": "Existential", "filters": FLOW.to_api()}
    assert checks.isolation(FLOW)["checkType"] == "Isolation"
    assert checks.reachability(FLOW)["checkType"] == "Reachability"


def test_the_optional_fields() -> None:
    definition = checks.existential(
        FLOW, noise_types=["ICMP"], header_fields_with_defaults=["tp_src"], return_path="SYMMETRIC"
    )
    assert definition["noiseTypes"] == ["ICMP"]
    assert definition["headerFieldsWithDefaults"] == ["tp_src"]
    assert definition["returnPath"] == "SYMMETRIC"
    assert "returnPath" not in checks.existential(FLOW)


def test_refusals_before_sending() -> None:
    with pytest.raises(ForwardConfigurationError, match="ANY or SYMMETRIC"):
        checks.existential(FLOW, return_path="BOTH")  # type: ignore[arg-type]
    with pytest.raises(ForwardConfigurationError, match="Forward refuses ipv4_dst"):
        checks.isolation(FLOW, header_fields_with_defaults=["ipv4_dst"])


def test_an_nqe_check() -> None:
    assert checks.nqe_check(" Q_123 ") == {"checkType": "NQE", "queryId": "Q_123"}
    assert checks.nqe_check("Q_1", {"threshold": 3})["params"] == {"threshold": 3}
    with pytest.raises(ForwardConfigurationError, match="library query id"):
        checks.nqe_check(" ")


@pytest.mark.parametrize(
    ("body", "match"),
    [
        ({"definition": checks.nqe_check("Q_1"), "name": "x"}, "takes no name"),
        ({"definition": checks.nqe_check("Q_1", {"n": 1})}, "needs a name"),
        ({"definition": checks.nqe_check("Q_1"), "note": "x"}, "takes no note"),
        ({"definition": checks.nqe_check("Q_1"), "tags": ["x"]}, "takes no tags"),
        ({"definition": {"checkType": "Predefined"}, "name": "x"}, "takes no name"),
        ({"definition": {"checkType": "Predefined"}, "tags": ["x"]}, "takes no tags"),
        ({"definition": checks.isolation(FLOW), "perfMonitoringEnabled": True}, "Existential"),
        ({"name": "x"}, "needs a definition"),
    ],
)
def test_bodies_forward_refuses_are_refused(body: dict[str, object], match: str) -> None:
    with pytest.raises(ForwardConfigurationError, match=match):
        checks.validate_new_check(body)


@pytest.mark.parametrize(
    "body",
    [
        {"definition": checks.existential(FLOW), "name": "app", "tags": ["a"], "note": "n"},
        {"definition": checks.existential(FLOW), "perfMonitoringEnabled": True},
        {"definition": checks.nqe_check("Q_1")},
        {"definition": checks.nqe_check("Q_1", {"n": 1}), "name": "with params"},
        {"definition": {"checkType": "Predefined"}, "note": "a note is allowed here"},
    ],
)
def test_bodies_forward_accepts_pass(body: dict[str, object]) -> None:
    checks.validate_new_check(body)


@pytest.mark.parametrize(
    "field", ["ip_frag_offset", "tcp_flags", "ip_tos", "tp_src", "app_id", "url", "user_id"]
)
def test_every_field_forward_lets_a_check_default_is_accepted(field: str) -> None:
    assert checks.existential(FLOW, header_fields_with_defaults=[field])[
        "headerFieldsWithDefaults"
    ] == [field]


@pytest.mark.parametrize(
    ("body", "match"),
    [
        (
            {"definition": {"checkType": "NetworkQuery", "queryId": "Q"}, "name": "x"},
            "takes no name",
        ),
        (
            {"definition": {"checkType": "NetworkQuery", "queryId": "Q"}, "note": "x"},
            "takes no note",
        ),
    ],
)
def test_forwards_older_type_names_get_the_same_rules(body: dict[str, object], match: str) -> None:
    with pytest.raises(ForwardConfigurationError, match=match):
        checks.validate_new_check(body)


def test_older_path_type_names_take_tags_and_perf_monitoring() -> None:
    checks.validate_new_check({"definition": {"checkType": "ExistentialCheck"}, "tags": ["a"]})
    checks.validate_new_check(
        {"definition": {"checkType": "Exists"}, "perfMonitoringEnabled": True}
    )


def test_a_blank_name_or_note_counts_as_none() -> None:
    checks.validate_new_check({"definition": checks.nqe_check("Q_1"), "name": "  ", "note": " "})
    with pytest.raises(ForwardConfigurationError, match="needs a name"):
        checks.validate_new_check({"definition": checks.nqe_check("Q_1", {"n": 1}), "name": "  "})


def test_a_request_model_body_is_read() -> None:
    body = NewNetworkCheck.model_validate({"definition": checks.nqe_check("Q_1"), "name": "x"})
    with pytest.raises(ForwardConfigurationError, match="takes no name"):
        checks.validate_new_check(body)


def test_trimming_follows_java_so_a_non_breaking_space_is_a_name() -> None:
    # Java's trim() strips only characters up to U+0020; U+00A0 survives, so it is a name.
    checks.validate_new_check({"definition": checks.nqe_check("Q_1", {"n": 1}), "name": "\u00a0"})
    with pytest.raises(ForwardConfigurationError, match="needs a name"):
        checks.validate_new_check(
            {"definition": checks.nqe_check("Q_1", {"n": 1}), "name": "\t\x01 "}
        )


@pytest.mark.parametrize("body", [None, ["definition"], "definition"])
def test_a_body_that_is_not_a_mapping_is_a_configuration_error(body: object) -> None:
    with pytest.raises(ForwardConfigurationError, match="mapping or a request model"):
        checks.validate_new_check(body)
