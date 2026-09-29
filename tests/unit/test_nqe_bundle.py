"""Merging a query and its imports into one self-contained source."""

from __future__ import annotations

import re

import pytest

from forward_sdk.nqe import NqeBundleError, bundle_sources
from forward_sdk.nqe.bundle import import_paths, module_path, tokenize

ENTRY = "/Lib/Main"


def bundle(**sources: str) -> str:
    """Bundle modules given as keyword arguments, ``Main`` being the entry."""
    return bundle_sources(ENTRY, {f"/Lib/{name}": text for name, text in sources.items()}).source


def body(source: str, module: str) -> str:
    """The rewritten text of one module within a bundle."""
    return source.split(f"// ---- module /Lib/{module}\n", 1)[1].split("// ---- module", 1)[0]


class TestPaths:
    def test_imports_are_absolute_library_paths(self) -> None:
        assert module_path("A/B") == "/A/B"
        assert module_path("/A/B") == "/A/B"
        assert module_path("@fwd/Lib") == "@fwd/Lib"

    def test_imports_are_read_in_order(self) -> None:
        source = 'import "A/One";\nimport "@fwd/Two";\nx = 1;'
        assert import_paths(source) == ("/A/One", "@fwd/Two")

    def test_an_import_inside_a_string_is_not_an_import(self) -> None:
        assert import_paths('x = "import \\"A\\";";') == ()


class TestNamespaces:
    def test_private_helpers_with_the_same_name_stay_distinct(self) -> None:
        """Two modules each keeping a private ``flatten`` is ordinary."""
        source = bundle(
            Helper="flatten(x) = x;\nexport helper(x) = flatten(x);",
            Main='import "Lib/Helper";\nflatten(x) = [x];\n@query\nmain() = helper(flatten(1));',
        )
        helper, main = body(source, "Helper"), body(source, "Main")
        helper_flatten = re.search(r"(\w+__flatten)\(x\) = x;", helper)
        main_flatten = re.search(r"(\w+__flatten)\(x\) = \[x\];", main)
        assert helper_flatten and main_flatten
        assert helper_flatten.group(1) != main_flatten.group(1)
        assert f"{main_flatten.group(1)}(1)" in main
        assert f"= {helper_flatten.group(1)}(x);" in helper

    def test_exports_resolve_through_the_importing_module(self) -> None:
        source = bundle(
            Helper="export answer = 42;",
            Main='import "Lib/Helper";\n@query\nmain() = answer;',
        )
        declared = re.search(r"export (\w+__answer) = 42;", source)
        assert declared
        assert f"main() = {declared.group(1)};" in source

    def test_a_module_does_not_see_its_imports_imports(self) -> None:
        """Only direct imports are visible, so a transitive name is left alone."""
        source = bundle(
            Deep="export deep = 1;",
            Mid='import "Lib/Deep";\nexport mid = deep;',
            Main='import "Lib/Mid";\n@query\nmain() = deep;',
        )
        assert body(source, "Main").rstrip().endswith("main() = deep;")

    def test_dependencies_come_first_and_the_query_last(self) -> None:
        result = bundle_sources(
            ENTRY,
            {
                "/Lib/Main": 'import "Lib/A";\n@query\nmain() = a;',
                "/Lib/A": 'import "Lib/B";\nexport a = b;',
                "/Lib/B": "export b = 1;",
            },
        )
        assert result.modules == ("/Lib/B", "/Lib/A", "/Lib/Main")
        assert result.source.count("@query") == 1
        assert result.source.rstrip().endswith("main() = " + result.renamed["/Lib/A"]["a"] + ";")
        assert "import" not in result.source

    def test_a_query_annotation_in_a_dependency_is_dropped(self) -> None:
        """Only one @query may exist, and the entry's is the one being run."""
        source = bundle(
            Helper="@query\nexport helper(x: Number) = x;",
            Main='import "Lib/Helper";\n@query\nmain() = helper(1);',
        )
        assert source.count("@query") == 1
        assert "@query" not in body(source, "Helper")


