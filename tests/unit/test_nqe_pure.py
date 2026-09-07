"""The NQE helpers that involve no I/O."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from forward_sdk.errors import (
    ForwardConfigurationError,
    ForwardPaginationError,
    ForwardTimeoutError,
)
from forward_sdk.models import Vendor
from forward_sdk.nqe import PageGuards, PageTracker, QueryRef, sanitize_commit_id
from forward_sdk.nqe.enums import NQE_ENUMS
from forward_sdk.nqe.enums import members as nqe_members
from forward_sdk.nqe.files import (
    contract_version,
    inline_local_imports,
    load_query,
    local_import_closure,
    missing_fields,
    select_field_sets,
    strip_primary_key,
)
from forward_sdk.nqe.pagination import Decision
from forward_sdk.nqe.query_ref import SortKey
from forward_sdk.nqe.where import (
    enum_one_of,
    literal,
    membership,
    one_of,
    tag_scope,
    where,
)

FULL_COMMIT = "84f84b0c0a0a1805ddff0ca5451c2c55c58605e5"


class TestQueryRef:
    def test_requires_exactly_one_identifier(self) -> None:
        with pytest.raises(ForwardConfigurationError, match="exactly one"):
            QueryRef()
        with pytest.raises(ForwardConfigurationError, match="exactly one"):
            QueryRef(text="foreach x in y select {}", query_id="FQ_1")

    def test_path_gains_leading_slash(self) -> None:
        assert QueryRef.by_path("NetBox/Devices").path == "/NetBox/Devices"
        assert QueryRef.by_path("/NetBox/Devices").path == "/NetBox/Devices"

    def test_inline_payload_carries_source_and_parameters(self) -> None:
        ref = QueryRef.inline("foreach d in network.devices select {n: d.name}", limit=5)
        assert ref.to_payload() == {
            "query": "foreach d in network.devices select {n: d.name}",
            "parameters": {"limit": 5},
        }

    def test_by_id_payload_includes_full_commit(self) -> None:
        ref = QueryRef.by_id("FQ_abc", commit_id=FULL_COMMIT)
        assert ref.to_payload() == {"queryId": "FQ_abc", "commitId": FULL_COMMIT}

    def test_unresolved_path_cannot_be_sent(self) -> None:
        ref = QueryRef.by_path("/NetBox/Devices")
        assert not ref.is_runnable
        with pytest.raises(ForwardConfigurationError, match="has not been resolved"):
            ref.to_payload()

    def test_resolution_makes_a_path_runnable(self) -> None:
        ref = QueryRef.by_path("/NetBox/Devices").resolved("FQ_xyz", FULL_COMMIT)
        assert ref.is_runnable
        assert ref.to_payload()["queryId"] == "FQ_xyz"
        assert ref.path == "/NetBox/Devices"

    def test_sort_keys_render(self) -> None:
        ref = QueryRef.inline("q").with_sort("Name", SortKey("Speed", "DESC"))
        assert ref.to_payload()["sortKeys"] == [
            {"columnName": "Name", "order": "ASC"},
            {"columnName": "Speed", "order": "DESC"},
        ]
        assert "sortKeys" not in ref.to_payload(include_sort=False)

    def test_immutability(self) -> None:
        original = QueryRef.inline("q")
        assert original.with_parameters(a=1) is not original
        assert original.parameters == {}


class TestCommitIdSanitizing:
    @pytest.mark.parametrize("value", ["", None, "head", "HEAD"])
    def test_head_and_empty_are_dropped(self, value: str | None) -> None:
        """Forward does not know the symbolic name, and omitting it means the same."""
        assert sanitize_commit_id(value) is None

    @pytest.mark.parametrize("value", ["84f84b0", "abc123", "0" * 39])
    def test_abbreviated_hash_is_refused(self, value: str) -> None:
        """Dropping a pin silently would answer a different question.

        Forward cannot use a short hash. Discarding it would run against
        whatever is at head and report success, defeating the point of pinning.
        """
        with pytest.raises(ForwardConfigurationError, match="abbreviated"):
            sanitize_commit_id(value)

    def test_full_hash_is_kept(self) -> None:
        assert sanitize_commit_id(FULL_COMMIT) == FULL_COMMIT

    def test_non_hex_name_is_kept(self) -> None:
        """Only hex-looking values are treated as abbreviated hashes."""
        assert sanitize_commit_id("release-2026") == "release-2026"


class TestPageTracker:
    def test_stops_when_total_is_reached(self) -> None:
        tracker = PageTracker(page_size=2)
        assert tracker.observe([1, 2], total=3) is Decision.CONTINUE
        assert tracker.observe([3], total=3) is Decision.DONE
        assert tracker.rows == 3

    def test_short_page_ends_paging_without_a_total(self) -> None:
        tracker = PageTracker(page_size=10)
        assert tracker.observe([1, 2]) is Decision.DONE

    def test_offset_tracks_rows_collected(self) -> None:
        tracker = PageTracker(page_size=2)
        tracker.observe([1, 2], total=10)
        assert tracker.offset == 2

    def test_empty_page_before_total_is_an_error(self) -> None:
        """A truncated result set must not be returned as if it were complete."""
        tracker = PageTracker(page_size=2)
        tracker.observe([1, 2], total=10)
        with pytest.raises(ForwardPaginationError, match="ended early"):
            tracker.observe([], total=10)

    def test_empty_first_page_is_simply_empty(self) -> None:
        assert PageTracker().observe([], total=0) is Decision.DONE

    def test_repeated_full_pages_are_detected(self) -> None:
        """A server ignoring the offset would otherwise loop forever."""
        tracker = PageTracker(PageGuards(repeat_limit=3), page_size=2)
        page = [{"id": 1}, {"id": 2}]
        with pytest.raises(ForwardPaginationError, match="not advancing"):
            for _ in range(10):
                tracker.observe(list(page))

    def test_distinct_pages_do_not_trip_repeat_detection(self) -> None:
        tracker = PageTracker(PageGuards(repeat_limit=2), page_size=2)
        for start in range(0, 20, 2):
            assert tracker.observe([{"id": start}, {"id": start + 1}]) is Decision.CONTINUE

    def test_row_ceiling_stops_runaway_results(self) -> None:
        tracker = PageTracker(PageGuards(max_rows=4), page_size=2)
        tracker.observe([1, 2], total=100)
        with pytest.raises(ForwardPaginationError, match="row limit"):
            tracker.observe([3, 4], total=100)

    def test_page_ceiling_stops_runaway_paging(self) -> None:
        tracker = PageTracker(PageGuards(max_pages=2), page_size=1)
        tracker.observe([1], total=100)
        with pytest.raises(ForwardPaginationError, match="page limit"):
            tracker.observe([2], total=100)

    def test_deadline_is_enforced(self) -> None:
        tracker = PageTracker(PageGuards(deadline=time.monotonic() - 1), page_size=1)
        with pytest.raises(ForwardTimeoutError, match="deadline"):
            tracker.observe([1], total=10)

    def test_rejects_impossible_page_size(self) -> None:
        with pytest.raises(ValueError, match="page_size"):
            PageTracker(page_size=10_001)


class TestWhereBuilders:
    def test_literal_escapes_quotes_and_backslashes(self) -> None:
        assert literal('a"b\\c') == '"a\\"b\\\\c"'
        assert literal(True) == "true"
        assert literal(None) == "null"
        assert literal(7) == "7"

    def test_membership_uses_the_nqe_in_operator(self) -> None:
        """NQE's membership operators are `in` and `not in`, element on the left."""
        assert membership("device.tagNames", ["core"]) == '"core" in device.tagNames'

    def test_membership_any_and_all(self) -> None:
        assert membership("t", ["a", "b"]) == '("a" in t || "b" in t)'
        assert membership("t", ["a", "b"], match="all") == '("a" in t && "b" in t)'

    def test_exclusion_of_one_value(self) -> None:
        assert membership("t", ["lab"], negate=True) == '"lab" not in t'

    def test_exclusion_requires_every_value_absent(self) -> None:
        """Excluding two tags means neither may be present, not "not both".

        Getting this backwards admits devices carrying one of the excluded tags,
        which for a sync that prunes out-of-scope objects means deleting things
        that should have stayed.
        """
        assert membership("t", ["a", "b"], negate=True) == '("a" not in t && "b" not in t)'

    def test_match_does_not_apply_to_exclusion(self) -> None:
        """Exclusion is all-or-nothing, so `match` cannot invert it."""
        assert membership("t", ["a", "b"], negate=True, match="all") == membership(
            "t", ["a", "b"], negate=True
        )

    def test_empty_values_produce_no_clause(self) -> None:
        assert membership("t", []) is None
        assert membership("t", ["", None]) is None  # type: ignore[list-item]

    def test_one_of_puts_the_scalar_field_on_the_left(self) -> None:
        """A scalar needs `field in [values]`, the other direction from membership.

        Using membership on a scalar matches nothing, and a probe built that way
        reports an empty scope on a healthy network.
        """
        assert one_of("device.platform.vendor", ["cisco", "juniper"]) == (
            'device.platform.vendor in ["cisco", "juniper"]'
        )

    def test_one_of_with_no_values(self) -> None:
        assert one_of("device.model", []) is None

    def test_one_of_escapes_its_values(self) -> None:
        assert one_of("f", ['a"b']) == 'f in ["a\\"b"]'

    def test_nqe_member_names_are_checked_before_the_query_is_sent(self) -> None:
        """A REST name in a predicate fails here, not at query time.

        Verified live: NQE rejects Vendor.MICROSOFT with "Unknown alternative".
        Catching it locally turns a failed query into a message naming the two
        namespaces.
        """
        for rest_only in ("MICROSOFT", "GD", "IBM"):
            with pytest.raises(ValueError, match="NQE has no member"):
                enum_one_of("d.platform.vendor", "Vendor", [rest_only])

    def test_nqe_only_members_are_accepted(self) -> None:
        """The names NQE actually takes, which the REST model does not have."""
        assert enum_one_of("d.platform.vendor", "Vendor", ["AZURE"]) == (
            "d.platform.vendor == Vendor.AZURE"
        )
        assert enum_one_of("d.platform.vendor", "Vendor", ["GENERAL_DYNAMICS"]) == (
            "d.platform.vendor == Vendor.GENERAL_DYNAMICS"
        )

    def test_an_unknown_type_is_not_second_guessed(self) -> None:
        """The data model covers the network schema, not every namespace."""
        assert enum_one_of("f", "NotInTheDataModel", ["ANYTHING"]) == (
            "f == NotInTheDataModel.ANYTHING"
        )

    def test_no_nqe_type_has_zero_members(self) -> None:
        """So an empty result means "type unknown", not "nothing is valid".

        Consumers treat an empty member set as no information and accept the
        value, which is the right reading only while this holds. If a type ever
        arrives with no members, that fallback would silently switch off a
        check, so the guidance needs revisiting rather than the test relaxing.
        """
        empty = sorted(name for name, values in NQE_ENUMS.items() if not values)
        assert not empty, f"types with no members: {empty}; revisit members() docs"

    def test_nqe_enums_match_what_was_measured_live(self) -> None:
        """The generated lists agree with what a real instance accepted."""
        vendor = nqe_members("Vendor")
        assert {"AZURE", "GENERAL_DYNAMICS", "CISCO"} <= vendor
        assert not ({"MICROSOFT", "GD", "IBM"} & vendor)
        # NQE's DeviceType describes what a device does, in 16 members; the REST
        # model has 75 describing how it is deployed. Different concepts.
        assert len(nqe_members("DeviceType")) == 16
        assert nqe_members("CloudType") == frozenset({"AWS", "AZURE", "GCP", "IBM"})

    def test_shipped_enums_are_not_a_source_of_nqe_member_names(self) -> None:
        """The generated enums describe the REST schema, not NQE's namespace.

        Measured against a live instance: NQE accepts Vendor.AZURE and
        Vendor.GENERAL_DYNAMICS, which the SDK enum does not have, and rejects
        Vendor.MICROSOFT, Vendor.GD and Vendor.IBM, which it does. Building a
        predicate from these names produces a query Forward refuses, so the
        helper's docstring points at the NQE reference instead. This test
        records the divergence rather than asserting it away.
        """
        members = {e.value for e in Vendor}
        assert {"MICROSOFT", "GD", "IBM"} <= members, "REST-only names still present"
        assert not ({"AZURE", "GENERAL_DYNAMICS"} & members), (
            "the SDK enum has gained an NQE-only name; re-check the guidance"
        )

    def test_enum_one_of_accepts_shipped_enum_members(self) -> None:
        """Members may be enum values; the type name is still given explicitly.

        The NQE type name is not always the SDK class name, so it cannot be
        inferred: device.platform.os has NQE type OS while the model class is
        VendorOs, and NQE rejects VendorOs as not in scope.
        """
        assert enum_one_of("d.platform.vendor", "Vendor", [Vendor.cisco]) == (
            "d.platform.vendor == Vendor.CISCO"
        )
        assert enum_one_of("d.platform.os", "OS", ["PAN_OS"]) == ("d.platform.os == OS.PAN_OS")

    def test_enum_one_of_emits_equality_not_membership(self) -> None:
        """An enum member is not a string, so `in [..]` fails at run time."""
        assert enum_one_of("f", "Vendor", ["CISCO", "ARISTA"]) == (
            "(f == Vendor.CISCO || f == Vendor.ARISTA)"
        )

    def test_enum_one_of_refuses_an_unquotable_member(self) -> None:
        with pytest.raises(ValueError, match="not a valid enum member"):
            enum_one_of("f", "Vendor", ['X" || true'])

    def test_enum_one_of_with_no_values(self) -> None:
        assert enum_one_of("f", "Vendor", []) is None

    def test_where_skips_empty_clauses(self) -> None:
        assert where("a == 1", None, "b == 2") == "where a == 1\nwhere b == 2"
        assert where(None) == ""

    def test_tag_scope_combines_include_and_exclude(self) -> None:
        clause = tag_scope(include=["core"], exclude=["lab"])
        assert clause == ('where "core" in device.tagNames\nwhere "lab" not in device.tagNames')

    def test_tag_scope_without_tags_is_unscoped(self) -> None:
        assert tag_scope() == ""

    def test_injection_attempt_is_escaped_not_executed(self) -> None:
        """A value that looks like NQE must arrive as a string, not as syntax."""
        clause = membership("t", ['x" || true || "'])
        assert clause == r'"x\" || true || \"" in t'


