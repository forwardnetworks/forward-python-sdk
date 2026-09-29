"""Combine a library query and its imports into one self-contained source.

Forward runs only committed queries by ID. Inline source can import library
modules, but those imports resolve against the library's current head: the
synchronous endpoint resolves them there, the asynchronous one does not resolve
them at all, and neither accepts a commit to pin them to. So a change to a
module three imports deep cannot be tried without committing it first, and a
baseline cannot be rerun once head has moved past it.

A bundle solves both. Every module is supplied as source -- fetched at a pinned
commit, or replaced by a local edit -- and merged into a single program that
imports nothing. Merging needs more than concatenation, because modules keep
private helpers with the same names (two ``flatten`` definitions is ordinary),
and a module sees only what it declares and what its direct imports export. So
each module's top-level declarations are renamed with a per-module prefix, and
every reference is rewritten to the declaration it resolved to before merging.

This is a token-level rewrite driven by the NQE lexer's rules, not a parser. It
understands what it must in order to rename safely -- string, text-block, regex
and pattern literals, comments, field selectors, record field names and
shorthand, sort keys, ``when`` tags, parameters and local bindings -- and raises
:class:`NqeBundleError` rather than guess at anything else. Forward typechecks
the result, so a rewrite mistake surfaces as a compile error rather than a
wrong answer in almost every case; comparing a bundle of an unchanged commit
with that commit run by ID catches the rest.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from forward_sdk.errors import ForwardConfigurationError
from forward_sdk.nqe.files import strip_primary_key

__all__ = [
    "NqeBundle",
    "NqeBundleError",
    "bundle_sources",
    "import_paths",
    "module_path",
]

#: The Forward-shipped library, as named in an import.
FWD_IMPORT_PREFIX = "@fwd/"

IDENTIFIER_START = re.compile(r"[A-Za-z_]")
IDENTIFIER_BODY = re.compile(r"[A-Za-z0-9_]*")
NUMBER = re.compile(r"[0-9]*\.?[0-9]*")
ANNOTATION = re.compile(r"@(?:query|primaryKey|juniper\((?:INDENTATION|NATIVE)\);)")

#: Words the lexer reserves. A module that declares one at top level is refused,
#: because renaming it would also rename the syntax.
KEYWORDS = frozenset(
    {
        "import", "export", "when", "is", "if", "then", "else", "select",
        "distinct", "limit", "order", "ascending", "descending", "asc", "desc",
        "natural", "foreach", "where", "let", "group", "by", "as", "true",
        "false", "null", "not", "in", "otherwise", "List", "Bag", "Set",
        "Pattern", "Regex", "PatternBlocks",
    }
)  # fmt: skip

SORT_DIRECTIONS = frozenset({"asc", "desc", "ascending", "descending"})
OPENERS = {"(": ")", "[": "]", "{": "}"}
CLOSERS = frozenset(OPENERS.values())
TWO_CHAR_PUNCTUATION = ("->", "==", "!=", "<=", ">=", "&&", "||")


class NqeBundleError(ForwardConfigurationError):
    """A set of modules could not be combined safely into one query."""


@dataclass(frozen=True, slots=True)
class Token:
    kind: str  # "ident", "punct", "literal" or "annotation"
    text: str
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class NqeBundle:
    """One self-contained query built from a library query and its imports.

    Attributes:
        entry: Library path of the query the bundle runs.
        source: The combined source, ready for ``QueryRef.inline``.
        modules: Every module included, dependencies first, entry last.
        overridden: The modules whose source came from the caller rather than
            the library.
        commit_id: The library commit the other modules were read from, when
            the bundle was fetched from a repository.
    """

    entry: str
    source: str
    modules: tuple[str, ...]
    overridden: tuple[str, ...] = ()
    commit_id: str | None = None
    renamed: Mapping[str, Mapping[str, str]] = field(default_factory=dict)

    def to_api(self) -> dict[str, Any]:
        return {
            "entry": self.entry,
            "modules": list(self.modules),
            "overridden": list(self.overridden),
            "commitId": self.commit_id,
            "sourceLength": len(self.source),
        }


def module_path(import_target: str) -> str:
    """The library path an ``import`` names, in the SDK's leading-slash form.

    Imports are absolute library paths written without the leading slash.
    Forward's own library is addressed as ``@fwd/...`` and is kept that way.
    """
    target = import_target.strip()
    if not target:
        raise NqeBundleError("an import names an empty path")
    if target.startswith(FWD_IMPORT_PREFIX):
        return target
    return target if target.startswith("/") else "/" + target


def import_paths(source: str) -> tuple[str, ...]:
    """The library paths a module imports, in the order it imports them."""
    return tuple(module_path(target) for target, _ in _imports(tokenize(source)))


def bundle_sources(entry: str, sources: Mapping[str, str]) -> NqeBundle:
    """Merge ``entry`` and everything it imports into one query.

    Args:
        entry: Library path of the query to run, as in ``sources``.
        sources: Source for every module in the import closure, keyed by
            library path (``/Dir/Query``, or ``@fwd/...``).

    Raises:
        NqeBundleError: If a module is missing, imports form a cycle, or a
            module uses something this rewrite cannot rename safely.
    """
    entry = module_path(entry)
    normalized = {module_path(path): text for path, text in sources.items()}
    if len(normalized) != len(sources):
        raise NqeBundleError("two sources name the same module")
    order = _dependency_order(entry, normalized)
    modules = {path: _Module.parse(path, normalized[path]) for path in order}
    queries = len(modules[entry].query_annotations)
    if queries != 1:
        raise NqeBundleError(
            f"{entry}: the entry must declare exactly one @query to run, found {queries}"
        )
    prefixes = _prefixes(order, modules)

    exported = {
        path: {name: prefixes[path] + name for name in modules[path].exports} for path in order
    }
    parts = [
        "// Bundled by forward_sdk.nqe.bundle: "
        f"{len(order)} modules, top-level names prefixed per module.",
    ]
    renamed: dict[str, dict[str, str]] = {}
    for path in order:
        module = modules[path]
        scope: dict[str, str] = {}
        for dependency in module.imports:
            scope.update(exported[dependency])
        scope.update({name: prefixes[path] + name for name in module.declarations})
        renamed[path] = {name: scope[name] for name in module.declarations}
        body = module.rewrite(scope, keep_query=path == entry)
        parts.append(f"// ---- module {path}\n{body.strip()}")
    source = "\n\n".join(parts) + "\n"
    return NqeBundle(
        entry=entry,
        source=strip_primary_key(source),
        modules=tuple(order),
        renamed=renamed,
    )


def _dependency_order(entry: str, sources: Mapping[str, str]) -> list[str]:
    ordered: list[str] = []
    done: set[str] = set()

    def visit(path: str, stack: tuple[str, ...]) -> None:
        if path in stack:
            raise NqeBundleError("circular import: " + " -> ".join((*stack, path)))
        if path in done:
            return
        if path not in sources:
            importer = f" (imported by {stack[-1]})" if stack else ""
            raise NqeBundleError(f"no source supplied for module {path}{importer}")
        for dependency in import_paths(sources[path]):
            visit(dependency, (*stack, path))
        done.add(path)
        ordered.append(path)

    visit(entry, ())
    return ordered


def _prefixes(order: Sequence[str], modules: Mapping[str, _Module]) -> dict[str, str]:
    """A distinct, collision-free prefix per module."""
    taken = {
        token.text
        for module in modules.values()
        for token in module.tokens
        if token.kind == "ident"
    }
    prefixes: dict[str, str] = {}
    for index, path in enumerate(order):
        prefix = f"m{index}__"
        while any(name.startswith(prefix) for name in taken):
            prefix = "z" + prefix
        prefixes[path] = prefix
    return prefixes


# --- Lexing -------------------------------------------------------------------


def tokenize(source: str) -> list[Token]:
    """Split NQE source into the tokens the rewrite needs.

    Whitespace and comments are dropped; their text survives because rewriting
    splices the original source by position. Literals are single opaque tokens:
    identifiers inside a pattern or regex are capture names, not references.
    """
    tokens: list[Token] = []
    index = 0
    length = len(source)
    while index < length:
        char = source[index]
        if char in " \t\r\n":
            index += 1
        elif source.startswith("//", index):
            newline = source.find("\n", index)
            index = length if newline < 0 else newline
        elif source.startswith("/*", index):
            index = _find_or_fail(source, "*/", index + 2, "unterminated comment") + 2
        elif char == '"':
            end = _string_end(source, index)
            tokens.append(Token("literal", source[index:end], index, end))
            index = end
        elif source.startswith("```", index):
            end = _pattern_end(source, index + 3, "```")
            tokens.append(Token("literal", source[index:end], index, end))
            index = end
        elif char == "`":
            end = _pattern_end(source, index + 1, "`")
            tokens.append(Token("literal", source[index:end], index, end))
            index = end
        elif char == "@":
            match = ANNOTATION.match(source, index)
            if not match:
                raise NqeBundleError(f"unrecognised annotation at offset {index}")
            tokens.append(Token("annotation", match.group(0), index, match.end()))
            index = match.end()
        elif IDENTIFIER_START.match(char):
            end = IDENTIFIER_BODY.match(source, index + 1).end()  # type: ignore[union-attr]
            if source[index:end] == "re" and end < length and source[end] == "`":
                end = _regex_end(source, end + 1)
                tokens.append(Token("literal", source[index:end], index, end))
            else:
                tokens.append(Token("ident", source[index:end], index, end))
            index = end
        elif char.isdigit() or (char == "." and index + 1 < length and source[index + 1].isdigit()):
            end = NUMBER.match(source, index).end()  # type: ignore[union-attr]
            tokens.append(Token("literal", source[index:end], index, end))
            index = end
        else:
            text = next((p for p in TWO_CHAR_PUNCTUATION if source.startswith(p, index)), char)
            tokens.append(Token("punct", text, index, index + len(text)))
            index += len(text)
    return tokens


def _find_or_fail(source: str, needle: str, start: int, message: str) -> int:
    found = source.find(needle, start)
    if found < 0:
        raise NqeBundleError(f"{message} at offset {start}")
    return found


def _string_end(source: str, start: int) -> int:
    """End of a string literal or a ``\"\"\"`` text block beginning at ``start``."""
    for opener in ('"""csv\n', '"""json\n', '"""\n'):
        if source.startswith(opener, start):
            return (
                _find_or_fail(source, '\n"""', start + len(opener) - 1, "unterminated text block")
                + 4
            )
    index = start + 1
    while index < len(source):
        char = source[index]
        if char == "\\":
            index += 2
            continue
        if char == '"':
            return index + 1
        if char in "\r\n":
            break
        index += 1
    raise NqeBundleError(f"unterminated string at offset {start}")


