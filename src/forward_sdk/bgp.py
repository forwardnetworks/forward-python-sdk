"""Checking staged BGP advertisements against what Predict made of them.

Forward accepts a staged withdrawal of a key the base snapshot does not have,
and ignores it when the change set runs. The only sign is the draft's diffs
view, where the row reads ``ABSENT``. :func:`match_staging` reads that view
back against what the caller staged; :meth:`verify_bgp_staging` on a change-set
handle fetches the view and raises
:class:`~forward_sdk.errors.ForwardBgpKeyMatchedNothingError` when a key matched
nothing.

Mirrors the Go SDK v0.8.30 (``Predict.VerifyBGPStaging``). The diffs shape is
from Forward's source (``BgpAdvertisementDiffInfo``), not yet recorded live.
"""

from __future__ import annotations

import contextlib
import ipaddress
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from forward_sdk._generated.models import (
    BgpAdvertisement,
    BgpAdvertisementDiff,
    BgpAdvertisementKey,
    Change,
)

__all__ = [
    "PREDICT_MODELING",
    "PREDICT_MODEL_BGP",
    "PREDICT_MODEL_EXT_ADV",
    "BgpKeyOutcome",
    "BgpReadiness",
    "BgpStagingReport",
    "Change",
    "RpBgpAttributes",
    "bgp_attributes",
    "key_of",
    "match_staging",
]

KeyTuple = tuple[str, str, str]

#: Org property that switches Predict's protocol modeling on; the change-set routes need it.
PREDICT_MODELING = "PREDICT_MODELING"
#: Org property (default true, configurable on premises) that lets protocol modeling seed
#: routes from the collected iBGP and eBGP received-route tables. With it off, a staged
#: advertisement is not modeled the way a received one is.
PREDICT_MODEL_EXT_ADV = "PREDICT_MODEL_EXT_ADV"
#: Org property (default true, configurable on premises) that switches modeling BGP at all.
PREDICT_MODEL_BGP = "PREDICT_MODEL_BGP"


@dataclass
class BgpReadiness:
    """Whether Predict will model staged BGP advertisements on a device.

    ``problems`` lists each reason Predict would silently model nothing, found
    by a read that succeeded. ``unknown`` names what could not be checked, with
    the Go SDK's labels: an org property the API did not return (its name), a
    refused (403) or unserved read (``organization-properties``,
    ``classic-devices``, ``bgp-collection-requirements``), or a device with no
    classic source (``collection-source:<name>``). Forward's source defaults the
    Predict switches to on, but this does not assume it. ``unknown_reasons``
    says why each unknown is unknown.
    """

    device: str
    #: The effective org property values for the calling user's organization;
    #: ``None`` when not read.
    predict_modeling: bool | None = None
    predict_model_ext_adv: bool | None = None
    predict_model_bgp: bool | None = None
    #: False when the network has no classic collection source with that name
    #: (then ``unknown`` says so: a controller-managed device is configured
    #: elsewhere), or the list could not be read.
    device_found: bool = False
    collect_bgp_advertisements: bool | None = None
    #: BOTH, ADJ_RIB_IN or ADJ_RIB_OUT, with Forward's unstated default filled in
    #: as BOTH when collection is on.
    bgp_table_type: str | None = None
    bgp_peer_type: str | None = None
    #: True when the device is in one of the network's BGP collection
    #: requirement groups. Forward merges the group's required options into
    #: such a device's setup when it is saved.
    required_by_network: bool = False
    problems: list[str] = field(default_factory=list)
    unknown: list[str] = field(default_factory=list)
    #: Why each ``unknown`` entry could not be checked, keyed by its exact text.
    unknown_reasons: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        """Every check ran and none found a problem. Anything unknown is not a pass."""
        return not self.problems and not self.unknown

    @property
    def blocked(self) -> bool:
        """A check that ran found a problem: Predict would model nothing for the device."""
        return bool(self.problems)


@dataclass(frozen=True)
class BgpKeyOutcome:
    """What the diffs view says about one key the caller staged or withdrew."""

    key: BgpAdvertisementKey
    #: False when no diff row carries the key: the stage is not in the draft,
    #: or sits in another device's draft.
    found: bool = False
    #: The row's change, as Forward's ``BgpChangeType`` names it. ``None`` when
    #: not found. An unknown value from a newer Forward comes back as itself.
    change: Change | None = None
    diff: BgpAdvertisementDiff | None = None


@dataclass(frozen=True)
class BgpStagingReport:
    """The read-back, one outcome per key, in the order given."""

    staged: list[BgpKeyOutcome] = field(default_factory=list)
    withdrawn: list[BgpKeyOutcome] = field(default_factory=list)
    #: One line per key that matched nothing: a withdrawal that reads
    #: ``ABSENT``, or any key with no diff row.
    unmatched: list[str] = field(default_factory=list)


def key_of(advertisement: BgpAdvertisement | BgpAdvertisementKey) -> BgpAdvertisementKey:
    """The (vrf, external peer, prefix) key of an advertisement."""
    return BgpAdvertisementKey(
        vrf=advertisement.vrf,
        external_peer=advertisement.external_peer,
        prefix=advertisement.prefix,
    )


def _comparable(key: BgpAdvertisementKey) -> KeyTuple:
    """Trimmed, with a host prefix in one form whether given bare or as /32 (/128)."""
    vrf, peer, prefix = key.vrf.strip(), key.external_peer.strip(), key.prefix.strip()
    with contextlib.suppress(ValueError):
        prefix = str(ipaddress.ip_network(ipaddress.ip_address(prefix)))
    with contextlib.suppress(ValueError):
        peer = str(ipaddress.ip_address(peer))
    return vrf, peer, prefix