QUERY_WITH_KEY = """/**
 * @intent Devices
 * @contract-version v2
 */
@primaryKey(name)
foreach device in network.devices
where device.name != ""
select {
  name: device.name,  // the device name
  vendor: device.platform.vendor,
}
"""


class TestQueryFiles:
    def test_strip_primary_key(self) -> None:
        stripped = strip_primary_key(QUERY_WITH_KEY)
        assert "@primaryKey" not in stripped
        assert "foreach device in network.devices" in stripped

    def test_strip_primary_key_is_a_no_op_without_one(self) -> None:
        source = "foreach d in network.devices select {n: d.name}"
        assert strip_primary_key(source) == source

    def test_contract_version(self) -> None:
        assert contract_version(QUERY_WITH_KEY) == "v2"
        assert contract_version("foreach x select {}") is None

    def test_select_field_sets(self) -> None:
        assert select_field_sets(QUERY_WITH_KEY) == (("name", "vendor"),)

    def test_select_field_sets_ignores_nested_blocks(self) -> None:
        source = """
        foreach d in network.devices
        select {
          name: d.name,
          detail: {
            inner: d.other,
          },
          vendor: d.vendor,
        }
        """
        assert select_field_sets(source) == (("name", "detail", "vendor"),)

    def test_missing_fields_detects_contract_drift(self) -> None:
        assert missing_fields(QUERY_WITH_KEY, ["name", "vendor"]) == ()
        assert missing_fields(QUERY_WITH_KEY, ["name", "serial"]) == ("serial",)

    def test_inline_local_imports(self, tmp_path: Path) -> None:
        (tmp_path / "helpers.nqe").write_text("pattern helper() = 1;\n", encoding="utf-8")
        main = tmp_path / "main.nqe"
        main.write_text(
            'import "helpers";\nforeach d in network.devices select {n: d.name}\n', encoding="utf-8"
        )

        combined = inline_local_imports(main)
        assert "pattern helper() = 1;" in combined
        assert 'import "helpers"' not in combined
        assert combined.index("helper") < combined.index("foreach")

    def test_import_closure_is_dependency_ordered_and_deduplicated(self, tmp_path: Path) -> None:
        (tmp_path / "base.nqe").write_text("// base\n", encoding="utf-8")
        (tmp_path / "mid.nqe").write_text('import "base";\n// mid\n', encoding="utf-8")
        main = tmp_path / "main.nqe"
        main.write_text('import "base";\nimport "mid";\n// main\n', encoding="utf-8")

        names = [p.name for p in local_import_closure(main)]
        assert names == ["base.nqe", "mid.nqe", "main.nqe"]

    def test_import_cycle_is_reported(self, tmp_path: Path) -> None:
        (tmp_path / "a.nqe").write_text('import "b";\n', encoding="utf-8")
        (tmp_path / "b.nqe").write_text('import "a";\n', encoding="utf-8")
        with pytest.raises(ValueError, match="circular import"):
            local_import_closure(tmp_path / "a.nqe")

    def test_missing_import_is_reported(self, tmp_path: Path) -> None:
        main = tmp_path / "main.nqe"
        main.write_text('import "nope";\n', encoding="utf-8")
        with pytest.raises(ValueError, match="not found"):
            local_import_closure(main)

    def test_import_cannot_escape_the_query_directory(self, tmp_path: Path) -> None:
        outside = tmp_path / "outside.nqe"
        outside.write_text("// secret\n", encoding="utf-8")
        queries = tmp_path / "queries"
        queries.mkdir()
        main = queries / "main.nqe"
        main.write_text('import "../outside";\n', encoding="utf-8")
        with pytest.raises(ValueError, match="escapes"):
            local_import_closure(main, root=queries)

    def test_library_imports_are_left_alone(self, tmp_path: Path) -> None:
        main = tmp_path / "main.nqe"
        main.write_text('import "@fwd/library";\nforeach d in x select {}\n', encoding="utf-8")
        assert 'import "@fwd/library";' in inline_local_imports(main)

    def test_load_query_for_execution_strips_key(self, tmp_path: Path) -> None:
        path = tmp_path / "q.nqe"
        path.write_text(QUERY_WITH_KEY, encoding="utf-8")
        assert "@primaryKey" not in load_query(path)
        assert "@primaryKey" in load_query(path, for_execution=False)


class TestEncoding:
    """Query files are UTF-8 wherever they are read.

    On Windows the default text encoding is the locale codepage, which cannot
    decode UTF-8, so a query containing a non-ASCII character would fail to load
    there and nowhere else.
    """

    def test_query_with_non_ascii_content_loads(self, tmp_path: Path) -> None:
        path = tmp_path / "q.nqe"
        path.write_text(
            '// Sites: München, Zürich, São Paulo — "smart quotes" too\n'
            "foreach device in network.devices\n"
            "select {\n  name: device.name,\n}\n",
            encoding="utf-8",
        )
        source = load_query(path)
        assert "München" in source
        assert select_field_sets(source) == (("name",),)

    def test_non_ascii_survives_import_inlining(self, tmp_path: Path) -> None:
        (tmp_path / "helpers.nqe").write_text("// Zürich helper\n", encoding="utf-8")
        main = tmp_path / "main.nqe"
        main.write_text('import "helpers";\n// São Paulo\n', encoding="utf-8")
        assert "Zürich" in inline_local_imports(main)
