#!/usr/bin/env python3
"""Check faberline SaaS service repositories against docs/standards/saas-service.md.

One verb today:

  check   run the SaaS service skeleton checks on one or more repositories

The checks read only tracked files: `aw.toml`, `ddd.toml`, the `Cargo.toml`
files, and the `k8s/` and `clients/` paths.  A repository opts out of one
check with a reason in its own `aw.toml`:

  [[standards.saas_service.exception]]
  check = "SV5"
  reason = "replication moves out of src/cluster in the crate split"
  path = "crates/relay-replication/Cargo.toml"   # optional; omit to cover every path

An excepted finding is reported and counted but does not fail the run.
Exit codes: 0 pass, 1 at least one unexcepted finding, 2 usage error.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any

SCHEMA = "saas-service-report/1"
KIND = "saas-service"
# Slot suffix -> the dependency that shows the package does its job ("" = none).
REQUIRED = {"": "", "shared-kernel": "", "access": "service-auth",
            "replication": "raft-runtime", "storage": "", "operator": "kube"}
OPTIONAL = {"client", "bench"}
# Slots that are not bounded contexts in ddd.toml.
ASSEMBLY = {"", "shared-kernel", "client", "bench"}
MAX_PRODUCT = 3
OVERLAYS = ("dev", "staging", "prod", "template", "gke-test")
DEP_TABLES = ("dependencies", "dev-dependencies", "build-dependencies")
CHECKS = {
    "SV0": "every exception names a check and a reason",
    "SV1": 'aw.toml declares [project] kind = "saas-service"',
    "SV2": "the root Cargo.toml is a virtual workspace of crates/<package>",
    "SV3": "the required slot packages exist",
    "SV4": "every other package is a declared context; at most 3 product contexts",
    "SV5": "each slot package depends on the library that does its job",
    "SV6": "each context package is one ddd.toml context with paths in its package",
    "SV7": "k8s/ has base, operator, and the five overlays",
    "SV8": "clients/codegen.toml exists",
}


@dataclass(frozen=True)
class Finding:
    check: str
    path: str
    message: str


def tracked(repo: Path) -> set[str]:
    out = subprocess.run(
        ["git", "-C", str(repo), "ls-files", "-z"],
        capture_output=True, text=True, check=True,
    ).stdout
    return {p for p in out.split("\0") if p}


def load(repo: Path, rel: str, files: set[str]) -> dict[str, Any] | None:
    return tomllib.loads((repo / rel).read_text()) if rel in files else None


def dep_names(manifest: dict[str, Any]) -> set[str]:
    names = set()
    for table in DEP_TABLES:
        for name, spec in manifest.get(table, {}).items():
            names.add(str(spec.get("package", name)) if isinstance(spec, dict) else name)
    return names


def check_repo(repo: Path) -> list[Finding]:
    files = tracked(repo)
    aw = load(repo, "aw.toml", files) or {}
    project = aw.get("project", {})
    name = str(project.get("name") or repo.resolve().name)
    findings: list[Finding] = []
    if project.get("kind") != KIND:
        findings.append(Finding("SV1", "aw.toml", f'set [project] kind = "{KIND}"'))

    root = load(repo, "Cargo.toml", files) or {}
    packages: dict[str, tuple[str, dict[str, Any]]] = {}
    for rel in sorted(files):
        parts = PurePosixPath(rel).parts
        if len(parts) == 3 and parts[0] == "crates" and parts[2] == "Cargo.toml":
            manifest = tomllib.loads((repo / rel).read_text())
            pkg = manifest.get("package", {}).get("name")
            if pkg:
                packages[pkg] = (rel, manifest)
                if pkg != parts[1]:
                    findings.append(Finding("SV2", rel, f"folder {parts[1]} holds package {pkg}"))
    if "workspace" not in root or "package" in root or not packages:
        findings.append(Finding("SV2", "Cargo.toml",
                                "use a virtual [workspace] with members under crates/"))

    slots = {(f"{name}-{s}" if s else name): s for s in [*REQUIRED, *OPTIONAL]}
    for suffix, lib in REQUIRED.items():
        pkg = f"{name}-{suffix}" if suffix else name
        if pkg not in packages:
            findings.append(Finding("SV3", "crates/", f"add package {pkg}"))
        elif lib and lib not in dep_names(packages[pkg][1]):
            findings.append(Finding("SV5", packages[pkg][0], f"{pkg} does not depend on {lib}"))

    ddd = load(repo, "ddd.toml", files)
    contexts = {str(c.get("name")): c for c in (ddd or {}).get("contexts", [])}
    product = []
    for pkg, (rel, _) in sorted(packages.items()):
        if pkg in slots:
            continue
        context = pkg.removeprefix(f"{name}-")
        if context == pkg or context not in contexts:
            findings.append(Finding("SV4", rel, f"{pkg} is not a slot or a ddd.toml context"))
        else:
            product.append(pkg)
    if len(product) > MAX_PRODUCT:
        findings.append(Finding("SV4", "crates/",
                                f"{len(product)} product contexts; at most {MAX_PRODUCT}"))

    if ddd is None and packages:
        findings.append(Finding("SV6", "ddd.toml", "add ddd.toml with one context per package"))
    else:
        for pkg in sorted(packages):
            suffix = slots.get(pkg, pkg.removeprefix(f"{name}-"))
            if suffix in ASSEMBLY or pkg == suffix:
                continue
            context = contexts.get(suffix)
            if context is None:
                if pkg in slots:  # SV4 already reports an undeclared non-slot package
                    findings.append(Finding("SV6", "ddd.toml",
                                            f"declare context {suffix} for {pkg}"))
                continue
            paths = [str(p) for p in context.get("layers", {}).values()]
            outside = [p for p in paths if not p.startswith(f"crates/{pkg}/")]
            if not paths or outside:
                findings.append(Finding("SV6", "ddd.toml",
                                        f"context {suffix} layer paths must be in crates/{pkg}/"))

    needed = ["k8s/base/kustomization.yaml", "k8s/operator/"]
    needed += [f"k8s/overlays/{o}/kustomization.yaml" for o in OVERLAYS]
    for path in needed:
        hit = any(f.startswith(path) for f in files) if path.endswith("/") else path in files
        if not hit:
            findings.append(Finding("SV7", path, "missing"))
    if "clients/codegen.toml" not in files:
        findings.append(Finding("SV8", "clients/codegen.toml", "missing"))
    return findings


def exceptions(repo: Path) -> tuple[list[dict[str, str]], list[Finding]]:
    path = repo / "aw.toml"
    if not path.is_file():
        return [], []
    entries = (tomllib.loads(path.read_text())
               .get("standards", {}).get("saas_service", {}).get("exception", []))
    valid, broken = [], []
    for i, entry in enumerate(entries):
        if entry.get("check") in CHECKS and str(entry.get("reason", "")).strip():
            valid.append(entry)
        else:
            broken.append(Finding("SV0", "aw.toml",
                                  f"exception {i + 1} needs a known check and a reason"))
    return valid, broken


def excepted(finding: Finding, entries: list[dict[str, str]]) -> dict[str, str] | None:
    for entry in entries:
        if entry["check"] == finding.check and entry.get("path", finding.path) == finding.path:
            return entry
    return None


def run(repos: list[Path]) -> dict[str, Any]:
    report: dict[str, Any] = {"schema": SCHEMA, "repos": [], "ok": True}
    for repo in repos:
        entries, broken = exceptions(repo)
        failing, waived = list(broken), []
        for finding in check_repo(repo):
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
    lines = []
    for repo in report["repos"]:
        lines.append(f"{'PASS' if repo['ok'] else 'FAIL'} {repo['repo']}")
        for f in repo["findings"]:
            lines.append(f"  {f['check']} {f['path']}: {f['message']}")
        for f in repo["excepted"]:
            lines.append(f"  excepted {f['check']} {f['path']}: {f['reason']}")
    return "\n".join(lines)


def cmd_check(args: argparse.Namespace) -> int:
    repos = [Path(r) for r in args.repo] or [Path(".")]
    for path in repos:
        if not (path / ".git").exists():
            print(f"error: {path} is not a git checkout", file=sys.stderr)
            return 2
    try:
        report = run(repos)
    except (ValueError, tomllib.TOMLDecodeError, subprocess.CalledProcessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2) if args.format == "json" else render(report))
    return 0 if report["ok"] else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aw standards saas-service", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="verb", required=True)
    check = sub.add_parser("check", help="run the SaaS service skeleton checks")
    check.add_argument("--repo", action="append", default=[],
                       help="repository root (repeatable; default: .)")
    check.add_argument("--format", choices=("text", "json"), default="text")
    check.set_defaults(func=cmd_check)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
