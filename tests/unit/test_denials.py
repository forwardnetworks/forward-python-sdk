"""Telling one kind of refusal from another.

Forward sends no machine-readable code for a refusal. Its access enforcer builds
an ErrorInfo with `reason` set to null, so the message is the only signal there
is. The wordings below are that enforcer's own format strings.
"""

from __future__ import annotations

import pytest

from forward_sdk.errors import Denial, classify_denial


class TestClassifyDenial:
    @pytest.mark.parametrize(
        ("message", "kind", "detail"),
        [
            (
                "Unlicensed operation: NetworkOperation.USE_NQE",
                "license",
                "NetworkOperation.USE_NQE",
            ),
            (
                "Missing permission: OrgOperation.MANAGE_LICENSING",
                "rbac",
                "OrgOperation.MANAGE_LICENSING",
            ),
            (
                "API_RATE_LIMITING is off for your deployment",
                "deployment_setting",
                "API_RATE_LIMITING",
            ),
            (
                "AUTO_UPLOAD_TELEMETRY is on for your organization",
                "org_setting",
                "AUTO_UPLOAD_TELEMETRY",
            ),
            ("Forward admin authority required", "authority", "Forward admin"),
        ],
    )
    def test_each_wording_is_recognised(self, message: str, kind: str, detail: str) -> None:
        assert classify_denial(message) == Denial(kind=kind, detail=detail)

    def test_a_typographic_apostrophe_does_not_defeat_it(self) -> None:
        """Forward's expiry sentence contains U+2019, not an ASCII apostrophe.

        Matching across it is exactly how a check like this stops working
        silently, so the pattern deliberately matches only the tail.
        """
        # Written as an escape so the linter does not rewrite the very
        # character this test exists for.
        curly = "Your organization\u2019s license has expired"
        assert classify_denial(curly) == Denial(kind="license_expired")
        assert classify_denial("Your organization's license has expired") == Denial(
            kind="license_expired"
        )

    def test_a_permission_denial_is_not_read_as_a_licence_one(self) -> None:
        """The distinction is the whole point; conflating them misdirects an operator."""
        licence = classify_denial("Unlicensed operation: NetworkOperation.USE_NQE")
        rbac = classify_denial("Missing permission: NetworkOperation.USE_NQE")
        assert licence is not None and rbac is not None
        assert licence.kind != rbac.kind

    @pytest.mark.parametrize("message", ["", None, "   ", "some unrelated failure"])
    def test_unrecognised_wording_yields_nothing(self, message: str | None) -> None:
        """None means "not recognised", never "no cause"."""
        assert classify_denial(message) is None
