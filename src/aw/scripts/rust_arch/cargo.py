"""Cargo manifests: packages, workspace members, dependencies, and lints."""

from __future__ import annotations

import fnmatch
import posixpath
import re
from dataclasses import dataclass
from pathlib import Path

import tomllib

from .model import ToolError

DEP_TABLES = (("dependencies", "normal"), ("dev-dependencies", "dev"), ("build-dependencies", "build"))
_HEADER = re.compile(r"^\[\[?\s*([^\]]+?)\s*\]\]?")
_KEY = re.compile(r"""^(["']?)([A-Za-z0-9_\-]+)\1\s*[=.]""")


def rel(base: str, path: str) -> str:
    joined = posixpath.normpath(posixpath.join(base, path) if base else path)
    return "" if joined == "." else joined


def under(path: str, root: str) -> bool:
    return root == "" or path == root or path.startswith(root + "/")


@dataclass
class Dep:
    key: str
    package: str
    kind: str
    target: str | None
    optional: bool
    path: str | None
    line: int | None

    @property
    def code_name(self) -> str:
        return self.key.replace("-", "_")

    @property
    def crate(self) -> str:
        return self.package.replace("-", "_")


@dataclass
class Package:
    name: str
    dir: str
    manifest_path: str
    manifest: dict
    text: str
    src_dir: str
    lib: str | None
    bins: list[str]
    tests: list[str]
    build: bool
    deps: list[Dep]
    own_workspace: bool

    @property
    def crate(self) -> str:
        return self.name.replace("-", "_")

    def lints(self) -> dict:
        lints = self.manifest.get("lints", {})
        return lints if isinstance(lints, dict) else {}


@dataclass
class Cargo:
    root: dict | None
    root_text: str
    packages: dict[str, Package]
    members: list[str]
    strays: list[str]

    @property
    def is_workspace(self) -> bool:
        return self.root is not None and "workspace" in self.root

    @property
    def has_root_package(self) -> bool:
        return self.root is not None and "package" in self.root

    def workspace(self) -> dict:
        return (self.root or {}).get("workspace", {})

    def member_packages(self) -> list[Package]:
        return [self.packages[name] for name in self.members]

    def by_dir(self, path: str | None) -> Package | None:
        for pkg in self.packages.values():
            if path is not None and pkg.dir == path:
                return pkg
        return None

    def owner(self, path: str) -> Package | None:
        """The package whose directory most closely contains ``path``."""
        best = None
        for pkg in self.packages.values():
            if under(path, pkg.dir) and (best is None or len(pkg.dir) > len(best.dir)):
                best = pkg
        return best

    def internal(self, dep: Dep) -> Package | None:
        pkg = self.by_dir(dep.path)
        return pkg if pkg is not None and pkg.name in self.members else None


def lint_level(value: object) -> str | None:
    if isinstance(value, dict):
        value = value.get("level")
    return value if isinstance(value, str) else None


def key_lines(text: str) -> dict[tuple[str, str], int]:
    out: dict[tuple[str, str], int] = {}
    table = ""
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        header = _HEADER.match(line)
        if header:
            table = header.group(1).replace('"', "").replace("'", "")
            out.setdefault((table, ""), number)
            continue
        key = _KEY.match(line)
        if key:
            out.setdefault((table, key.group(2)), number)
    return out


def _parse(repo: Path, path: str) -> tuple[dict, str]:
    text = (repo / path).read_text(encoding="utf-8", errors="replace")
    try:
        return tomllib.loads(text), text
    except tomllib.TOMLDecodeError as exc:
        raise ToolError(f"{repo.name}/{path}: TOML parse error: {exc}") from exc


def _deps(manifest: dict, pkg_dir: str, ws_deps: dict, lines: dict) -> list[Dep]:
    out: list[Dep] = []

    def collect(table: object, kind: str, target: str | None, prefix: str) -> None:
        if not isinstance(table, dict):
            return
        for key, spec in table.items():
            spec = {"version": spec} if isinstance(spec, str) else spec
            inherited = spec.get("workspace") is True
            base = ws_deps.get(key, {}) if inherited else {}
            base = {"version": base} if isinstance(base, str) else base
            path = None
            if spec.get("path"):
                path = rel(pkg_dir, spec["path"])
            elif base.get("path"):
                path = rel("", base["path"])
            line = lines.get((prefix, key)) or lines.get((f"{prefix}.{key}", ""))
            package = spec.get("package") or base.get("package") or key
            out.append(Dep(key, package, kind, target, bool(spec.get("optional")), path, line))

    for table, kind in DEP_TABLES:
        collect(manifest.get(table), kind, None, table)
        collect(manifest.get(table.replace("-", "_")), kind, None, table.replace("-", "_"))
    for target, spec in (manifest.get("target") or {}).items():
        for table, kind in DEP_TABLES:
            collect((spec or {}).get(table), kind, target, f"target.{target}.{table}")
    return out