def _pattern_end(source: str, index: int, closer: str) -> int:
    """End of a backtick pattern, skipping ``{...}`` expressions and their strings."""
    while index < len(source):
        if source.startswith(closer, index):
            return index + len(closer)
        if source[index] == "{":
            index += 1
            while index < len(source) and source[index] != "}":
                index = _string_end(source, index) if source[index] == '"' else index + 1
        index += 1
    raise NqeBundleError("unterminated pattern literal")


def _regex_end(source: str, index: int) -> int:
    """End of a ``re`...``` literal; a backtick in a character class is literal."""
    in_class = False
    while index < len(source):
        char = source[index]
        if char == "\\":
            index += 2
            continue
        if in_class:
            in_class = char != "]"
        elif char == "[":
            in_class = True
        elif char == "`":
            return index + 1
        index += 1
    raise NqeBundleError("unterminated regex literal")


def _imports(tokens: Sequence[Token]) -> list[tuple[str, Token]]:
    """Each ``import "...";`` as its target and the token that starts it."""
    found = []
    for index, token in enumerate(tokens):
        if token.kind == "ident" and token.text == "import" and index + 2 < len(tokens):
            target, semicolon = tokens[index + 1], tokens[index + 2]
            if target.kind == "literal" and target.text.startswith('"') and semicolon.text == ";":
                found.append((target.text[1:-1], token))
    return found


