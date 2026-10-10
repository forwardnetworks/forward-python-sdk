"""Where a property's value comes from.

Forward has no route that says so. The rule is derived from three listings:
the org's own settings win, then a Forward administrator's change to the global
default, and otherwise the value is the one compiled into Forward.
"""

from __future__ import annotations

from forward_sdk import PropertySource, describe_properties
from forward_sdk.models import ConfigValues


def test_the_org_setting_wins_over_the_global_one() -> None:
    effective = {"firewall_predict": True}
    described = describe_properties(
        effective, {"firewall_predict": True}, {"firewall_predict": False}
    )
    assert described["firewall_predict"] == PropertySource(True, "org")


def test_a_global_change_is_reported_as_global() -> None:
    described = describe_properties({"session_timeout": 45}, {}, {"session_timeout": 45})
    assert described["session_timeout"] == PropertySource(45, "global")


def test_a_property_nobody_set_is_the_default() -> None:
    described = describe_properties({"session_timeout": 30}, {}, {})
    assert described["session_timeout"] == PropertySource(30, "default")


def test_an_org_setting_equal_to_the_default_is_still_the_orgs() -> None:
    # It will not change if the default does, so it is not "default".
    described = describe_properties({"session_timeout": 30}, {"session_timeout": 30}, {})
    assert described["session_timeout"].source == "org"


def test_the_value_is_the_effective_one_not_the_scope_s() -> None:
    described = describe_properties({"a": "effective"}, {"a": "org-says"}, {"a": "global-says"})
    assert described["a"].value == "effective"


def test_names_are_compared_without_regard_to_case() -> None:
    described = describe_properties({"FIREWALL_PREDICT": True}, {"firewall_predict": True}, {})
    assert described["FIREWALL_PREDICT"].source == "org"


def test_every_effective_property_is_described_and_no_others() -> None:
    described = describe_properties({"a": 1, "b": 2}, {"c": 3}, {"d": 4})
    assert set(described) == {"a", "b"}


def test_it_accepts_the_generated_models() -> None:
    effective = ConfigValues.model_validate({"firewall_predict": True, "x": 1})
    org = ConfigValues.model_validate({"firewall_predict": True})
    glob = ConfigValues.model_validate({})
    described = describe_properties(effective, org, glob)
    assert described["firewall_predict"].source == "org"
    assert described["x"].source == "default"


def test_empty_responses_describe_nothing() -> None:
    assert describe_properties(None, None, None) == {}
