#!/usr/bin/env python3
"""Check faberline repositories against the shared standards in docs/standards/.

One verb today:

  check   run the core-reuse checks (docs/standards/core-reuse.md) on one or
          more Rust repositories against a local `faberline/core` checkout

The checks read only tracked `Cargo.toml` and `build.rs` files, so each one is
a plain pass or fail.  A repository opts out of one check with a reason in its
own `aw.toml`:

  [[standards.core_reuse.exception]]
  check = "CR3"
  reason = "pinned until the index migration lands"
  path = "Cargo.toml"        # optional; omit to cover every path

An excepted finding is reported and counted but does not fail the run.
Exit codes: 0 pass, 1 at least one unexcepted finding, 2 usage error.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import re
import subprocess
import sys
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any

SCHEMA = "core-reuse-report/1"
CORE_SOURCE = re.compile(r"github\.com[:/]faberline/core(\.git)?/?$")
FABERLINE_SOURCE = re.compile(r"github\.com[:/]faberline/")
TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
STAMP = re.compile(r"cargo:rustc-env=[A-Z0-9_]*GIT_SHA")
DENIED = {
    "prometheus": "metrics-prometheus",
    "openraft": "raft-core",
    "raft": "raft-core",
}
SKIP_PARTS = {"vendor", "fixtures", "testdata", "target"}
DEP_TABLES = ("dependencies", "dev-dependencies", "build-dependencies")
CHECKS = {
    "CR0": "every exception names a check and a reason",
    "CR1": "core is pinned by git tag, never by path, branch, or rev",
    "CR2": "one core tag per repository",
    "CR3": "the pinned tag is the current core release",
    "CR4": "a workspace member takes core from [workspace.dependencies]",
    "CR5": "a repository that builds a binary depends on cli-std",
    "CR6": "a build.rs that stamps GIT_SHA uses build-stamp",
    "CR7": "no direct dependency that core already replaces",
    "CR8": "core depends on no other faberline repository",
}


@dataclass(frozen=True)
class Finding:
    check: str
    path: str
    message: str


@dataclass(frozen=True)
class Dep:
    manifest: str
    table: str
    name: str
    spec: dict[str, Any]

    @property
    def package(self) -> str:
        return str(self.spec.get("package", self.name))

    @property
    def from_core(self) -> bool:
        return bool(CORE_SOURCE.search(str(self.spec.get("git", ""))))


def tracked(repo: Path, pattern: str) -> list[str]:
    out = subprocess.run(
        ["git", "-C", str(repo), "ls-files", "-z", "--", pattern],
        capture_output=True, text=True, check=True,
    ).stdout
    paths = [p for p in out.split("\0") if p]
    return sorted(p for p in paths
                  if not SKIP_PARTS & set(PurePosixPath(p).parts)
                  and (repo / p).is_file())


def core_release(core: Path) -> tuple[str, set[str]]:
    """The highest vX.Y.Z tag and the package names under core/crates/."""
    tags = subprocess.run(
        ["git", "-C", str(core), "tag", "--list", "v*"],
        capture_output=True, text=True, check=True,
    ).stdout.split()
    versions = sorted((tuple(map(int, m.groups())), t)
                      for t in tags if (m := TAG.match(t)))
    if not versions:
        raise ValueError(f"{core}: no vX.Y.Z tag")
    crates = set()
    for manifest in sorted((core / "crates").glob("*/Cargo.toml")):
        crates.add(tomllib.loads(manifest.read_text())["package"]["name"])
    return versions[-1][1], crates


def dependencies(rel: str, data: dict[str, Any]) -> list[Dep]:
    tables: list[tuple[str, dict[str, Any]]] = []
    for name in DEP_TABLES:
        tables.append((name, data.get(name, {})))
    for target, body in data.get("target", {}).items():
        for name in DEP_TABLES:
            tables.append((f"target.{target}.{name}", body.get(name, {})))
    tables.append(("workspace.dependencies",
                   data.get("workspace", {}).get("dependencies", {})))
    deps = []
    for table, entries in tables:
        for name, spec in entries.items():
            spec = spec if isinstance(spec, dict) else {"version": spec}
            deps.append(Dep(rel, table, name, spec))
    return deps


def member(workspace: dict[str, Any], manifest: str) -> bool:
    """True when a non-root manifest belongs to the root workspace."""
    folder = str(PurePosixPath(manifest).parent)
    if folder == ".":
        return False
    hit = lambda globs: any(fnmatch.fnmatchcase(folder, g.rstrip("/"))
                            for g in globs)
    return hit(workspace.get("members", [])) and not hit(workspace.get("exclude", []))


def check_repo(repo: Path, tag: str, crates: set[str], is_core: bool) -> list[Finding]:
    manifests = {rel: tomllib.loads((repo / rel).read_text())
                 for rel in tracked(repo, "*Cargo.toml")}
    deps = [d for rel, data in manifests.items() for d in dependencies(rel, data)]
    findings: list[Finding] = []
    if is_core:
        for d in deps:
            git = str(d.spec.get("git", ""))
            if FABERLINE_SOURCE.search(git) and not d.from_core:
                findings.append(Finding("CR8", d.manifest,
                                        f"{d.name} comes from {git}"))
        return findings

    pinned: dict[str, list[str]] = {}
    for d in deps:
        if d.from_core:
            bad = [k for k in ("branch", "rev") if k in d.spec]
            if bad or "tag" not in d.spec:
                findings.append(Finding("CR1", d.manifest,
                                        f"{d.name} uses {', '.join(bad) or 'no tag'}"))
            if "tag" in d.spec:
                pinned.setdefault(str(d.spec["tag"]), []).append(d.manifest)
        elif d.package in crates and "path" in d.spec:
            target = (repo / d.manifest).parent / str(d.spec["path"])
            if not target.resolve().is_relative_to(repo.resolve()):
                findings.append(Finding("CR1", d.manifest,
                                        f"{d.name} is a path outside the repository"))
    if len(pinned) > 1:
        findings.append(Finding("CR2", "Cargo.toml",
                                f"pins {', '.join(sorted(pinned))}"))
    for old in sorted(set(pinned) - {tag}):
        for rel in sorted(set(pinned[old])):
            findings.append(Finding("CR3", rel, f"pins {old}; current is {tag}"))

    root = manifests.get("Cargo.toml", {})
    if "workspace" in root:
        for d in deps:
            if d.from_core and member(root["workspace"], d.manifest):
                findings.append(Finding(
                    "CR4", d.manifest,
                    f"{d.name} names the core git source; use {d.name}.workspace = true"))

    uses = {d.package for d in deps if d.from_core or d.spec.get("workspace") is True}
    uses &= crates
    binaries = sorted(
        rel for rel, data in manifests.items()
        if "package" in data and (
            data.get("bin")
            or (repo / rel).parent.joinpath("src/main.rs").is_file()))
    if binaries and "cli-std" not in uses:
        findings.append(Finding("CR5", binaries[0],
                                "builds a binary without cli-std"))

    for script in tracked(repo, "*build.rs"):
        if not STAMP.search((repo / script).read_text(errors="replace")):
            continue
        manifest = str(PurePosixPath(script).parent / "Cargo.toml").removeprefix("./")
        build = {d.package for d in deps
                 if d.manifest == manifest and d.table.endswith("build-dependencies")}
        if "build-stamp" not in build:
            findings.append(Finding("CR6", script,
                                    "stamps GIT_SHA by hand; use build-stamp"))

    for d in deps:
        if d.package in DENIED and not d.from_core:
            findings.append(Finding("CR7", d.manifest,
                                    f"{d.package}; use {DENIED[d.package]}"))
    return findings


def exceptions(repo: Path) -> tuple[list[dict[str, str]], list[Finding]]:
    path = repo / "aw.toml"
    if not path.is_file():
        return [], []
    entries = (tomllib.loads(path.read_text())
               .get("standards", {}).get("core_reuse", {}).get("exception", []))
    valid, broken = [], []
    for i, entry in enumerate(entries):
        if entry.get("check") in CHECKS and str(entry.get("reason", "")).strip():
            valid.append(entry)
        else:
            broken.append(Finding("CR0", "aw.toml",
                                  f"exception {i + 1} needs a known check and a reason"))
    return valid, broken


def excepted(finding: Finding, entries: list[dict[str, str]]) -> dict[str, str] | None:
    for entry in entries:
        if entry["check"] == finding.check and entry.get("path", finding.path) == finding.path:
            return entry
    return None


def run(repos: list[Path], core: Path) -> dict[str, Any]:
    tag, crates = core_release(core)
    report: dict[str, Any] = {
        "schema": SCHEMA, "core": {"path": str(core), "tag": tag, "crates": len(crates)},
        "repos": [], "ok": True,
    }
    for repo in repos:
        entries, broken = exceptions(repo)
        is_core = repo.resolve() == core.resolve()
        failing, waived = list(broken), []
        for finding in check_repo(repo, tag, crates, is_core):
            entry = excepted(finding, entries)
            if entry is None:
                failing.append(finding)
            else:
                waived.append({**asdict(finding), "reason": entry["reason"]})
        report["repos"].append({
            "repo": str(repo), "ok": not failing,
            "findings": [asdict(f) for f in failing], "excepted": waived,
        })
        report["ok"] = report["ok"] and not failing
    return report


def render(report: dict[str, Any]) -> str:
    core = report["core"]
    lines = [f"core {core['tag']} ({core['crates']} crates)"]
    for repo in report["repos"]:
        lines.append(f"{'PASS' if repo['ok'] else 'FAIL'} {repo['repo']}")
        for f in repo["findings"]:
            lines.append(f"  {f['check']} {f['path']}: {f['message']}")
        for f in repo["excepted"]:
            lines.append(f"  excepted {f['check']} {f['path']}: {f['reason']}")
    return "\n".join(lines)


def cmd_check(args: argparse.Namespace) -> int:
    repos = [Path(r) for r in args.repo] or [Path(".")]
    core = Path(args.core) if args.core else repos[0].resolve().parent / "core"
    for path in [*repos, core]:
        if not (path / ".git").exists():
            print(f"error: {path} is not a git checkout", file=sys.stderr)
            return 2
    try:
        report = run(repos, core)
    except (ValueError, tomllib.TOMLDecodeError, subprocess.CalledProcessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2) if args.format == "json" else render(report))
    return 0 if report["ok"] else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aw standards", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="verb", required=True)
    check = sub.add_parser("check", help="run the core-reuse checks")
    check.add_argument("--repo", action="append", default=[],
                       help="repository root (repeatable; default: .)")
    check.add_argument("--core", help="faberline/core checkout (default: ../core)")
    check.add_argument("--format", choices=("text", "json"), default="text")
    check.set_defaults(func=cmd_check)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