# --- One module ---------------------------------------------------------------


@dataclass(slots=True)
class _Declaration:
    name: Token
    params: set[str]
    body_start: int  # token index
    end: int  # token index of the terminating semicolon


@dataclass(slots=True)
class _Module:
    path: str
    source: str
    tokens: list[Token]
    imports: tuple[str, ...]
    declarations: dict[str, _Declaration]
    exports: frozenset[str]
    query_annotations: list[Token]
    import_spans: list[tuple[int, int]]

    @classmethod
    def parse(cls, path: str, source: str) -> _Module:
        tokens = tokenize(source)
        if any(t.kind == "annotation" and t.text.startswith("@juniper") for t in tokens):
            raise NqeBundleError(f"{path}: module attributes cannot be merged into a bundle")
        import_spans = []
        for _, token in _imports(tokens):
            semicolon = tokens[tokens.index(token) + 2]
            import_spans.append((token.start, semicolon.end))
        declarations, exports, queries = _declarations(path, tokens)
        return cls(
            path=path,
            source=source,
            tokens=tokens,
            imports=import_paths(source),
            declarations=declarations,
            exports=frozenset(exports),
            query_annotations=queries,
            import_spans=import_spans,
        )

    def rewrite(self, scope: Mapping[str, str], *, keep_query: bool) -> str:
        edits: list[tuple[int, int, str]] = [(start, end, "") for start, end in self.import_spans]
        if not keep_query:
            edits.extend((t.start, t.end, "") for t in self.query_annotations)
        for declaration in self.declarations.values():
            edits.append(
                (declaration.name.start, declaration.name.end, scope[declaration.name.text])
            )
            edits.extend(self._references(declaration, scope))
        edits.sort()
        out, cursor = [], 0
        for start, end, text in edits:
            if start < cursor:
                raise NqeBundleError(f"{self.path}: overlapping rewrites at offset {start}")
            out.append(self.source[cursor:start])
            out.append(text)
            cursor = end
        out.append(self.source[cursor:])
        return "".join(out)

    def _references(
        self, declaration: _Declaration, scope: Mapping[str, str]
    ) -> list[tuple[int, int, str]]:
        """Rewrites for every reference in one declaration's body.

        A local name shadows a top-level one from where it is bound to the end
        of the expression binding it: the close of the bracket it was bound in,
        or the next comma or semicolon at that depth (a list element, record
        field, argument or ``when`` case). Parameters shadow the whole body.
        """
        tokens = self.tokens
        edits = []
        bound: list[tuple[str, int]] = [(name, -1) for name in declaration.params]
        stack: list[str] = []
        for index in range(declaration.body_start, declaration.end):
            token = tokens[index]
            text = token.text
            if token.kind == "punct":
                if text in OPENERS:
                    stack.append(text)
                elif text in CLOSERS:
                    if not stack:
                        raise NqeBundleError(
                            f"{self.path}: unbalanced '{text}' at offset {token.start}"
                        )
                    stack.pop()
                    bound = [(n, d) for n, d in bound if d <= len(stack)]
                elif text in {",", ";"}:
                    bound = [(n, d) for n, d in bound if d != len(stack)]
                continue
            if token.kind != "ident":
                continue
            if _binds(tokens, index):
                # A when case's binding sits inside the tag's parentheses, but
                # its scope is the case body that follows them.
                depth = len(stack) - 1 if tokens[index - 1].text == "(" else len(stack)
                bound.append((text, depth))
                continue
            if text not in scope or any(name == text for name, _ in bound):
                continue
            if _is_label(tokens, index, stack):
                continue
            previous = tokens[index - 1].text
            following = tokens[index + 1].text if index + 1 < len(tokens) else ""
            replacement = scope[text]
            shorthand = stack[-1:] == ["{"] and previous in {"{", ","} and following in {",", "}"}
            edits.append(
                (token.start, token.end, f"{text}: {replacement}" if shorthand else replacement)
            )
        return edits


