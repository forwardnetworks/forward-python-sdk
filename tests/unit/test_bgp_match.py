"""``forward_sdk.bgp.match_staging``: reading a draft's BGP diffs back against what was staged."""

from __future__ import annotations

from typing import Any

import pytest

from forward_sdk.bgp import bgp_attributes, match_staging
from forward_sdk.models import (
    BgpAdvertisement,
    BgpAdvertisementDiff,
    BgpAdvertisementKey,
    Change,
    DevRoutingRpPrefixDetails,
)

AD: dict[str, Any] = {
    "vrf": "default",
    "externalPeer": "198.51.100.2",
    "prefix": "192.0.2.0/24",
    "nextHop": "198.51.100.2",
    "origin": "IGP",
    "asPath": [65001],
}


def diff(**fields: Any) -> BgpAdvertisementDiff:
    return BgpAdvertisementDiff.model_validate(fields)


def key(**overrides: str) -> BgpAdvertisementKey:
    base = {"vrf": "default", "externalPeer": "198.51.100.2", "prefix": "192.0.2.0/24"}
    return BgpAdvertisementKey.model_validate({**base, **overrides})


def test_whitespace_around_any_key_field_is_ignored() -> None:
    staged = BgpAdvertisement.model_validate(
        {**AD, "vrf": " default ", "externalPeer": "198.51.100.2 ", "prefix": " 192.0.2.0/24"}
    )
    report = match_staging([diff(change="INJECTED", updateAdded=AD)], staged=[staged])
    assert report.staged[0].found and report.unmatched == []


def test_an_ipv6_peer_matches_in_any_spelling() -> None:
    row = diff(change="INJECTED", updateAdded={**AD, "externalPeer": "2001:db8::2"})
    staged = BgpAdvertisement.model_validate({**AD, "externalPeer": "2001:DB8:0:0::2"})
    assert match_staging([row], staged=[staged]).staged[0].found


def test_a_host_prefix_matches_bare_or_with_its_length() -> None:
    bare_row = diff(change="INJECTED", updateAdded={**AD, "prefix": "192.0.2.7"})
    staged_32 = BgpAdvertisement.model_validate({**AD, "prefix": "192.0.2.7/32"})
    assert match_staging([bare_row], staged=[staged_32]).staged[0].found

    v6_row = diff(change="ABSENT", updateRemoved=key(prefix="2001:db8::7/128"))
    report = match_staging([v6_row], withdrawn=[key(prefix="2001:db8::7")])
    assert report.withdrawn[0].change == Change.absent
    assert report.unmatched == ["withdraw 2001:db8::7 via 198.51.100.2 in VRF default"]


def test_a_row_with_neither_side_is_skipped() -> None:
    report = match_staging([diff(change="INJECTED")], withdrawn=[key()])
    assert not report.withdrawn[0].found
    assert report.unmatched == ["withdraw 192.0.2.0/24 via 198.51.100.2 in VRF default"]


def test_a_withdrawal_with_no_row_is_unmatched_but_one_that_withdrew_is_not() -> None:
    rows = [diff(change="WITHDRAWN", original=AD, updateRemoved=key())]
    report = match_staging(rows, withdrawn=[key(), key(prefix="203.0.113.0/24")])
    assert [o.found for o in report.withdrawn] == [True, False]
    assert report.unmatched == ["withdraw 203.0.113.0/24 via 198.51.100.2 in VRF default"]


def test_an_unknown_change_from_a_newer_forward_is_reported_as_itself() -> None:
    staged = BgpAdvertisement.model_validate(AD)
    report = match_staging([diff(change="REPLACED", updateAdded=AD)], staged=[staged])
    assert report.staged[0].change == "REPLACED" and report.unmatched == []


def test_duplicated_is_reported_not_raised() -> None:
    staged = BgpAdvertisement.model_validate(AD)
    report = match_staging(
        [diff(change="DUPLICATED", original=AD, updateAdded=AD)], staged=[staged]
    )
    assert report.staged[0].change == Change.duplicated and report.unmatched == []


def test_a_received_route_without_next_hop_or_origin_still_parses() -> None:
    row = diff(
        change="WITHDRAWN",
        original={"vrf": "default", "externalPeer": "198.51.100.2", "prefix": "192.0.2.0/24"},
        updateRemoved=key(),
    )
    assert row.original is not None and row.original.next_hop is None


def test_a_key_both_staged_and_withdrawn_matches_each_against_its_own_rows() -> None:
    # Forward keeps staged and withdrawn advertisements as two sets; its diff
    # lists the added rows, then the withdrawn ones.
    rows = [
        diff(change="INJECTED", updateAdded=AD),
        diff(change="ABSENT", updateRemoved=key()),
    ]
    staged = BgpAdvertisement.model_validate(AD)
    report = match_staging(rows, staged=[staged], withdrawn=[key()])
    assert report.staged[0].change == Change.injected
    assert report.withdrawn[0].change == Change.absent
    assert report.unmatched == ["withdraw 192.0.2.0/24 via 198.51.100.2 in VRF default"]


def test_an_added_row_does_not_answer_for_a_withdrawal() -> None:
    report = match_staging([diff(change="INJECTED", updateAdded=AD)], withdrawn=[key()])
    assert not report.withdrawn[0].found


def test_bgp_attributes_decode_with_the_predicted_side_s_shapes() -> None:
    attrs = bgp_attributes(
        {
            "protocol": "BGP",
            "asPath": [[65001], [65002, 65003]],
            "localPref": 100,
            "med": 0,
            "bgpNextHopAddr": "198.51.100.1",
            "origin": "IGP",
            "standardCommunities": ["65001:100"],
            "somethingNew": 1,
        }
    )
    assert attrs.flat_as_path == [65001, 65002, 65003]
    assert attrs.next_hop == "198.51.100.1" and attrs.local_pref == 100 and attrs.med == 0
    assert attrs.model_extra == {"somethingNew": 1}


def test_bgp_attributes_refuse_another_protocol_or_none() -> None:
    with pytest.raises(ValueError, match="OSPF, not BGP"):
        bgp_attributes({"protocol": "OSPF", "cost": 10})
    with pytest.raises(ValueError, match="no attributes"):
        bgp_attributes(None)


def test_bgp_attributes_take_the_answer_model_or_a_raw_answer() -> None:
    answer = {"prefix": "192.0.2.0/24", "attributes": {"protocol": "BGP", "localPref": 200}}
    assert bgp_attributes(DevRoutingRpPrefixDetails.model_validate(answer)).local_pref == 200
    assert bgp_attributes(answer).local_pref == 200


def test_an_answer_with_an_error_says_so() -> None:
    with pytest.raises(ValueError, match="node not found"):
        bgp_attributes({"error": "node not found"})
    with pytest.raises(ValueError, match="node not found"):
        bgp_attributes(DevRoutingRpPrefixDetails.model_validate({"error": "node not found"}))
