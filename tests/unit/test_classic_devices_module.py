"""``conn_type_exposed`` follows Forward's isExposed: PRODUCTION and TECH_PREVIEW only."""

from __future__ import annotations

import pytest

from forward_sdk.classic_devices import conn_type_exposed


@pytest.mark.parametrize(
    ("status", "exposed"),
    [
        ("PRODUCTION", True),
        ("TECH_PREVIEW", True),
        ("INTERNAL", False),
        ("UNDER_DEVELOPMENT", False),
        ("NOT_SUPPORTED", False),
        (None, False),
        ("SOMETHING_NEW", False),
    ],
)
def test_only_production_and_tech_preview_are_exposed(status: str | None, exposed: bool) -> None:
    info = {} if status is None else {"status": status}
    assert conn_type_exposed(info) is exposed