def _describe(key: BgpAdvertisementKey, *, withdrawal: bool) -> str:
    verb = "withdraw" if withdrawal else "stage"
    return f"{verb} {key.prefix} via {key.external_peer} in VRF {key.vrf}"


def match_staging(
    diffs: Iterable[BgpAdvertisementDiff],
    staged: Sequence[BgpAdvertisement] = (),
    withdrawn: Sequence[BgpAdvertisementKey] = (),
) -> BgpStagingReport:
    """Match each staged advertisement and withdrawn key to its diff row.

    A staged advertisement matches only a row with that key in
    ``updateAdded``; a withdrawal only a row with it in ``updateRemoved``.

    A staged advertisement that reads ``INJECTED`` where a received route was
    meant to be replaced is not counted as unmatched: that is how an EBGP/IBGP
    type mismatch shows (the received route stays and the staged one is added
    beside it), so a caller expecting ``MODIFIED`` checks
    :attr:`BgpKeyOutcome.change` itself. ``DUPLICATED`` is reported as is.
    """
    # Forward keeps staged and withdrawn advertisements as two sets
    # (computeBgpAdvertisementDiff) and a key can be in both, so a staged key is
    # matched only against added rows and a withdrawal only against withdrawn
    # rows: one map would let a withdrawn row answer for a staged key.
    added: dict[KeyTuple, BgpAdvertisementDiff] = {}
    removed: dict[KeyTuple, BgpAdvertisementDiff] = {}
    for diff in diffs:
        if diff.update_added is not None:
            added[_comparable(key_of(diff.update_added))] = diff
        elif diff.update_removed is not None:
            removed[_comparable(key_of(diff.update_removed))] = diff

    report = BgpStagingReport()

    def look(key: BgpAdvertisementKey, *, withdrawal: bool) -> BgpKeyOutcome:
        diff = (removed if withdrawal else added).get(_comparable(key))
        outcome = (
            BgpKeyOutcome(key=key, found=True, change=diff.change, diff=diff)
            if diff is not None
            else BgpKeyOutcome(key=key)
        )
        if not outcome.found or (withdrawal and outcome.change == Change.absent):
            report.unmatched.append(_describe(key, withdrawal=withdrawal))
        return outcome

    for advertisement in staged:
        report.staged.append(look(key_of(advertisement), withdrawal=False))
    for key in withdrawn:
        report.withdrawn.append(look(key, withdrawal=True))
    return report


class RpBgpAttributes(BaseModel):
    """A BGP node's route attributes from ``getRpPrefixDetails`` (Forward's ``BgpRouteAttributes``).

    Read from Forward's source, not yet decoded from a live answer; unknown
    fields are kept (``model_extra``). The predicted side differs from a
    collected :class:`~forward_sdk.models.BgpAdvertisement` in ways that matter
    when comparing the two:

    - ``med`` is always a number: Forward models "not set" as -1 but writes it
      as 0, so a predicted 0 means unset or an explicit 0, where a collected
      advertisement with no MED leaves the field out.
    - ``local_pref`` is always present (100 when nothing set it), where a
      collected eBGP advertisement with no policy leaves it out.
    - ``as_path`` is a list of sets of AS numbers (an AS_SEQUENCE step is a
      one-member set), not a flat list; see :attr:`flat_as_path`.

    Mirrors the Go SDK's ``RPBGPAttributes`` (v0.8.38).
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    as_path: list[list[int]] = Field(default_factory=list, alias="asPath")
    local_pref: int | None = Field(default=None, alias="localPref")
    med: int | None = None
    aigp_metric: int | None = Field(default=None, alias="aigpMetric")
    next_hop: str | None = Field(default=None, alias="bgpNextHopAddr")
    origin: str | None = None
    protocol: str | None = None
    standard_communities: list[Any] = Field(default_factory=list, alias="standardCommunities")
    extended_communities: list[Any] = Field(default_factory=list, alias="extendedCommunities")
    large_communities: list[Any] = Field(default_factory=list, alias="largeCommunities")
    next_hop_discard: bool | None = Field(default=None, alias="nextHopDiscard")

    @property
    def flat_as_path(self) -> list[int]:
        """``as_path`` flattened in order: the shape of a collected advertisement's AS path.

        An AS_SET (a set of several members) loses its set-ness here.
        """
        return [number for step in self.as_path for number in step]


#: Keys of a ``getRpPrefixDetails`` answer, which an attributes object does not have.
_ANSWER_KEYS = frozenset({"attributes", "error", "nodeAttributes", "policies"})


def bgp_attributes(details: Any) -> RpBgpAttributes:
    """Decode the attributes of a ``getRpPrefixDetails`` answer for a BGP node.

    ``details`` is the answer (``DevRoutingRpPrefixDetails``, or the same as a
    raw mapping) or its ``attributes`` mapping. Raises ``ValueError`` when there are no attributes,
    or when they belong to another protocol (OSPF, IS-IS, config, OMP).
    """
    error = getattr(details, "error", None)
    attributes = getattr(details, "attributes", details)
    if isinstance(attributes, Mapping) and _ANSWER_KEYS & attributes.keys():
        # A raw answer, not the attributes object.
        error = attributes.get("error")
        attributes = attributes.get("attributes")
    if not attributes:
        detail = f": {error}" if error else ""
        raise ValueError(f"the prefix details carry no attributes{detail}")
    protocol = str(attributes.get("protocol") or "")
    if protocol and protocol.upper() != "BGP":
        raise ValueError(f"the node's attributes are {protocol}, not BGP")
    return RpBgpAttributes.model_validate(attributes)
