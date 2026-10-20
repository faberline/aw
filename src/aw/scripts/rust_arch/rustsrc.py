"""Extract modules, use bindings, path references, and items from Rust source."""

from __future__ import annotations

import re
from bisect import bisect_right
from dataclasses import dataclass, field

import tree_sitter_rust
from tree_sitter import Language, Node, Parser, Query, QueryCursor

LANG = Language(tree_sitter_rust.language())
_PARSER = Parser(LANG)
_QUERY = Query(
    LANG,
    """
    (scoped_identifier) @path
    (scoped_type_identifier) @path
    (use_declaration) @use
    (extern_crate_declaration) @extern
    (macro_invocation) @macro
    (token_tree) @tokens
    (attribute_item) @attr
    (mod_item) @mod
    """,
)
LEAVES = frozenset({"identifier", "type_identifier", "crate", "self", "super", "metavariable"})
SCOPED = frozenset({"scoped_identifier", "scoped_type_identifier"})
TOKEN_WORDS = frozenset({"crate", "self", "super"})
_CFG_TEST = re.compile(r"^cfg\((test|all\(.*\btest\b.*\))\)$")
_WS = re.compile(r"\s+")

Segs = tuple[str, ...]


@dataclass
class Binding:
    segs: Segs
    pub: bool
    line: int


@dataclass
class ModDecl:
    name: str
    line: int
    inline: bool
    cfg_test: bool
    path_attr: bool


@dataclass
class Scope:
    uses: dict[str, Binding] = field(default_factory=dict)
    globs: list[Binding] = field(default_factory=list)
    mods: dict[str, ModDecl] = field(default_factory=dict)


@dataclass(frozen=True)
class Ref:
    scope: Segs
    segs: Segs
    line: int
    kind: str


@dataclass
class Item:
    kind: str
    line: int


@dataclass
class SourceFacts:
    scopes: dict[Segs, Scope]
    refs: list[Ref]
    macros: list[Ref]
    items: list[Item]
    path_attr_lines: list[int]


def _text(src: bytes, node: Node | None) -> str:
    # Slice the caller's bytes: Node.text can crash the binding on some trees.
    return src[node.start_byte:node.end_byte].decode("utf-8", "replace") if node is not None else ""


class _Lines:
    """1-based line of a byte offset.

    Node.start_point is avoided on purpose: reading it and then a node's children
    crashes the tree-sitter binding (0.25/0.26) on some trees.
    """

    def __init__(self, src: bytes) -> None:
        self.breaks = [m.start() for m in re.finditer(rb"\n", src)]

    def __call__(self, node: Node) -> int:
        return bisect_right(self.breaks, node.start_byte - 1) + 1


def segments(src: bytes, node: Node | None) -> Segs | None:
    """Path segments of a (possibly scoped or generic) path node, or None."""
    if node is None:
        return None
    kind = node.type
    if kind in LEAVES or kind == "primitive_type":
        return (_text(src, node),)
    if kind == "generic_type":
        return segments(src, node.child_by_field_name("type"))
    if kind in SCOPED:
        prefix = node.child_by_field_name("path")
        name = node.child_by_field_name("name")
        if prefix is None:
            base: Segs | None = ("::",) if _text(src, node).startswith("::") else ()
        else:
            base = segments(src, prefix)
        if base is None or name is None:
            return None
        return base + (_text(src, name),)
    return None


def _use_leaves(src: bytes, node: Node, prefix: Segs):
    """Yield (segments, bound name or None, is_glob) for each leaf of a use tree."""
    kind = node.type
    if kind == "self" and prefix:
        yield prefix, prefix[-1], False
    elif kind in LEAVES:
        yield prefix + (_text(src, node),), _text(src, node), False
    elif kind in SCOPED:
        segs = segments(src, node)
        if segs:
            yield prefix + segs, segs[-1], False
    elif kind == "use_as_clause":
        segs = segments(src, node.child_by_field_name("path"))
        alias = _text(src, node.child_by_field_name("alias"))
        if segs:
            yield prefix + segs, None if alias == "_" else alias, False
    elif kind == "use_wildcard":
        inner = next((c for c in node.named_children if c.type in LEAVES or c.type in SCOPED), None)
        segs = segments(src, inner) if inner is not None else ()
        yield prefix + (segs or ()), None, True
    elif kind == "scoped_use_list":
        path = node.child_by_field_name("path")
        segs = segments(src, path) if path is not None else (("::",) if _text(src, node).startswith("::") else ())
        body = node.child_by_field_name("list")
        for child in body.named_children if body is not None else []:
            yield from _use_leaves(src, child, prefix + (segs or ()))
    elif kind == "use_list":
        for child in node.named_children:
            yield from _use_leaves(src, child, prefix)