class TestWhatIsNotAReference:
    def test_parameters_shadow_top_level_names(self) -> None:
        source = bundle(Main="x = 1;\nf(x) = x + 1;\n@query\nmain() = f(x);")
        assert "f(x) = x + 1;" in re.sub(r"\w+__f\b", "f", source)
        assert re.search(r"\w+__f\(\w+__x\);", source)

    def test_local_bindings_shadow_until_their_expression_ends(self) -> None:
        source = bundle(Main=("x = 1;\n@query\nmain() = [foreach x in [2] select x, x];"))
        rewritten = body(source, "Main")
        assert "foreach x in [2] select x," in rewritten
        assert re.search(r"select x, \w+__x\]", rewritten), "the binding ends at the comma"

    def test_let_and_group_bindings(self) -> None:
        source = bundle(
            Main=(
                "k = 1;\nv = 2;\n"
                "@query\n"
                "main() = foreach r in [1] let k = r group r as v by k as g select { v: v, g: g };"
            )
        )
        rewritten = body(source, "Main")
        assert "let k = r group r as v by k as g select { v: v, g: g }" in rewritten

    def test_field_selectors_and_record_labels_are_names_not_references(self) -> None:
        source = bundle(Main="name = 1;\n@query\nmain() = { name: name, other: device.name };")
        assert re.search(r"\{ name: \w+__name, other: device\.name \}", source)

    def test_record_shorthand_keeps_its_field_name(self) -> None:
        """``{ name }`` means ``{ name: name }``; renaming it would rename the field."""
        source = bundle(Main="name = 1;\n@query\nmain() = { name, other: 2 };")
        assert re.search(r"\{ name: \w+__name, other: 2 \}", source)

    def test_sort_keys_name_columns(self) -> None:
        query = "foreach d in [1] select { Name: d } order by Name asc"
        source = bundle(Main=f"Name = 1;\n@query\nmain() = {query};")
        assert "order by Name asc" in source

    def test_when_tags_and_their_bindings(self) -> None:
        source = bundle(
            Main=("ok = 1;\nv = 2;\n@query\nmain() = when x is ok(v) -> v; otherwise -> ok;")
        )
        rewritten = body(source, "Main")
        assert "when x is ok(v) -> v; otherwise -> " in rewritten
        assert re.search(r"otherwise -> \w+__ok;", rewritten)

    def test_a_semicolon_between_when_cases_does_not_end_the_declaration(self) -> None:
        source = bundle(Main="f(x) = when x is a -> 1; b -> 2;\n@query\nmain() = f(1);")
        assert re.search(r"\w+__f\(x\) = when x is a -> 1; b -> 2;", source)

    def test_literals_and_comments_are_left_alone(self) -> None:
        source = bundle(
            Main=(
                "x = 1;\n"
                "// x in a line comment\n"
                "/* x in a block comment */\n"
                "@query\n"
                'main() = { a: "x \\" x", b: re`x[`]x`, c: `x {x:string}`, d: x };'
            )
        )
        rewritten = body(source, "Main")
        assert "// x in a line comment" in rewritten
        assert "/* x in a block comment */" in rewritten
        assert '"x \\" x"' in rewritten
        assert "re`x[`]x`" in rewritten
        assert "`x {x:string}`" in rewritten
        assert re.search(r"d: \w+__x \}", rewritten)

    def test_text_blocks_are_opaque(self) -> None:
        source = bundle(Main='x = 1;\nt = """\nx and "x"\n""";\n@query\nmain() = t;')
        assert '"""\nx and "x"\n"""' in source


class TestRefusals:
    def test_a_missing_module_names_its_importer(self) -> None:
        with pytest.raises(NqeBundleError, match=r"/Lib/Gone \(imported by /Lib/Main\)"):
            bundle(Main='import "Lib/Gone";\n@query\nmain() = 1;')

    def test_an_import_cycle(self) -> None:
        with pytest.raises(NqeBundleError, match="circular import"):
            bundle(
                Main='import "Lib/A";\n@query\nmain() = 1;',
                A='import "Lib/Main";\nexport a = 1;',
            )

    def test_module_attributes_cannot_be_merged(self) -> None:
        with pytest.raises(NqeBundleError, match="module attributes"):
            bundle(Main="@juniper(INDENTATION);\n@query\nmain() = 1;")

    def test_a_bare_query_expression(self) -> None:
        with pytest.raises(NqeBundleError, match="bare query expression"):
            bundle(Main="foreach d in network.devices select d")

    def test_an_unterminated_literal(self) -> None:
        with pytest.raises(NqeBundleError, match="unterminated string"):
            tokenize('x = "open')

    def test_an_entry_without_a_query(self) -> None:
        with pytest.raises(NqeBundleError, match="exactly one @query to run, found 0"):
            bundle(Main="export x = 1;")

    def test_an_entry_with_two_queries(self) -> None:
        """Only one @query may exist; the server would reject the bundle."""
        with pytest.raises(NqeBundleError, match="found 2"):
            bundle(Main="@query\na() = 1;\n@query\nb() = 2;")

    def test_a_name_declared_twice(self) -> None:
        with pytest.raises(NqeBundleError, match="declared twice"):
            bundle(Main="x = 1;\nx = 2;\n@query\nmain() = x;")
