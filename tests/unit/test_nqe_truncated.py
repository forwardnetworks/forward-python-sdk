"""``NqeRunResult.truncated``: a page with fewer rows than Forward says matched."""

from __future__ import annotations

import pytest

from forward_sdk.models import NqeRunResult


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"items": [{"a": 1}], "totalNumItems": 3}, True),
        ({"items": [{"a": 1}, {"a": 2}], "totalNumItems": 2}, False),
        ({"items": [], "totalNumItems": 0}, False),
        ({"items": [{"a": 1}]}, False),  # no total says nothing either way
        ({"totalNumItems": 5}, True),  # a total with no rows at all
    ],
)
def test_truncated(payload: dict[str, object], expected: bool) -> None:
    assert NqeRunResult.model_validate(payload).truncated is expected


def test_truncated_is_not_sent_back() -> None:
    result = NqeRunResult.model_validate({"items": [], "totalNumItems": 1})
    assert "truncated" not in result.to_api()
