"""Structure rules: context and layer layout (B1), root files (B5), and binaries (B6)."""

from __future__ import annotations

from .cargo import Package, under
from .config import Config
from .model import Findings
from .modtree import CrateRole, Index, in_root
from .rustsrc import Segs

# Comments (//!, ///) and attributes on these items are not items, so they always pass.
LIB_ALLOWED = frozenset({"mod", "use", "pub use", "#[cfg(test)] mod", "#![..]", "extern crate"})
BIN_ALLOWED = frozenset({"mod", "use", "pub use", "#[cfg(test)] mod", "#![..]", "extern crate", "fn main"})
LIB_WHAT = "只能有 mod、use、pub use、#[cfg(test)] mod、#![..]"


def _items(index: Index, path: str, allowed: frozenset[str], rule: str, what: str, out: Findings,
           warn: bool = False) -> None:
    emit = out.warn if warn else out.error
    for item in index.facts(path).items:
        if item.kind not in allowed:
            emit(rule, path, item.line, item.kind, f"{what}，不能有 `{item.kind}`")


def check_roots(index: Index, out: Findings) -> None:
    """B5 on every lib.rs, B6 on every binary entry file.

    The entries are src/main.rs, src/bin/<name>.rs, and src/bin/<name>/main.rs.
    Other modules under src/bin/<name>/ belong to that binary and are not B6.
    """
    bin_what = "main.rs／bin 只能有 use、mod 宣告、#[cfg(test)] mod 和 fn main"
    for pkg in index.cargo.packages.values():
        if pkg.lib:
            _items(index, pkg.lib, LIB_ALLOWED, "B5", f"lib.rs {LIB_WHAT}", out, warn=True)
        for path in pkg.bins:
            _items(index, path, BIN_ALLOWED, "B6", bin_what, out)


def check_undeclared(index: Index, out: Findings) -> None:
    """Without ddd.toml, one B1 summary per workspace member."""
    for pkg in index.cargo.member_packages():
        if not pkg.lib and not pkg.bins:
            continue
        count = len(index.children(pkg, ()))
        out.error("B1", pkg.src_dir or ".", None, pkg.name,
                  f"尚未宣告 contexts（沒有 ddd.toml）：`{pkg.name}` 的 src 頂層有 {count} 個模組")


def _is_empty(index: Index, path: str) -> bool:
    text = (index.repo / path).read_text(encoding="utf-8", errors="replace")
    return not any(line.strip() and not line.strip().startswith("//") for line in text.splitlines())


class _Layout:
    def __init__(self, index: Index, cfg: Config, policy: dict, repo: str, out: Findings) -> None:
        self.index, self.cfg, self.policy, self.repo, self.out = index, cfg, policy, repo, out
        self.roles = index.crate_roles(policy["domain_layer"])

    def roots_exist(self) -> None:
        for root, role in self.index.roots:
            pkg = self.index.cargo.owner(root)
            label = role.label()
            if pkg is None or not under(root, pkg.src_dir):
                self.out.error("B1", "ddd.toml", None, label, f"`{label}` 的模組根 `{root}` 不在任何 Cargo package 的 src 底下")
                continue
            files = [f for f in self.index.files if in_root(f, root)]
            if not files:
                self.out.error("B1", "ddd.toml", None, label, f"宣告的 `{label}` 模組根 `{root}` 不存在")
            elif all(_is_empty(self.index, f) for f in files):
                self.out.error("B1", "ddd.toml", None, label, f"宣告的 `{label}` 模組根 `{root}` 是空的")

    def crates(self) -> None:
        for pkg in self.index.cargo.member_packages():
            role = self.roles.get(pkg.name, CrateRole("none"))
            if role.kind == "none":
                self.out.error("B1", pkg.manifest_path, None, pkg.name,
                               f"crate `{pkg.name}` 沒有歸屬：不屬於任何 context、shared kernel 或 assembly")
                continue
            if role.kind == "multi" and self.policy["one_context_per_crate"]:
                names = "、".join(sorted(role.contexts))
                self.out.error("B1", pkg.manifest_path, None, pkg.name,
                               f"crate `{pkg.name}` 橫跨多個 context（{names}）；一個 crate 只能屬於一個 context")
            if role.kind == "assembly":
                continue
            self.path_attrs(pkg)
            declared = [r for r, _ in self.index.roots if under(r, pkg.src_dir)]
            if pkg.src_dir not in declared:
                self.walk(pkg, (), declared)

    def path_attrs(self, pkg: Package) -> None:
        for path in self.index.modules(pkg).values():
            for line in self.index.facts(path).path_attr_lines:
                self.out.error("B1", path, line, "#[path]", "不能用 #[path]，模組位置必須跟檔案路徑一致")

    def walk(self, pkg: Package, module: Segs, declared: list[str]) -> None:
        base = "/".join((pkg.src_dir, *module)) if pkg.src_dir else "/".join(module)
        container = self.index.modules(pkg).get(module)
        tests: set[str] = set()
        if container is not None:
            scope = self.index.facts(container).scopes[()]
            tests = {name for name, decl in scope.mods.items() if decl.cfg_test}
            if module:
                _items(self.index, container, LIB_ALLOWED, "B5", f"context 根檔{LIB_WHAT}", self.out, warn=True)
        for name, file in self.index.children(pkg, module).items():
            child = f"{base}/{name}" if base else name
            if child in declared:
                continue
            if any(root.startswith(child + "/") for root in declared):
                self.walk(pkg, module + (name,), declared)
            elif name not in tests:
                where = "未宣告的頂層模組" if not module else "未宣告的層或模組"
                self.out.error("B1", file, None, child,
                               f"{where} `{child}`：不在任何宣告的 context 層、shared kernel 或 assembly 模組裡")

    def naming(self) -> None:
        naming = self.policy["naming"]
        split = {r.context for r in self.roles.values() if r.kind == "domain"}
        for pkg in self.index.cargo.member_packages():
            role = self.roles.get(pkg.name, CrateRole("none"))
            expected = None
            if role.kind == "domain" and role.context in split:
                expected = naming["domain_crate"].format(repo=self.repo, context=role.context)
            elif role.kind == "context" and role.context in split:
                expected = naming["context_crate"].format(repo=self.repo, context=role.context)
            elif role.kind == "kernel" and role.whole and split:
                expected = naming["shared_kernel_crate"].format(repo=self.repo)
            if expected and pkg.name != expected:
                self.out.error("B1", pkg.manifest_path, None, pkg.name,
                               f"拆 crate 的命名應為 `{expected}`，目前是 `{pkg.name}`")


def check_declared(index: Index, cfg: Config, policy: dict, repo: str, out: Findings) -> None:
    layout = _Layout(index, cfg, policy, repo, out)
    layout.roots_exist()
    layout.crates()
    layout.naming()
