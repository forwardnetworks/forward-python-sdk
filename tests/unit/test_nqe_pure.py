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
from forward_sdk.nqe import PageGuards, PageTracker, QueryRef, sanitize_commit_id
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
from forward_sdk.nqe.where import literal, membership, tag_scope, where

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
    @pytest.mark.parametrize("value", ["", None, "head", "HEAD", "84f84b0", "abc123"])
    def test_unusable_values_are_dropped(self, value: str | None) -> None:
        """Forward rejects abbreviated hashes and does not know the name 'head'."""
        assert sanitize_commit_id(value) is None

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

    def test_membership_of_one_value(self) -> None:
        assert membership("device.tagNames", ["core"]) == 'device.tagNames contains "core"'

    def test_membership_any_and_all(self) -> None:
        assert membership("t", ["a", "b"]) == '(t contains "a" || t contains "b")'
        assert membership("t", ["a", "b"], match="all") == '(t contains "a" && t contains "b")'

    def test_membership_negation_excludes_every_value(self) -> None:
        assert membership("t", ["a", "b"], negate=True) == '!(t contains "a" && t contains "b")'

    def test_empty_values_produce_no_clause(self) -> None:
        assert membership("t", []) is None
        assert membership("t", ["", None]) is None  # type: ignore[list-item]

    def test_where_skips_empty_clauses(self) -> None:
        assert where("a == 1", None, "b == 2") == "where a == 1\nwhere b == 2"
        assert where(None) == ""

    def test_tag_scope_combines_include_and_exclude(self) -> None:
        clause = tag_scope(include=["core"], exclude=["lab"])
        assert (
            clause == 'where device.tagNames contains "core"\nwhere !device.tagNames contains "lab"'
        )

    def test_tag_scope_without_tags_is_unscoped(self) -> None:
        assert tag_scope() == ""

    def test_injection_attempt_is_escaped_not_executed(self) -> None:
        clause = membership("t", ['x" || true || "'])
        assert clause == 't contains "x\\" || true || \\""'


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
        (tmp_path / "helpers.nqe").write_text("pattern helper() = 1;\n")
        main = tmp_path / "main.nqe"
        main.write_text('import "helpers";\nforeach d in network.devices select {n: d.name}\n')

        combined = inline_local_imports(main)
        assert "pattern helper() = 1;" in combined
        assert 'import "helpers"' not in combined
        assert combined.index("helper") < combined.index("foreach")

    def test_import_closure_is_dependency_ordered_and_deduplicated(self, tmp_path: Path) -> None:
        (tmp_path / "base.nqe").write_text("// base\n")
        (tmp_path / "mid.nqe").write_text('import "base";\n// mid\n')
        main = tmp_path / "main.nqe"
        main.write_text('import "base";\nimport "mid";\n// main\n')

        names = [p.name for p in local_import_closure(main)]
        assert names == ["base.nqe", "mid.nqe", "main.nqe"]

    def test_import_cycle_is_reported(self, tmp_path: Path) -> None:
        (tmp_path / "a.nqe").write_text('import "b";\n')
        (tmp_path / "b.nqe").write_text('import "a";\n')
        with pytest.raises(ValueError, match="circular import"):
            local_import_closure(tmp_path / "a.nqe")

    def test_missing_import_is_reported(self, tmp_path: Path) -> None:
        main = tmp_path / "main.nqe"
        main.write_text('import "nope";\n')
        with pytest.raises(ValueError, match="not found"):
            local_import_closure(main)

    def test_import_cannot_escape_the_query_directory(self, tmp_path: Path) -> None:
        outside = tmp_path / "outside.nqe"
        outside.write_text("// secret\n")
        queries = tmp_path / "queries"
        queries.mkdir()
        main = queries / "main.nqe"
        main.write_text('import "../outside";\n')
        with pytest.raises(ValueError, match="escapes"):
            local_import_closure(main, root=queries)

    def test_library_imports_are_left_alone(self, tmp_path: Path) -> None:
        main = tmp_path / "main.nqe"
        main.write_text('import "@fwd/library";\nforeach d in x select {}\n')
        assert 'import "@fwd/library";' in inline_local_imports(main)

    def test_load_query_for_execution_strips_key(self, tmp_path: Path) -> None:
        path = tmp_path / "q.nqe"
        path.write_text(QUERY_WITH_KEY)
        assert "@primaryKey" not in load_query(path)
        assert "@primaryKey" in load_query(path, for_execution=False)
