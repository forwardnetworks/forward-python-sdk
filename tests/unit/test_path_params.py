"""Path parameters that the HTTP client would turn into a different route."""

import httpx
import pytest

from forward_sdk._http import build_path, encode_query

TEMPLATE = "/networks/{networkId}/classic-devices/{deviceName}"


@pytest.mark.parametrize("name", [".", ".."])
def test_dot_segments_are_refused(name: str) -> None:
    with pytest.raises(ValueError, match="must not be"):
        build_path(TEMPLATE, {"networkId": "1", "deviceName": name})


def test_without_the_guard_the_client_collapses_the_segment() -> None:
    """Pins why the refusal exists: an unquoted ``..`` addresses the parent."""
    request = httpx.Client(base_url="https://h/api").build_request(
        "GET", "/networks/1/classic-devices/.."
    )
    assert request.url.path == "/api/networks/1"


@pytest.mark.parametrize("name", ["a..b", "...", "a/../b", "%2e%2e", ".hidden"])
def test_names_that_merely_contain_dots_are_kept_inside_one_segment(name: str) -> None:
    path = build_path(TEMPLATE, {"networkId": "1", "deviceName": name})
    request = httpx.Client(base_url="https://h/api").build_request("GET", path)
    raw = request.url.raw_path.decode()
    assert raw.startswith("/api/networks/1/classic-devices/")
    assert raw.count("/") == 5


def test_a_bare_dispatch_key_is_replaced_by_the_callers_value() -> None:
    fixed = [("action", "duplicate"), ("targetNetworkId", None)]
    assert encode_query({"targetNetworkId": "7"}, fixed) == [
        ("action", "duplicate"),
        ("targetNetworkId", "7"),
    ]
    # Without a value the bare key still goes out, so the route keeps matching.
    assert encode_query({}, fixed) == [("action", "duplicate"), ("targetNetworkId", "")]