def _attached_item(attr: Node) -> Node | None:
    node = attr.next_named_sibling
    while node is not None and node.type in ("attribute_item", "line_comment", "block_comment"):
        node = node.next_named_sibling
    return node


def _attr_body(src: bytes, attr: Node) -> str:
    inner = next((c for c in attr.named_children if c.type == "attribute"), None)
    return _WS.sub("", _text(src, inner))


def _token_paths(src: bytes, tree: Node):
    """Reconstruct ``ident(::ident)+`` sequences from one token tree's direct tokens."""
    toks = [(c.type, _text(src, c), c) for c in tree.children]
    i = 0
    while i < len(toks):
        kind, text, node = toks[i]
        wordish = kind in ("identifier", "metavariable") or text in TOKEN_WORDS
        global_start = text == "::" and i + 1 < len(toks) and toks[i + 1][0] == "identifier"
        if not (wordish or global_start):
            i += 1
            continue
        segs = ["::", toks[i + 1][1]] if global_start else [text]
        j = i + 2 if global_start else i + 1
        while (
            j + 1 < len(toks)
            and toks[j][1] == "::"
            and (toks[j + 1][0] in ("identifier", "metavariable") or toks[j + 1][1] in TOKEN_WORDS)
        ):
            segs.append(toks[j + 1][1])
            j += 2
        if len(segs) >= 2:
            yield tuple(segs), node
        i = j


def _item_kind(src: bytes, node: Node) -> str | None:
    kind = node.type
    if kind in ("attribute_item", "line_comment", "block_comment", "ERROR"):
        return None
    if kind == "mod_item":
        return "inline mod" if node.child_by_field_name("body") is not None else "mod"
    if kind == "use_declaration":
        pub = any(c.type == "visibility_modifier" for c in node.children)
        return "pub use" if pub else "use"
    if kind == "inner_attribute_item":
        return "#![..]"
    if kind == "extern_crate_declaration":
        return "extern crate"
    if kind == "function_item":
        return f"fn {_text(src, node.child_by_field_name('name'))}"
    return kind


class _Ranges:
    """Merged half-open byte ranges with O(log n) membership."""

    def __init__(self, ranges: list[tuple[int, int]]) -> None:
        merged: list[list[int]] = []
        for start, end in sorted(ranges):
            if merged and start <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([start, end])
        self.starts = [m[0] for m in merged]
        self.ends = [m[1] for m in merged]

    def __contains__(self, byte: int) -> bool:
        index = bisect_right(self.starts, byte) - 1
        return index >= 0 and byte < self.ends[index]


class _Spans:
    """Byte-range lookups for cfg(test) items and inline module scopes."""

    def __init__(self) -> None:
        self.tests: list[tuple[int, int]] = []
        self.mods: list[tuple[int, int, Segs]] = []
        self._tests = _Ranges([])

    def freeze(self) -> None:
        self._tests = _Ranges(self.tests)

    def in_test(self, byte: int) -> bool:
        return byte in self._tests

    def scope(self, byte: int) -> Segs:
        best: tuple[int, Segs] = (-1, ())
        for start, end, path in self.mods:
            if start < byte < end and start > best[0]:
                best = (start, path)
        return best[1]


