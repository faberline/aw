"""Load and validate a repo's ddd.toml (rules P1 and P2)."""

from __future__ import annotations

import posixpath
from dataclasses import dataclass, field
from pathlib import Path

import tomllib

from .model import SUPPRESSIBLE, Findings, ToolError

CONFIG_NAME = "ddd.toml"
SCHEMA = 1
KNOWN_KEYS = {
    "": {"schema", "policy", "migration", "shared_kernel", "assembly", "contexts",
         "large_files", "exceptions", "renames"},
    "migration": {"enforce"},
    "shared_kernel": {"path"},
    "assembly": {"crates", "modules"},
    "contexts": {"name", "depends_on", "layers"},
    "large_files": {"path", "max_lines", "reason"},
    "exceptions": {"rule", "paths", "subject", "reason"},
    "renames": {"from", "to"},
}
_MISSING = object()


@dataclass
class Context:
    name: str
    depends_on: list[str]
    layers: dict[str, str]


@dataclass
class Config:
    enforce: bool = False
    policy: dict = field(default_factory=dict)
    shared_kernel: str | None = None
    assembly_crates: list[str] = field(default_factory=list)
    assembly_modules: list[str] = field(default_factory=list)
    contexts: list[Context] = field(default_factory=list)
    large_files: dict[str, dict] = field(default_factory=dict)
    exceptions: list[dict] = field(default_factory=list)
    renames: list[dict] = field(default_factory=list)

    def context(self, name: str) -> Context | None:
        return next((c for c in self.contexts if c.name == name), None)


def module_root(path: str) -> str:
    """Normalise a declared module root: no trailing slash, no .rs suffix."""
    path = path.strip().rstrip("/")
    path = path.removesuffix(".rs")
    normal = posixpath.normpath(path)
    return "" if normal == "." else normal


def read_text(text: str, where: str) -> dict:
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ToolError(f"{where}: TOML parse error: {exc}") from exc


def read(repo: Path) -> dict | None:
    path = repo / CONFIG_NAME
    if not path.is_file():
        return None
    return read_text(path.read_text(encoding="utf-8"), f"{repo.name}/{CONFIG_NAME}")


def policy_diff(left: object, right: object, prefix: str = "") -> list[str]:
    if isinstance(left, dict) and isinstance(right, dict):
        out: list[str] = []
        for key in sorted(set(left) | set(right)):
            out += policy_diff(left.get(key, _MISSING), right.get(key, _MISSING), f"{prefix}{key}.")
        return out
    return [] if left == right else [prefix.rstrip(".")]


def _is_glob(path: object) -> bool:
    return isinstance(path, str) and any(ch in path for ch in "*?[")


class _Validator:
    def __init__(self, out: Findings) -> None:
        self.out = out

    def p2(self, subject: str, message: str) -> None:
        self.out.error("P2", CONFIG_NAME, None, subject, message)

    def keys(self, table: object, section: str, where: str) -> dict:
        if not isinstance(table, dict):
            self.p2(where, f"`{where}` 必須是 table")
            return {}
        for key in sorted(set(table) - KNOWN_KEYS[section]):
            self.p2(f"{where}.{key}", f"未知的欄位 `{where}.{key}`")
        return table

    def path(self, value: object, where: str) -> str | None:
        if not isinstance(value, str) or not value.strip():
            self.p2(where, f"`{where}` 必須是非空字串")
            return None
        if _is_glob(value):
            self.p2(where, f"`{where}` 不接受 glob：{value}")
            return None
        return value.strip()

    def reason(self, value: object, where: str) -> None:
        if not isinstance(value, str) or not value.strip() or "TODO" in value.upper():
            self.p2(where, f"`{where}` 必須寫明原因（不能是空字串或 TODO）")

    def str_list(self, value: object, where: str, allow_empty: bool = True) -> list[str]:
        if value is None:
            value = []
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            self.p2(where, f"`{where}` 必須是字串陣列")
            return []
        if not value and not allow_empty:
            self.p2(where, f"`{where}` 不能是空陣列")
        return value


def _cycles(contexts: list[Context]) -> list[list[str]]:
    graph = {c.name: [d for d in c.depends_on] for c in contexts}
    state: dict[str, int] = {}
    found: list[list[str]] = []

    def visit(node: str, trail: list[str]) -> None:
        state[node] = 1
        for nxt in graph.get(node, []):
            if state.get(nxt) == 1:
                found.append(trail[trail.index(nxt):] + [nxt] if nxt in trail else [node, nxt])
            elif nxt in graph and state.get(nxt) is None:
                visit(nxt, trail + [nxt])
        state[node] = 2

    for name in graph:
        if state.get(name) is None:
            visit(name, [name])
    return found


