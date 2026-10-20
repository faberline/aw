"""Command line: ``check`` and ``scaffold``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import (
    cargo,
    config,
    ratchet,
    report,
    rules_ddd,
    rules_deps,
    rules_layout,
    rules_size,
)
from .files import file_lines, head, is_dirty, is_git_repo, repo_files
from .model import (
    ERROR,
    POLICY_PATH,
    Findings,
    RepoReport,
    ToolError,
    apply_exceptions,
    load_policy,
)
from .modtree import Index

DEFAULT_ROOT = Path.home() / "faberlines"


def _repos(root: Path, names: list[str]) -> list[Path]:
    if not names:
        if not root.is_dir():
            raise ToolError(f"root {root} is not a directory")
        return sorted(p for p in root.iterdir() if p.is_dir() and is_git_repo(p))
    out = []
    for name in names:
        path = Path(name).expanduser()
        path = path if path.is_dir() else root / name
        if not is_git_repo(path):
            raise ToolError(f"{name}: not a git repository ({path})")
        out.append(path.resolve())
    return out


def check_repo(path: Path, policy: dict, args: argparse.Namespace) -> RepoReport:
    result = RepoReport(repo=path.name, path=str(path))
    try:
        result.head, result.dirty = head(path), is_dirty(path)
        files = repo_files(path)
        if "Cargo.toml" not in files:
            result.notes.append("沒有根目錄 Cargo.toml，不是 Rust repo")
            return result
        result.language = "rust"
        out = Findings()
        crates = cargo.load(path, files)
        raw = config.read(path)
        cfg = config.parse(raw, policy, out) if raw is not None else None
        result.config = config.CONFIG_NAME if raw is not None else "none"
        result.enforced = not args.baseline and (args.strict or bool(cfg and cfg.enforce))
        rules_layout.check_tests(path, files, crates, policy, out)
        result.layout, result.d2 = rules_layout.check_cargo_shape(crates, policy, out)
        rules_layout.check_lints(crates, policy, out)
        rules_layout.check_docs(files, policy, out)
        index = Index(path, files, crates, cfg)
        rules_ddd.check_roots(index, out)
        if cfg is None:
            rules_ddd.check_undeclared(index, out)
            result.notes.append("ddd: undeclared（沒有 ddd.toml，略過 B2–B4）")
        else:
            rules_ddd.check_declared(index, cfg, policy, path.name, out)
            rules_deps.check(index, cfg, policy, out)
        result.large_files = rules_size.check(path, files, crates, policy, cfg, out, args.top)
        if args.base:
            ratchet.check(path, args.base, raw, cfg, files, policy, out, result.notes)
        result.findings = out.items()
        if cfg is not None:
            apply_exceptions(result.findings, cfg.exceptions)
        if not result.enforced:
            result.status = "report"
        else:
            failed = any(f.severity == ERROR for f in result.active())
            result.status = "fail" if failed else "pass"
    except ToolError as exc:
        result.status = "error"
        result.notes.append(str(exc))
    return result


def _check(args: argparse.Namespace) -> int:
    policy = load_policy()
    root = Path(args.root).expanduser().resolve()
    reports = [check_repo(path, policy, args) for path in _repos(root, args.repo)]
    code = 0
    if any(r.status == "fail" for r in reports):
        code = 1
    if any(r.status == "error" for r in reports):
        code = 2
    mode = "baseline" if args.baseline else ("strict" if args.strict else "enforce")
    if args.json == "-":
        sys.stdout.write(report.to_json(str(root), args.base, mode, code, reports))
    else:
        sys.stdout.write(report.to_text(mode, reports, args.top, args.verbose))
        if args.json:
            Path(args.json).write_text(report.to_json(str(root), args.base, mode, code, reports), encoding="utf-8")
    return code


def _scaffold(args: argparse.Namespace) -> int:
    policy = load_policy()
    root = Path(args.root).expanduser().resolve()
    (path,) = _repos(root, [args.repo])
    files = repo_files(path)
    crates = cargo.load(path, files)
    text = POLICY_PATH.read_text(encoding="utf-8")
    lines = ["schema = 1", "", "[migration]", "enforce = false", "", text[text.index("\n[policy]") + 1:].rstrip(), ""]
    index = Index(path, files, crates, None)
    for pkg in crates.member_packages():
        children = sorted(index.children(pkg, ()))
        lines.append(f"# {pkg.name}（{pkg.src_dir}）頂層模組：{', '.join(children) or '（無）'}")
    lines += ["", "# [shared_kernel]", '# path = "src/shared"', "",
              "# [assembly]", "# crates = []", "# modules = []", "",
              "# [[contexts]]", '# name = "<context>"', "# depends_on = []", "# [contexts.layers]"]
    lines += [f'# {layer} = "src/<context>/{layer}"' for layer in policy["layers"]]
    for source in rules_size.sources(files, crates):
        count = file_lines(path, source)
        if count > policy["size_error"]:
            lines += ["", "[[large_files]]", f'path = "{source}"', f"max_lines = {count}",
                      'reason = "TODO: 說明為什麼暫時不能拆，以及拆分計畫"']
    sys.stdout.write("\n".join(lines) + "\n")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aw standards rust-arch", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check", help="檢查 Rust repo 的結構、DDD 和大檔規則")
    check.add_argument("--repo", action="append", default=[], help="repo 名稱或路徑；可重複，省略則掃 --root 底下全部")
    check.add_argument("--root", default=str(DEFAULT_ROOT))
    check.add_argument("--base", help="ratchet（R1）比對的 base ref，取 merge-base")
    mode = check.add_mutually_exclusive_group()
    mode.add_argument("--baseline", action="store_true", help="全部只回報，不讓 exit code 失敗")
    mode.add_argument("--strict", action="store_true", help="把每個 Rust repo 都當成 enforce")
    check.add_argument("--json", help="JSON 報告輸出路徑；- 表示只輸出 JSON 到 stdout")
    check.add_argument("--top", type=int, default=5)
    check.add_argument("--verbose", action="store_true")
    scaffold = sub.add_parser("scaffold", help="印出某個 repo 的 ddd.toml 草稿（不寫檔）")
    scaffold.add_argument("--repo", required=True)
    scaffold.add_argument("--root", default=str(DEFAULT_ROOT))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return _check(args) if args.command == "check" else _scaffold(args)
    except ToolError as exc:
        print(f"rust-arch: {exc}", file=sys.stderr)
        return 2
