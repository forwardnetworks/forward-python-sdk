"""Path parameters that the HTTP client would turn into a different route."""

import httpx
import pytest

from forward_sdk._http import build_path, encode_query
from forward_sdk._ops import spec_for

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


class TestPreEncodedValues:
    """A job's cancelLink is handed out already encoded; encoding it again breaks it."""

    def test_the_cancel_link_is_not_encoded_twice(self) -> None:
        link = "AbC%2BdEf%3D%3D"
        spec = spec_for("cancelJob", path_params={"cancelJobId": link})
        assert spec.path == f"/jobs/{link}"

    def test_it_is_still_one_plain_segment(self) -> None:
        for bad in ("a/b", "a?x=1", "..", "%2e%2e", "a b", "a#b"):
            with pytest.raises(ValueError, match="single encoded path segment"):
                spec_for("cancelJob", path_params={"cancelJobId": bad})

    def test_ordinary_parameters_are_still_quoted(self) -> None:
        assert build_path("/x/{n}", {"n": "a%2Fb"}) == "/x/a%252Fb"
