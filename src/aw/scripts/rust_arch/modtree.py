"""Module trees, path resolution, and layer roles for one repo."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import rustsrc
from .cargo import Cargo, Dep, Package, under
from .config import Config
from .rustsrc import Segs, SourceFacts

STD = frozenset({"std", "core", "alloc", "proc_macro", "test"})
MAX_HOPS = 12


@dataclass(frozen=True)
class Target:
    internal: bool
    crate: str
    segs: Segs

    def dotted(self) -> str:
        return "::".join((self.crate, *self.segs))


@dataclass(frozen=True)
class Role:
    kind: str
    context: str | None = None
    layer: str | None = None

    def label(self) -> str:
        if self.kind == "layer":
            return f"{self.context}.{self.layer}"
        return self.kind


@dataclass(frozen=True)
class CrateRole:
    """What a workspace member holds: assembly | kernel | domain | context | multi | none."""

    kind: str
    contexts: frozenset[str] = frozenset()
    whole: bool = False

    @property
    def context(self) -> str | None:
        return next(iter(self.contexts)) if len(self.contexts) == 1 else None


def in_root(path: str, root: str) -> bool:
    return path == root + ".rs" or (root != "" and path.startswith(root + "/"))


class Index:
    def __init__(self, repo: Path, files: list[str], cargo: Cargo, cfg: Config | None) -> None:
        self.repo = repo
        self.files = [f for f in files if f.endswith(".rs")]
        self.cargo = cargo
        self.cfg = cfg
        self._facts: dict[str, SourceFacts] = {}
        self._modules: dict[str, dict[Segs, str]] = {}
        self._deps: dict[str, dict[str, Dep]] = {}
        self._tests: dict[str, bool] = {}
        self.bins = {b for pkg in cargo.packages.values() for b in pkg.bins}
        self.roots: list[tuple[str, Role]] = []
        if cfg is not None:
            for ctx in cfg.contexts:
                for layer, path in ctx.layers.items():
                    self.roots.append((path, Role("layer", ctx.name, layer)))
            if cfg.shared_kernel:
                self.roots.append((cfg.shared_kernel, Role("kernel")))
            for path in cfg.assembly_modules:
                self.roots.append((path, Role("assembly")))
            self.roots.sort(key=lambda item: -len(item[0]))
        self.assembly_dirs = set()
        for entry in cfg.assembly_crates if cfg else []:
            pkg = cargo.packages.get(entry) or cargo.by_dir(entry.rstrip("/"))
            if pkg is not None:
                self.assembly_dirs.add(pkg.dir)

    def facts(self, path: str) -> SourceFacts:
        facts = self._facts.get(path)
        if facts is None:
            facts = rustsrc.parse((self.repo / path).read_bytes())
            self._facts[path] = facts
        return facts

    def modules(self, pkg: Package) -> dict[Segs, str]:
        cached = self._modules.get(pkg.name)
        if cached is not None:
            return cached
        prefix = pkg.src_dir + "/" if pkg.src_dir else ""
        out: dict[Segs, str] = {}
        for path in self.files:
            if not path.startswith(prefix) or path.startswith(prefix + "bin/"):
                continue
            if self.cargo.owner(path) is not pkg:
                continue
            parts = path[len(prefix):-3].split("/")
            if path == pkg.lib or parts in (["lib"], ["main"]):
                key: Segs = ()
            elif parts[-1] == "mod":
                key = tuple(parts[:-1])
            else:
                key = tuple(parts)
            if key == () and path == pkg.lib:
                out[()] = path
            else:
                out.setdefault(key, path)
        self._modules[pkg.name] = out
        return out

    def module_of(self, pkg: Package, path: str) -> Segs:
        for key, file in self.modules(pkg).items():
            if file == path:
                return key
        return ()

    def children(self, pkg: Package, module: Segs) -> dict[str, str]:
        """Child module name → one file of that child, from the file layout."""
        out: dict[str, str] = {}
        for key, file in sorted(self.modules(pkg).items()):
            if len(key) > len(module) and key[: len(module)] == module:
                out.setdefault(key[len(module)], file)
        return out

    def deps(self, pkg: Package) -> dict[str, Dep]:
        cached = self._deps.get(pkg.name)
        if cached is None:
            cached = {d.code_name: d for d in pkg.deps if d.kind != "build"}
            self._deps[pkg.name] = cached
        return cached

    def is_test_file(self, pkg: Package, path: str) -> bool:
        cached = self._tests.get(path)
        if cached is not None:
            return cached
        module = self.module_of(pkg, path)
        modules = self.modules(pkg)
        result = False
        for depth in range(len(module)):
            parent = modules.get(module[:depth])
            decl = self.facts(parent).scopes[()].mods.get(module[depth]) if parent else None
            if decl is not None and decl.cfg_test:
                result = True
                break
        self._tests[path] = result
        return result

    def role(self, path: str) -> Role | None:
        if path in self.bins:
            return Role("assembly")
        for root, role in self.roots:
            if in_root(path, root):
                return role
        pkg = self.cargo.owner(path)
        if pkg is not None and pkg.dir in self.assembly_dirs:
            return Role("assembly")
        return None

    def external(self, pkg: Package, segs: Segs) -> Target | None:
        if not segs:
            return None
        name = segs[0]
        if name in STD:
            return Target(False, name, segs[1:])
        dep = self.deps(pkg).get(name)
        if dep is not None:
            member = self.cargo.internal(dep)
            if member is not None:
                return Target(True, member.name, segs[1:])
            return Target(False, dep.crate, segs[1:])
        if name == pkg.crate:
            return Target(True, pkg.name, segs[1:])
        return None

    def resolve(
        self, pkg: Package, module: Segs, facts: SourceFacts, rel: Segs, segs: Segs, hops: int = 0
    ) -> Target | None:
        if hops > MAX_HOPS or not segs:
            return None
        head, here = segs[0], module + rel
        if head in ("crate", "$crate"):
            return Target(True, pkg.name, segs[1:])
        if head == "self":
            return Target(True, pkg.name, here + segs[1:])
        if head == "super":
            index = 0
            while index < len(segs) and segs[index] == "super":
                index += 1
            return Target(True, pkg.name, here[: max(len(here) - index, 0)] + segs[index:])
        if head == "::":
            return self.external(pkg, segs[1:])
        scope = facts.scopes.get(rel)
        if scope is not None:
            binding = scope.uses.get(head)
            if binding is not None and binding.segs[:1] != (head,):
                return self.resolve(pkg, module, facts, rel, binding.segs + segs[1:], hops + 1)
            if binding is not None:
                segs = binding.segs + segs[1:]
            if head in scope.mods:
                return Target(True, pkg.name, here + segs)
        return self.external(pkg, segs)

    def land(self, target: Target, hops: int = 0) -> tuple[Package, str, Segs] | None:
        """Follow ``target`` to the file that defines it, through pub use re-exports."""
        pkg = self.cargo.packages.get(target.crate)
        if pkg is None or not target.internal:
            return None
        modules, segs = self.modules(pkg), target.segs
        cut = len(segs)
        while cut >= 0 and segs[:cut] not in modules:
            cut -= 1
        if cut < 0:
            return None
        file = modules[segs[:cut]]
        facts = self.facts(file)
        rel: Segs = ()
        index = cut
        while index < len(segs) and rel + (segs[index],) in facts.scopes:
            rel += (segs[index],)
            index += 1
        rest = segs[index:]
        if rest and hops < MAX_HOPS:
            scope = facts.scopes.get(rel)
            binding = scope.uses.get(rest[0]) if scope is not None else None
            if binding is not None and binding.pub:
                nxt = self.resolve(pkg, segs[:cut], facts, rel, binding.segs + rest[1:], hops + 1)
                if nxt is not None and nxt.internal:
                    return self.land(nxt, hops + 1)
                return None
        return pkg, file, rest

    def crate_roles(self, domain_layer: str) -> dict[str, CrateRole]:
        out: dict[str, CrateRole] = {}
        for pkg in self.cargo.member_packages():
            if pkg.dir in self.assembly_dirs:
                out[pkg.name] = CrateRole("assembly")
                continue
            held = [(r, role) for r, role in self.roots if self.cargo.owner(r) is pkg]
            contexts = frozenset(role.context for _, role in held if role.kind == "layer")
            whole = [role for r, role in held if r == pkg.src_dir]
            if not held:
                kind = "none"
            elif len(contexts) > 1:
                kind = "multi"
            elif contexts:
                only_domain = {role.layer for _, role in held} == {domain_layer}
                kind = "domain" if only_domain and whole else "context"
            elif whole and whole[0].kind == "kernel":
                kind = "kernel"
            else:
                kind = "kernel" if any(role.kind == "kernel" for _, role in held) else "assembly"
            out[pkg.name] = CrateRole(kind, contexts, bool(whole))
        return out

    def source_files(self) -> list[tuple[Package, str, Role]]:
        """Non-test source files that sit in a context layer or the shared kernel."""
        out = []
        for path in self.files:
            role = self.role(path)
            if role is None or role.kind == "assembly":
                continue
            pkg = self.cargo.owner(path)
            if pkg is None or not under(path, pkg.src_dir) or self.is_test_file(pkg, path):
                continue
            out.append((pkg, path, role))
        return out