def _is_label(tokens: Sequence[Token], index: int, stack: Sequence[str]) -> bool:
    """Whether the identifier at ``index`` names a field or tag, not a value."""
    previous = tokens[index - 1] if index > 0 else None
    following = tokens[index + 1].text if index + 1 < len(tokens) else ""
    if previous is not None and previous.kind == "punct" and previous.text == ".":
        return True  # field selector
    if stack[-1:] == ["{"] and following == ":":
        return True  # record field name
    if following in SORT_DIRECTIONS and previous is not None and previous.text in {"by", ","}:
        return True  # sort key names a column
    if following == "->":
        return True  # when tag
    return (
        following == "("
        and index + 4 < len(tokens)
        and tokens[index + 3].text == ")"
        and tokens[index + 4].text == "->"
    )  # when tag with a data binding


def _binds(tokens: Sequence[Token], index: int) -> bool:
    """Whether the identifier at ``index`` introduces a local name."""
    previous = tokens[index - 1].text if index > 0 else ""
    following = tokens[index + 1].text if index + 1 < len(tokens) else ""
    if previous == "foreach" and following == "in":
        return True
    if previous == "let" and following == "=":
        return True
    if previous == "as" and following in {
        "by",
        "foreach",
        "where",
        "let",
        "group",
        "select",
        ")",
        "]",
        "}",
        ",",
    }:
        return True
    # when tag(binding) -> ...
    return (
        previous == "("
        and following == ")"
        and index + 2 < len(tokens)
        and tokens[index + 2].text == "->"
        and index >= 2
        and tokens[index - 2].kind == "ident"
    )