def parse(raw: dict, canonical: dict, out: Findings) -> Config:
    check = _Validator(out)
    raw = check.keys(raw, "", "ddd.toml")
    cfg = Config()
    if "schema" in raw and raw["schema"] != SCHEMA:
        check.p2("schema", f"schema 必須是 {SCHEMA}")
    policy = raw.get("policy")
    if not isinstance(policy, dict):
        out.error("P1", CONFIG_NAME, None, "policy", "缺少 [policy]；請逐字複製 canonical policy.toml")
    else:
        cfg.policy = policy
        for key in policy_diff(policy, canonical):
            out.error("P1", CONFIG_NAME, None, key, f"[policy] 的 `{key}` 跟 canonical policy.toml 不一致")
    migration = check.keys(raw.get("migration", {}), "migration", "migration")
    enforce = migration.get("enforce", False)
    if not isinstance(enforce, bool):
        check.p2("migration.enforce", "`migration.enforce` 必須是 true 或 false")
    cfg.enforce = enforce is True
    if "shared_kernel" in raw:
        kernel = check.keys(raw["shared_kernel"], "shared_kernel", "shared_kernel")
        path = check.path(kernel.get("path"), "shared_kernel.path")
        cfg.shared_kernel = module_root(path) if path else None
    assembly = check.keys(raw.get("assembly", {}), "assembly", "assembly")
    cfg.assembly_crates = check.str_list(assembly.get("crates"), "assembly.crates")
    cfg.assembly_modules = [module_root(m) for m in check.str_list(assembly.get("modules"), "assembly.modules")]

    layers_allowed = set(canonical.get("layers", []))
    names: set[str] = set()
    for index, entry in enumerate(raw.get("contexts") or []):
        where = f"contexts[{index}]"
        entry = check.keys(entry, "contexts", where)
        name = entry.get("name")
        if not isinstance(name, str) or not name:
            check.p2(f"{where}.name", f"`{where}.name` 必須是非空字串")
            continue
        if name in names:
            check.p2(f"{where}.name", f"context `{name}` 重複宣告")
            continue
        names.add(name)
        layers: dict[str, str] = {}
        table = entry.get("layers", {})
        if not isinstance(table, dict) or not table:
            check.p2(f"{where}.layers", f"context `{name}` 至少要宣告一層")
            table = {}
        for layer, value in table.items():
            if layer not in layers_allowed:
                check.p2(f"{name}.{layer}", f"context `{name}` 宣告了 policy 沒有的層 `{layer}`")
                continue
            path = check.path(value, f"{where}.layers.{layer}")
            if path:
                layers[layer] = module_root(path)
        depends = check.str_list(entry.get("depends_on"), f"{where}.depends_on")
        cfg.contexts.append(Context(name, depends, layers))
    for ctx in cfg.contexts:
        for dep in ctx.depends_on:
            if dep == ctx.name or dep not in names:
                check.p2(f"{ctx.name}.depends_on", f"context `{ctx.name}` 的 depends_on 指到不存在或自己的 context `{dep}`")
    for cycle in _cycles(cfg.contexts):
        check.p2("depends_on", "depends_on 形成環：" + " → ".join(cycle))
    roots = [(path, f"{c.name}.{layer}") for c in cfg.contexts for layer, path in c.layers.items()]
    roots += [(cfg.shared_kernel, "shared_kernel")] if cfg.shared_kernel else []
    for i, (a, an) in enumerate(roots):
        for b, bn in roots[i + 1:]:
            if a == b or a.startswith(b + "/") or b.startswith(a + "/"):
                check.p2(f"{an}|{bn}", f"`{an}` 和 `{bn}` 的模組根重疊")

    for index, entry in enumerate(raw.get("large_files") or []):
        where = f"large_files[{index}]"
        entry = check.keys(entry, "large_files", where)
        path = check.path(entry.get("path"), f"{where}.path")
        limit = entry.get("max_lines")
        if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
            check.p2(f"{where}.max_lines", f"`{where}.max_lines` 必須是正整數")
        check.reason(entry.get("reason"), f"{where}.reason")
        if path and path in cfg.large_files:
            check.p2(f"{where}.path", f"large_files 重複列了 `{path}`")
        elif path and isinstance(limit, int):
            cfg.large_files[path] = {"max_lines": limit, "reason": entry.get("reason"), "index": index}
    for index, entry in enumerate(raw.get("exceptions") or []):
        where = f"exceptions[{index}]"
        entry = check.keys(entry, "exceptions", where)
        if entry.get("rule") not in SUPPRESSIBLE:
            check.p2(f"{where}.rule", f"`{where}.rule` 只能是 B1–B6")
        paths = check.str_list(entry.get("paths"), f"{where}.paths", allow_empty=False)
        for path in paths:
            if _is_glob(path):
                check.p2(f"{where}.paths", f"`{where}.paths` 不接受 glob：{path}")
        if not isinstance(entry.get("subject"), str) or not entry.get("subject"):
            check.p2(f"{where}.subject", f"`{where}.subject` 必須是非空字串")
        check.reason(entry.get("reason"), f"{where}.reason")
        cfg.exceptions.append(entry)
    for index, entry in enumerate(raw.get("renames") or []):
        where = f"renames[{index}]"
        entry = check.keys(entry, "renames", where)
        source = check.path(entry.get("from"), f"{where}.from")
        targets = check.str_list(entry.get("to"), f"{where}.to", allow_empty=False)
        if source and targets and not any(_is_glob(t) for t in targets):
            cfg.renames.append({"from": source, "to": targets})
    return cfg
