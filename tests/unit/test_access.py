"""Whether an account's roles allow an operation, as Forward's enforcer decides it.

The expectations come from Forward's own rules (``OrgAuthority`` and
``NetworkAuthority``): an org admin holds every operation, an org read-only role
holds every read, and a network role holds what its table lists, each role adding
to the one below. The assertions that name a real operation use ones whose lowest
role is stated in Forward's source or documentation: collecting a snapshot needs
a workspace operator, and a workspace is the only place that role can exist.
"""

from __future__ import annotations

import pytest

from forward_sdk.access import Access, describe_operation, operations
from forward_sdk.errors import ForwardConfigurationError
from forward_sdk.models import CurrentUserRoles
from forward_sdk.rbac import RoleTable

COLLECT = "NetworkOperation.TRIGGER_SNAPSHOT_COLLECTION"


@pytest.fixture(autouse=True)
def _table(role_table: RoleTable, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test answers from the invented table, through the environment."""
    monkeypatch.setattr("forward_sdk.access.discover", lambda: role_table)


def holding(network: str | None = None, role: str | None = None, **more: object) -> Access:
    roles: dict[str, object] = {"org": more.pop("org", []), "network": {}}
    if network is not None and role is not None:
        roles["network"] = {network: role}
    roles.update(more)
    return Access.from_roles(roles, network)


class TestNetworkOperations:
    @pytest.mark.parametrize(
        ("role", "allowed"),
        [
            ("LIMITED_READ_ONLY", False),
            ("READ_ONLY", False),
            ("OPERATOR", False),
            ("WORKSPACE_OPERATOR", True),
            ("ADMIN", True),
        ],
    )
    def test_who_may_collect(self, role: str, allowed: bool) -> None:
        assert holding("5", role).can(COLLECT) is allowed

    def test_an_org_admin_may_do_anything(self) -> None:
        access = holding(org=["ADMIN"], network="5", role="READ_ONLY")
        assert access.can(COLLECT) is True
        assert access.can("OrgOperation.MANAGE_USER_ACCOUNTS") is True

    def test_a_role_on_another_network_grants_nothing_here(self) -> None:
        access = holding("9", "ADMIN")
        assert access.can(COLLECT, network_id="5") is False

    def test_the_network_can_be_named_per_call(self) -> None:
        access = Access.from_roles({"org": [], "network": {"5": "OPERATOR", "6": "ADMIN"}})
        assert access.can(COLLECT, "5") is False
        assert access.can(COLLECT, "6") is True

    def test_a_network_operation_needs_a_network(self) -> None:
        with pytest.raises(ValueError, match="no network id"):
            Access.from_roles({"org": [], "network": {"5": "ADMIN"}}).can(COLLECT)

    def test_the_bare_name_and_the_prefixed_name_agree(self) -> None:
        access = holding("5", "ADMIN")
        assert access.can("TRIGGER_SNAPSHOT_COLLECTION") is access.can(COLLECT) is True

    def test_org_read_only_holds_every_read_and_no_write(self) -> None:
        access = Access.from_roles({"org": ["READ_ONLY"], "network": {}}, "5")
        assert access.can("NetworkOperation.VIEW_NETWORK_AND_SNAPSHOTS") is True
        assert access.can(COLLECT) is False


class TestOrgOperations:
    def test_any_network_role_that_holds_it_is_enough(self) -> None:
        """Forward checks an org operation against every role the account has."""
        access = Access.from_roles({"org": [], "network": {"5": "LIMITED_READ_ONLY"}})
        assert access.can("OrgOperation.VIEW_DATA_FILES") is True

    def test_an_operation_no_network_role_holds_is_for_org_admins_only(self) -> None:
        info = describe_operation("OrgOperation.MANAGE_USER_ACCOUNTS")
        assert info.lowest_network_role is None
        assert holding("5", "ADMIN").can("OrgOperation.MANAGE_USER_ACCOUNTS") is False
        assert holding(org=["ADMIN"]).can("OrgOperation.MANAGE_USER_ACCOUNTS") is True


class TestWhatCannotBeSaid:
    def test_roles_we_could_not_read_are_unknown_not_refused(self) -> None:
        assert Access.from_roles(None, "5").can(COLLECT) is None

    def test_empty_roles_are_unknown_not_refused(self) -> None:
        assert Access.from_roles({"org": [], "network": {}}, "5").can(COLLECT) is None

    def test_a_forward_internal_role_is_not_modelled(self) -> None:
        access = Access.from_roles({"org": [], "network": {}, "system": ["ADMIN"]}, "5")
        assert access.can(COLLECT) is None

    def test_a_role_this_table_does_not_know_is_unknown(self) -> None:
        assert holding("5", "SUPER_ADMIN").can(COLLECT) is None

    def test_an_unrecognised_role_does_not_hide_a_known_grant(self) -> None:
        access = Access.from_roles({"org": [], "network": {"5": "ADMIN", "6": "NEW_ROLE"}})
        assert access.can("OrgOperation.VIEW_DATA_FILES") is True

    def test_a_misspelt_operation_is_an_error_not_a_no(self) -> None:
        with pytest.raises(ValueError, match="TRIGGER_SNAPSHOT_COLLECTION"):
            holding("5", "ADMIN").can("TRIGGER_SNAPSHOT_COLLECTON")


class TestSessionsTheRolesDoNotDescribe:
    """Forward gives some sessions more than the roles show, so a no is not safe to give."""

    def test_an_impersonated_session_gains_operations_the_roles_omit(self) -> None:
        access = Access.from_roles(
            {"org": [], "network": {"5": "OPERATOR"}}, "5", impersonator="support-1"
        )
        decision = access.may(COLLECT)
        assert decision.allowed is None
        assert "support-1" in decision.reason

    def test_an_impersonated_yes_is_still_a_yes(self) -> None:
        """Impersonation adds to the account's own permissions; it never takes any away."""
        access = Access.from_roles(
            {"org": [], "network": {"5": "ADMIN"}}, "5", impersonator="support-1"
        )
        assert access.can(COLLECT) is True

    def test_a_support_account_reaches_more_than_its_roles_show(self) -> None:
        access = Access.from_roles(
            {"org": [], "network": {"5": "OPERATOR"}, "supportedOrgIds": ["9"]}, "5"
        )
        decision = access.may(COLLECT)
        assert decision.allowed is None
        assert "support" in decision.reason

    def test_a_blank_network_role_is_unknown(self) -> None:
        assert Access.from_roles({"org": [], "network": {"5": ""}}, "5").can(COLLECT) is None


class TestRolesAsForwardSendsThem:
    def test_org_as_a_bare_string(self) -> None:
        access = Access.from_roles({"org": "ADMIN", "network": {}})
        assert access.org_admin

    def test_a_model_from_get_current_user(self) -> None:
        roles = CurrentUserRoles.model_validate({"org": ["ADMIN"], "network": {"5": "ADMIN"}})
        assert Access.from_roles(roles, "5").can(COLLECT) is True

    def test_a_model_with_a_system_role_keeps_it(self) -> None:
        roles = CurrentUserRoles.model_validate({"org": [], "network": {}, "system": ["ADMIN"]})
        assert Access.from_roles(roles, "5").system_roles == {"ADMIN"}


class TestAtLeast:
    def test_roles_are_ordered(self) -> None:
        access = holding("5", "OPERATOR")
        assert access.at_least("READ_ONLY") is True
        assert access.at_least("OPERATOR") is True
        assert access.at_least("WORKSPACE_OPERATOR") is False

    def test_an_org_admin_holds_every_network_role(self) -> None:
        assert holding(org=["ADMIN"], network="5").at_least("ADMIN") is True

    def test_an_unknown_role_name_is_an_error(self) -> None:
        with pytest.raises(ValueError, match="unknown network role"):
            holding("5", "ADMIN").at_least("OWNER")


class TestTheTable:
    def test_it_describes_an_operation(self) -> None:
        info = describe_operation(COLLECT)
        assert (info.scope, info.access_type, info.lowest_network_role) == (
            "network",
            "WRITE",
            "WORKSPACE_OPERATOR",
        )
        assert info.license_facet == "BASE"
        assert "collection" in info.doc.lower()

    def test_every_operation_can_be_looked_up_by_its_listed_name(self) -> None:
        listed = operations()
        assert len(listed) == 9
        for info in listed:
            assert describe_operation(f"{info.scope.capitalize()}Operation.{info.name}") == info

    def test_the_two_enums_share_no_names(self) -> None:
        """The bare name is only safe because of this; if it fails, require the prefix."""
        network = {o.name for o in operations("network")}
        org = {o.name for o in operations("org")}
        assert not network & org


class TestWithoutATable:
    @pytest.fixture(autouse=True)
    def _no_table(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("forward_sdk.access.discover", lambda: None)

    def test_every_answer_is_unknown_and_says_how_to_fix_it(self) -> None:
        decision = holding("5", "ADMIN").may(COLLECT)
        assert decision.allowed is None
        assert decision.operation is None
        assert "FORWARD_SOURCE" in decision.reason

    def test_the_role_ladder_needs_no_table(self) -> None:
        assert holding("5", "OPERATOR").at_least("READ_ONLY") is True

    def test_describing_an_operation_is_an_error(self) -> None:
        with pytest.raises(ForwardConfigurationError, match="no role table"):
            describe_operation(COLLECT)


class TestMayGivesTheReason:
    def test_a_yes_names_the_role_it_rested_on(self) -> None:
        decision = holding("5", "ADMIN").may(COLLECT)
        assert decision.allowed is True
        assert decision.role == "ADMIN"
        assert "ADMIN on network 5" in decision.reason

    def test_a_no_names_the_role_and_what_it_would_take(self) -> None:
        decision = holding("5", "OPERATOR").may(COLLECT)
        assert decision.allowed is False
        assert decision.role == "OPERATOR"
        assert "WORKSPACE_OPERATOR" in decision.reason

    def test_a_no_for_an_org_admin_only_operation_says_so(self) -> None:
        decision = holding("5", "ADMIN").may("OrgOperation.MANAGE_USER_ACCOUNTS")
        assert (decision.allowed, decision.role) == (False, None)
        assert "only an org ADMIN" in decision.reason

    def test_no_role_on_the_network_is_a_no_that_says_so(self) -> None:
        decision = holding("9", "ADMIN").may(COLLECT, "5")
        assert decision.allowed is False
        assert "no role on network 5" in decision.reason

    def test_an_org_admin_is_the_reason_for_an_org_admin(self) -> None:
        assert "org ADMIN" in holding(org=["ADMIN"], network="5").may(COLLECT).reason

    def test_unknown_always_says_why(self) -> None:
        for access, fragment in (
            (Access.from_roles({"org": [], "network": {}}, "5"), "no roles"),
            (Access.from_roles(None, "5"), "no roles"),
            (Access.unreadable_because("HTTP 403 from /users/current", "5"), "could not be read"),
            (holding("5", "SUPER_ADMIN"), "SUPER_ADMIN"),
            (Access.from_roles({"org": [], "network": {}, "system": ["ADMIN"]}, "5"), "internal"),
        ):
            decision = access.may(COLLECT)
            assert decision.allowed is None
            assert fragment in decision.reason

    def test_roles_that_could_not_be_read_are_never_a_no(self) -> None:
        access = Access.unreadable_because("HTTP 403 from /users/current", "5")
        assert access.can(COLLECT) is None
        assert access.at_least("ADMIN") is None

    def test_a_decision_is_not_a_truth_value(self) -> None:
        """`if access.may(x):` would read None as no, and a decision as always yes."""
        with pytest.raises(TypeError, match="three outcomes"):
            bool(holding("5", "ADMIN").may(COLLECT))
