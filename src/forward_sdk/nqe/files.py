"""Working with ``.nqe`` query files.

Integrations that ship queries alongside their code hit the same three problems,
which the Forward NetBox and Nautobot plugins each solved separately:

* A query saved in the library carries an ``@primaryKey`` annotation that the
  run endpoints reject, so it must be stripped before running inline.
* Query files import shared helpers from neighbouring files, but the library has
  no notion of a local import, so imports must be inlined before publishing.
* The columns a query produces are a contract with the code consuming them, and
  drift between the two is worth catching in a test rather than in production.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from pathlib import Path

__all__ = [
    "contract_version",
    "inline_local_imports",
    "load_query",
    "local_import_closure",
    "select_field_sets",
    "strip_primary_key",
]

#: ``@primaryKey(a, b)`` on its own line, as written by the query library.
PRIMARY_KEY_RE = re.compile(r"^[ \t]*@primaryKey\([^\n]*\)[ \t]*\r?\n", re.MULTILINE)

#: A local import: `import "shared_helpers";`. Library imports start with `@`
#: and are left alone.
LOCAL_IMPORT_RE = re.compile(
    r'^[ \t]*import[ \t]+"(?P<target>[^"@][^"]*)"[ \t]*;[ \t]*$', re.MULTILINE
)

#: The start of a `select { ... }` block, optionally `select distinct`.
SELECT_RE = re.compile(r"\bselect\s+(?:distinct\s+)?\{")

#: A field name at the start of a line inside a select block: `Name: ...`.
FIELD_RE = re.compile(r"^\s*([A-Za-z_]\w*)\s*:")

#: `@contract-version v2` in a header comment.
CONTRACT_VERSION_RE = re.compile(r"@contract-version\s+(\S+)")

QUERY_SUFFIX = ".nqe"


def strip_primary_key(source: str) -> str:
    """Remove ``@primaryKey`` annotations so a query can be run inline.

    The annotation is meaningful only to the query library. The run endpoints
    reject it, so a file that is both published and run inline needs both forms.
    """
    return PRIMARY_KEY_RE.sub("", source)


def contract_version(source: str) -> str | None:
    """Return the ``@contract-version`` recorded in the file, if any."""
    match = CONTRACT_VERSION_RE.search(source)
    return match.group(1) if match else None


def select_field_sets(source: str) -> tuple[tuple[str, ...], ...]:
    """Return the field names of each ``select { ... }`` block.

    Used to check that a query still produces the columns its consumer expects.
    This is a structural scan, not a parser: it reads field names at the start
    of a line and tracks brace depth, which is enough for the conventional
    layout and does not require an NQE grammar.
    """
    blocks: list[tuple[str, ...]] = []
    for match in SELECT_RE.finditer(source):
        fields: list[str] = []
        depth = 1
        index = match.end()
        line_start = index
        # A field whose value opens a nested record ("detail: {") raises the
        # depth partway through its own line, so the depth that decides whether
        # a line declares a top-level field is the one at the line's start.
        line_depth = depth
        while index < len(source) and depth > 0:
            char = source[index]
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    break
            elif char == "\n":
                fields.extend(_field_names(source[line_start:index], line_depth))
                line_start = index + 1
                line_depth = depth
            index += 1
        fields.extend(_field_names(source[line_start:index], line_depth))
        blocks.append(tuple(dict.fromkeys(fields)))
    return tuple(blocks)


def _field_names(line: str, depth: int) -> list[str]:
    """Field names declared directly in the select block, not in nested ones."""
    if depth != 1:
        return []
    text = line.split("//", 1)[0]
    match = FIELD_RE.match(text)
    return [match.group(1)] if match else []


def local_import_closure(path: Path, *, root: Path | None = None) -> tuple[Path, ...]:
    """Return ``path``'s local imports, dependencies first, without duplicates.

    Raises:
        ValueError: On an import cycle, a missing file, or a path that escapes
            ``root``.
    """
    base = (root or path.parent).resolve()
    ordered: list[Path] = []
    seen: set[Path] = set()

    def visit(current: Path, stack: tuple[Path, ...]) -> None:
        resolved = current.resolve()
        if resolved in stack:
            cycle = " -> ".join(p.name for p in (*stack, resolved))
            raise ValueError(f"circular import in query files: {cycle}")
        if resolved in seen:
            return
        if not resolved.is_file():
            raise ValueError(f"imported query file not found: {current}")
        if base not in resolved.parents and resolved.parent != base:
            raise ValueError(f"imported query file escapes {base}: {resolved}")

        for target in LOCAL_IMPORT_RE.findall(resolved.read_text()):
            visit(_resolve_import(resolved, target, base), (*stack, resolved))

        seen.add(resolved)
        ordered.append(resolved)

    visit(path, ())
    return tuple(ordered)


def _resolve_import(source: Path, target: str, root: Path) -> Path:
    name = target if target.endswith(QUERY_SUFFIX) else target + QUERY_SUFFIX
    candidate = (source.parent / name).resolve()
    if candidate.is_file():
        return candidate
    return (root / name).resolve()


def inline_local_imports(path: Path, *, root: Path | None = None) -> str:
    """Return ``path``'s source with its local imports expanded in place.

    The query library has no local imports, so a query split across files has to
    be flattened before it can be published. Imported files are concatenated
    dependencies-first, with their own import lines removed.
    """
    closure = local_import_closure(path, root=root)
    parts: list[str] = []
    for dependency in closure:
        body = LOCAL_IMPORT_RE.sub("", dependency.read_text()).strip()
        if body:
            header = f"// begin {dependency.name}" if dependency != closure[-1] else ""
            parts.append(f"{header}\n{body}" if header else body)
    return "\n\n".join(parts) + "\n"


def load_query(
    path: str | Path,
    *,
    for_execution: bool = True,
    inline_imports: bool = True,
    root: Path | None = None,
) -> str:
    """Read a ``.nqe`` file, ready to run or ready to publish.

    Args:
        path: The query file.
        for_execution: Strip ``@primaryKey``, which the run endpoints reject.
            Set ``False`` when publishing to the library, which needs it.
        inline_imports: Expand local ``import "..."`` lines.
        root: Directory imports may not escape. Defaults to the file's own.
    """
    file_path = Path(path)
    source = inline_local_imports(file_path, root=root) if inline_imports else file_path.read_text()
    return strip_primary_key(source) if for_execution else source


def iter_query_files(directory: str | Path) -> Iterable[Path]:
    """Yield the ``.nqe`` files in ``directory``, in a stable order."""
    return sorted(Path(directory).glob(f"*{QUERY_SUFFIX}"))


def missing_fields(source: str, expected: Sequence[str]) -> tuple[str, ...]:
    """Return expected column names that no ``select`` block produces."""
    produced = {name for block in select_field_sets(source) for name in block}
    return tuple(name for name in expected if name not in produced)