def _package(path: str, manifest: dict, text: str, fileset: set[str], ws_deps: dict) -> Package:
    pkg_dir = posixpath.dirname(path)
    meta = manifest["package"]
    lib_path = (manifest.get("lib") or {}).get("path")
    lib = rel(pkg_dir, lib_path) if lib_path else rel(pkg_dir, "src/lib.rs")
    lib = lib if lib in fileset else None
    src_dir = posixpath.dirname(lib) if lib else rel(pkg_dir, "src")
    bins = []
    main = rel(pkg_dir, "src/main.rs")
    if main in fileset:
        bins.append(main)
    bin_dir = rel(pkg_dir, "src/bin") + "/"
    for file in fileset:
        if file.startswith(bin_dir) and file.endswith(".rs"):
            tail = file[len(bin_dir):].split("/")
            if len(tail) == 1 or (len(tail) == 2 and tail[1] == "main.rs"):
                bins.append(file)
    for entry in manifest.get("bin") or []:
        if isinstance(entry, dict) and entry.get("path"):
            bins.append(rel(pkg_dir, entry["path"]))
    build_key = meta.get("build")
    build = isinstance(build_key, str) or (build_key is not False and rel(pkg_dir, "build.rs") in fileset)
    tests = [t["path"] for t in manifest.get("test") or [] if isinstance(t, dict) and t.get("path")]
    return Package(
        name=str(meta.get("name", pkg_dir or "root")),
        dir=pkg_dir,
        manifest_path=path,
        manifest=manifest,
        text=text,
        src_dir=src_dir,
        lib=lib,
        bins=sorted(set(bins)),
        tests=tests,
        build=build,
        deps=_deps(manifest, pkg_dir, ws_deps, key_lines(text)),
        own_workspace=path != "Cargo.toml" and "workspace" in manifest,
    )


def _glob_match(path: str, pattern: str) -> bool:
    parts, pats = path.split("/"), rel("", pattern).split("/")
    return len(parts) == len(pats) and all(fnmatch.fnmatchcase(a, b) for a, b in zip(parts, pats))


def load(repo: Path, files: list[str]) -> Cargo:
    fileset = set(files)
    if "Cargo.toml" not in fileset:
        return Cargo(None, "", {}, [], [])
    root, root_text = _parse(repo, "Cargo.toml")
    ws = root.get("workspace") or {}
    ws_deps = ws.get("dependencies") or {}
    packages: dict[str, Package] = {}
    for path in files:
        if path != "Cargo.toml" and not path.endswith("/Cargo.toml"):
            continue
        try:
            manifest, text = (root, root_text) if path == "Cargo.toml" else _parse(repo, path)
        except ToolError:
            if path == "Cargo.toml":
                raise
            continue
        if isinstance(manifest.get("package"), dict):
            pkg = _package(path, manifest, text, fileset, ws_deps)
            packages.setdefault(pkg.name, pkg)
    members: list[str] = []
    excluded = [rel("", p) for p in ws.get("exclude") or [] if isinstance(p, str)]
    excluded += [p.dir for p in packages.values() if p.own_workspace]
    if "workspace" in root:
        patterns = [p for p in ws.get("members") or [] if isinstance(p, str)]
        for pkg in packages.values():
            if pkg.dir == "" or any(_glob_match(pkg.dir, p) for p in patterns) and not any(
                under(pkg.dir, e) for e in excluded
            ):
                members.append(pkg.name)
    elif "package" in root:
        members = [p.name for p in packages.values() if p.dir == ""]
    strays = sorted(
        p.name
        for p in packages.values()
        if p.name not in members and not any(under(p.dir, e) for e in excluded)
    )
    return Cargo(root, root_text, packages, sorted(members), strays)