def parse(src: bytes) -> SourceFacts:
    tree = _PARSER.parse(src)
    root = tree.root_node
    _line = _Lines(src)
    caps = QueryCursor(_QUERY).captures(root)
    spans = _Spans()
    path_attr_lines: list[int] = []
    path_attr_items: set[int] = set()
    for attr in caps.get("attr", []):
        body = _attr_body(src, attr)
        item = _attached_item(attr)
        if item is not None and _CFG_TEST.match(body):
            spans.tests.append((item.start_byte, item.end_byte))
        if body.startswith("path="):
            path_attr_lines.append(_line(attr))
            if item is not None:
                path_attr_items.add(item.start_byte)
    spans.freeze()
    inline = sorted(
        (m for m in caps.get("mod", []) if m.child_by_field_name("body") is not None),
        key=lambda m: m.start_byte,
    )
    stack: list[tuple[int, Segs]] = []
    for mod in inline:
        while stack and stack[-1][0] <= mod.start_byte:
            stack.pop()
        path = (stack[-1][1] if stack else ()) + (_text(src, mod.child_by_field_name("name")),)
        stack.append((mod.end_byte, path))
        spans.mods.append((mod.start_byte, mod.end_byte, path))

    scopes: dict[Segs, Scope] = {(): Scope()}
    for _, _, path in spans.mods:
        scopes.setdefault(path, Scope())
    for mod in caps.get("mod", []):
        scope = scopes.setdefault(spans.scope(mod.start_byte), Scope())
        name = _text(src, mod.child_by_field_name("name"))
        scope.mods[name] = ModDecl(
            name=name,
            line=_line(mod),
            inline=mod.child_by_field_name("body") is not None,
            cfg_test=spans.in_test(mod.start_byte),
            path_attr=mod.start_byte in path_attr_items,
        )

    refs: list[Ref] = []
    use_spans: list[tuple[int, int]] = []
    for use in caps.get("use", []):
        use_spans.append((use.start_byte, use.end_byte))
        scope_path = spans.scope(use.start_byte)
        scope = scopes.setdefault(scope_path, Scope())
        pub = any(c.type == "visibility_modifier" for c in use.children)
        testing = spans.in_test(use.start_byte)
        arg = use.child_by_field_name("argument")
        for segs, name, glob in _use_leaves(src, arg, ()) if arg is not None else []:
            binding = Binding(segs, pub, _line(use))
            if glob:
                scope.globs.append(binding)
            elif name and name != "self":
                scope.uses.setdefault(name, binding)
            if not testing:
                refs.append(Ref(scope_path, segs, _line(use), "use"))
    for ext in caps.get("extern", []):
        name = _text(src, ext.child_by_field_name("name"))
        alias = _text(src, ext.child_by_field_name("alias")) or name
        scope_path = spans.scope(ext.start_byte)
        pub = any(c.type == "visibility_modifier" for c in ext.children)
        if name != "self" and alias != "_":
            scopes.setdefault(scope_path, Scope()).uses[alias] = Binding(("::", name), pub, _line(ext))

    in_use = _Ranges(use_spans)

    def keep(node: Node) -> bool:
        return not spans.in_test(node.start_byte) and node.start_byte not in in_use

    for node in caps.get("path", []):
        parent = node.parent
        if parent is not None and parent.type in SCOPED:
            head = parent.child_by_field_name("path")
            if head is not None and head.start_byte == node.start_byte and head.end_byte == node.end_byte:
                continue
        if not keep(node):
            continue
        segs = segments(src, node)
        if segs:
            refs.append(Ref(spans.scope(node.start_byte), segs, _line(node), "path"))
    for group in caps.get("tokens", []):
        if not keep(group):
            continue
        scope_path = spans.scope(group.start_byte)
        for segs, node in _token_paths(src, group):
            refs.append(Ref(scope_path, segs, _line(node), "token"))
    for attr in caps.get("attr", []):
        inner = next((c for c in attr.named_children if c.type == "attribute"), None)
        head = inner.named_children[0] if inner is not None and inner.named_children else None
        if head is not None and head.type == "identifier" and keep(attr):
            refs.append(Ref(spans.scope(attr.start_byte), (_text(src, head),), _line(attr), "attr"))
    macros: list[Ref] = []
    for mac in caps.get("macro", []):
        segs = segments(src, mac.child_by_field_name("macro"))
        if segs and keep(mac):
            macros.append(Ref(spans.scope(mac.start_byte), segs, _line(mac), "macro"))

    items = []
    for child in root.named_children:
        kind = _item_kind(src, child)
        if kind == "inline mod" and spans.in_test(child.start_byte):
            kind = "#[cfg(test)] mod"
        if kind is not None:
            items.append(Item(kind, _line(child)))
    return SourceFacts(scopes, refs, macros, items, path_attr_lines)
