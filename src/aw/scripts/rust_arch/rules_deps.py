"""Dependency rules: domain purity (B2), layer direction (B3), cross-context use (B4)."""

from __future__ import annotations

from dataclasses import dataclass

from .cargo import Package
from .config import Config
from .model import Findings
from .modtree import STD, CrateRole, Index, Role, Target, in_root


@dataclass
class _Rules:
    index: Index
    cfg: Config
    policy: dict
    out: Findings

    def __post_init__(self) -> None:
        domain = self.policy["domain"]
        self.domain_layer = self.policy["domain_layer"]
        self.allowed = {c.replace("-", "_") for c in domain["allowed_crates"]}
        self.denied_paths = list(domain["denied_paths"])
        self.denied_macros = set(domain["denied_macros"])
        self.kernel_pure = bool(domain.get("shared_kernel_pure"))
        self.layer_deps = self.policy["layer_deps"]
        self.public_layers = set(self.policy["public_layers"])
        self.events = self.policy.get("domain_events")
        self.roles = self.index.crate_roles(self.domain_layer)

    def pure(self, kind: str, layer: str | None = None) -> bool:
        return layer == self.domain_layer or kind == "domain" or (kind == "kernel" and self.kernel_pure)

    # Cargo manifests -----------------------------------------------------

    def cargo(self) -> None:
        for pkg in self.index.cargo.member_packages():
            role = self.roles.get(pkg.name, CrateRole("none"))
            for dep in pkg.deps:
                if dep.kind == "dev":
                    continue
                member = self.index.cargo.internal(dep)
                if member is None:
                    if (dep.kind == "normal" and self.pure(role.kind) and role.whole
                            and dep.crate not in self.allowed):
                        self.out.error(
                            "B2", pkg.manifest_path, dep.line, dep.crate,
                            f"{role.kind} crate `{pkg.name}` 依賴了不在允許清單的 crate `{dep.crate}`",
                        )
                    continue
                if dep.kind == "normal":
                    self.edge(pkg, role, member, dep.line)
            if self.pure(role.kind) and role.whole and (pkg.build or any(d.kind == "build" for d in pkg.deps)):
                self.out.error(
                    "B2", pkg.manifest_path, None, "build.rs",
                    f"{role.kind} crate `{pkg.name}` 不能有 build.rs 或 [build-dependencies]",
                )

    def edge(self, src: Package, role: CrateRole, dst: Package, line: int | None) -> None:
        target = self.roles.get(dst.name, CrateRole("none"))
        if role.kind in ("assembly", "multi", "none"):
            return
        if role.kind == "kernel":
            self.out.error("B4", src.manifest_path, line, dst.name,
                           f"shared kernel `{src.name}` 不能依賴 workspace 內的 crate `{dst.name}`")
            return
        if target.kind == "kernel":
            return
        if target.kind == "assembly":
            self.out.error("B4", src.manifest_path, line, dst.name,
                           f"`{src.name}` 不能依賴 assembly crate `{dst.name}`")
            return
        if target.context is None:
            return
        if role.kind == "domain":
            rule = "B3" if target.context == role.context else "B4"
            self.out.error(rule, src.manifest_path, line, dst.name,
                           f"domain crate `{src.name}` 只能依賴 shared kernel，不能依賴 `{dst.name}`")
            return
        if target.context == role.context:
            return
        ctx = self.cfg.context(role.context or "")
        if ctx is None or target.context not in ctx.depends_on:
            self.out.error("B4", src.manifest_path, line, dst.name,
                           f"context `{role.context}` 沒有在 depends_on 宣告 `{target.context}`，不能依賴 `{dst.name}`")

    # Source references ---------------------------------------------------

    def sources(self) -> None:
        for pkg, path, role in self.index.source_files():
            crate_role = self.roles.get(pkg.name, CrateRole("none"))
            pure = self.pure(role.kind, role.layer)
            facts = self.index.facts(path)
            module = self.index.module_of(pkg, path)
            where = "shared kernel" if role.kind == "kernel" else role.label()
            for ref in facts.refs:
                target = self.index.resolve(pkg, module, facts, ref.scope, ref.segs)
                if target is None:
                    continue
                if not target.internal:
                    if pure:
                        self.purity(path, ref.line, target, where, check_crate=not crate_role.whole)
                    continue
                landed = self.index.land(target)
                if landed is not None:
                    self.direction(path, ref.line, role, landed[1])
            if not pure:
                continue
            for mac in facts.macros:
                name = mac.segs[-1]
                if name not in self.denied_macros:
                    continue
                if len(mac.segs) > 1:
                    target = self.index.resolve(pkg, module, facts, mac.scope, mac.segs)
                    if target is None or target.internal or target.crate not in STD:
                        continue
                self.out.error("B2", path, mac.line, f"{name}!", f"{where} 不能用 `{name}!`")

    def purity(self, path: str, line: int, target: Target, where: str, check_crate: bool) -> None:
        dotted = target.dotted()
        for denied in self.denied_paths:
            if dotted == denied or dotted.startswith(denied + "::"):
                self.out.error("B2", path, line, denied, f"{where} 不能用 `{denied}`")
                break
        if check_crate and target.crate not in self.allowed:
            self.out.error("B2", path, line, target.crate,
                           f"{where} 用了不在允許清單的外部 crate `{target.crate}`")

    def direction(self, path: str, line: int, src: Role, dst_file: str) -> None:
        dst = self.index.role(dst_file)
        if dst is None or dst.kind != "layer":
            return
        if src.kind == "kernel":
            self.out.error("B4", path, line, dst.label(), f"shared kernel 不能依賴 context `{dst.label()}`")
            return
        if dst.context == src.context:
            if dst.layer != src.layer and dst.layer not in self.layer_deps.get(src.layer, []):
                self.out.error("B3", path, line, f"{src.layer}->{dst.layer}",
                               f"`{src.label()}` 不能用同一個 context 的 {dst.layer} 層")
            return
        if src.layer == self.domain_layer:
            self.out.error("B4", path, line, dst.label(),
                           f"domain 不能依賴其他 context（`{dst.label()}`）")
            return
        ctx = self.cfg.context(src.context or "")
        other = self.cfg.context(dst.context or "")
        if ctx is None or other is None or dst.context not in ctx.depends_on:
            self.out.error("B4", path, line, dst.label(),
                           f"context `{src.context}` 沒有在 depends_on 宣告 `{dst.context}`")
            return
        if not self.public_layers & set(other.layers) or dst.layer in self.public_layers:
            return
        domain_root = other.layers.get(self.domain_layer)
        if (dst.layer == self.domain_layer and self.events and domain_root is not None
                and in_root(dst_file, f"{domain_root}/{self.events}")):
            return
        allowed = "、".join(sorted(self.public_layers))
        self.out.error("B4", path, line, dst.label(),
                       f"跨 context 只能用 {allowed} 或 domain::{self.events}，不能用 `{dst.label()}`")


def check(index: Index, cfg: Config, policy: dict, out: Findings) -> None:
    rules = _Rules(index, cfg, policy, out)
    rules.cargo()
    rules.sources()