def _declarations(
    path: str, tokens: Sequence[Token]
) -> tuple[dict[str, _Declaration], set[str], list[Token]]:
    """Find each top-level declaration: its name, parameters and extent."""
    declarations: dict[str, _Declaration] = {}
    exports: set[str] = set()
    queries: list[Token] = []
    index = 0
    while index < len(tokens) and tokens[index].text == "import":
        index += 3
    while index < len(tokens):
        is_query = tokens[index].kind == "annotation" and tokens[index].text == "@query"
        if is_query:
            queries.append(tokens[index])
            index += 1
        if (
            index < len(tokens)
            and tokens[index].kind == "annotation"
            and tokens[index].text == "@primaryKey"
        ):
            # A main expression: the rest of the module is the query body.
            raise NqeBundleError(
                f"{path}: a module ending in a bare query expression cannot be bundled; "
                "declare the query with @query"
            )
        is_export = index < len(tokens) and tokens[index].text == "export"
        if is_export:
            index += 1
        if index >= len(tokens) or tokens[index].kind != "ident":
            where = tokens[index].start if index < len(tokens) else "end"
            raise NqeBundleError(f"{path}: expected a declaration at offset {where}")
        name = tokens[index]
        if name.text in KEYWORDS:
            raise NqeBundleError(
                f"{path}: expected a declaration at offset {name.start}, found {name.text!r}; "
                "a module ending in a bare query expression cannot be bundled"
            )
        if name.text in declarations:
            raise NqeBundleError(f"{path}: {name.text!r} is declared twice")
        index += 1
        params: set[str] = set()
        if index < len(tokens) and tokens[index].text == "(":
            close = _matching(tokens, index)
            params = _parameter_names(tokens[index + 1 : close])
            index = close + 1
        # Optional return type, then the assignment.
        depth = 0
        while index < len(tokens) and not (depth == 0 and tokens[index].text == "="):
            depth += {"(": 1, "[": 1, "<": 1, ")": -1, "]": -1, ">": -1}.get(tokens[index].text, 0)
            index += 1
        if index >= len(tokens):
            raise NqeBundleError(f"{path}: declaration {name.text!r} has no '='")
        body_start = index + 1
        end = _declaration_end(path, tokens, body_start)
        declarations[name.text] = _Declaration(
            name=name, params=params, body_start=body_start, end=end
        )
        if is_export:
            exports.add(name.text)
        index = end + 1
    return declarations, exports, queries


def _matching(tokens: Sequence[Token], index: int) -> int:
    depth = 0
    for position in range(index, len(tokens)):
        text = tokens[position].text
        if tokens[position].kind != "punct":
            continue
        if text in OPENERS:
            depth += 1
        elif text in CLOSERS:
            depth -= 1
            if depth == 0:
                return position
    raise NqeBundleError(f"unbalanced '{tokens[index].text}' at offset {tokens[index].start}")


def _parameter_names(tokens: Sequence[Token]) -> set[str]:
    """Parameter names from ``a, b: Type, c: List<String>``."""
    names: set[str] = set()
    depth = 0
    expect_name = True
    for token in tokens:
        if token.text in {"(", "[", "<", "{"}:
            depth += 1
        elif token.text in {")", "]", ">", "}"}:
            depth -= 1
        elif token.text == "," and depth == 0:
            expect_name = True
        elif expect_name and depth == 0 and token.kind == "ident":
            names.add(token.text)
            expect_name = False
    return names


def _declaration_end(path: str, tokens: Sequence[Token], index: int) -> int:
    """Token index of the semicolon that ends the declaration starting at ``index``.

    A ``when`` separates its cases with semicolons at the same depth as the one
    that ends the declaration, so a semicolon ends it only when the next token
    cannot begin another case (``tag ->``, ``tag(x) ->`` or ``otherwise ->``).
    """
    depth = 0
    for position in range(index, len(tokens)):
        token = tokens[position]
        if token.kind == "punct" and token.text in OPENERS:
            depth += 1
        elif token.kind == "punct" and token.text in CLOSERS:
            depth -= 1
        elif depth == 0 and token.text == ";" and not _starts_when_case(tokens, position + 1):
            return position
    raise NqeBundleError(f"{path}: declaration at offset {tokens[index - 1].start} never ends")


def _starts_when_case(tokens: Sequence[Token], index: int) -> bool:
    if index + 1 < len(tokens) and tokens[index + 1].text == "->":
        return tokens[index].kind == "ident"
    return (
        index + 4 < len(tokens)
        and tokens[index].kind == "ident"
        and tokens[index + 1].text == "("
        and tokens[index + 3].text == ")"
        and tokens[index + 4].text == "->"
    )
