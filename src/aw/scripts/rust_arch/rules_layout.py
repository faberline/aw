"""Repo layout rules: tests (A1), Cargo shape (A2), clippy lint (A3), root docs (A4, A5)."""

from __future__ import annotations

import re
from pathlib import Path

from .cargo import Cargo, lint_level, rel
from .model import Findings

_ISOLATION = re.compile(r"//!\s*isolation:(.*)")


def _isolation_reason(text: str) -> str | None:
    """The reason on a `//! isolation:` line in the file's leading //! block, if any.

    The block runs from the top of the file until the first line that is not
    blank, a `//!` line, or a one-line `#![..]` attribute.
    """
    for line in text.splitlines():
        stripped = line.strip()
        match = _ISOLATION.fullmatch(stripped)
        if match:
            return match.group(1).strip()
        if stripped and not stripped.startswith("//!") and not stripped.startswith("#!["):
            return None
    return None


def _loose_test(path: str, text: str, markers: list[str], single: bool, it_dir: str, out: Findings) -> None:
    """A1 exceptions for a tests/<name>.rs binary: sole target, isolation marker, or isolation doc line."""
    if single or any(marker in text for marker in markers):
        return
    reason = _isolation_reason(text)
    if reason:
        return
    if reason == "":
        out.error("A1", path, None, "tests/*.rs", f"`{path}` 的 `//! isolation:` 沒有寫理由")
        return
    out.error("A1", path, None, "tests/*.rs",
              f"`{path}` 不是這個 crate 唯一的測試檔，也沒有 isolation 標記；"
              f"移到 {it_dir}，或在檔頭 //! 區塊寫 `//! isolation: <理由>`")


def check_tests(repo: Path, files: list[str], cargo: Cargo, policy: dict, out: Findings) -> None:
    markers = policy["test_isolation_markers"]
    data_dirs = set(policy["test_data_dirs"])
    e2e_dirs = set()
    for path in files:
        parts = path.split("/")[:-1]
        if "e2e" in parts:
            index = parts.index("e2e")
            # rust-tests.md: e2e suites are modules of the one integration binary.
            if parts[max(index - 2, 0):index] == ["tests", "it"]:
                continue
            e2e_dirs.add("/".join(parts[: index + 1]))
    for directory in sorted(e2e_dirs):
        out.error("A1", directory + "/", None, "e2e", f"不能有 e2e/ 目錄（`{directory}/`）；e2e 測試放 tests/it/e2e/")
    for pkg in cargo.member_packages():
        tests = rel(pkg.dir, "tests") + "/"
        has_it = False
        loose: list[str] = []
        for path in files:
            if not path.startswith(tests) or not path.endswith(".rs") or cargo.owner(path) is not pkg:
                continue
            tail = path[len(tests):].split("/")
            if len(tail) == 1:
                loose.append(path)
            elif tail[0] == "it":
                has_it = True
            elif tail[0] not in data_dirs:
                out.error("A1", f"{tests}{tail[0]}/", None, "tests/<dir>",
                          f"`{tests}{tail[0]}/` 含 .rs；tests/ 底下只允許 it/ 放程式碼"
                          f"（測試資料目錄：{'、'.join(sorted(data_dirs))}）")
        single = len(loose) + has_it == 1
        for path in loose:
            text = (repo / path).read_text(encoding="utf-8", errors="replace")
            _loose_test(path, text, markers, single, f"{tests}it/", out)
        if has_it and f"{tests}it/main.rs" not in files:
            out.error("A1", f"{tests}it/", None, "tests/it/main.rs", f"`{tests}it/` 缺少 main.rs")
        for target in pkg.tests:
            if not rel("", target).startswith("tests/"):
                out.error("A1", pkg.manifest_path, None, "[[test]]",
                          f"[[test]] 的 path `{target}` 必須在 tests/ 底下")


def check_cargo_shape(cargo: Cargo, policy: dict, out: Findings) -> tuple[str, bool]:
    """A2; returns (layout label, D2-compliant)."""
    members = cargo.member_packages()
    crates_dir = policy["naming"]["crates_dir"]
    ok = True
    for name in cargo.strays:
        pkg = cargo.packages[name]
        out.warn("A2", pkg.manifest_path, None, name,
                 f"package `{name}` 不在 workspace members 裡，也沒有被 exclude")
    if len(members) <= 1:
        if cargo.is_workspace:
            out.error("A2", "Cargo.toml", None, "[workspace]",
                      "單 crate repo 不應包一層只有一個 member 的 [workspace]")
            return "single+workspace", False
        return "single", True
    ws = cargo.workspace()
    if cargo.has_root_package:
        ok = False
        out.warn("A2", "Cargo.toml", None, "virtual",
                 "多 crate repo 的根 Cargo.toml 應該是 virtual workspace（不能有 [package]）")
    for pkg in members:
        expected = f"{crates_dir}/{pkg.name}"
        if pkg.dir and pkg.dir != expected:
            ok = False
            out.warn("A2", pkg.manifest_path, None, pkg.name,
                     f"member `{pkg.name}` 應放在 `{expected}/`，目前在 `{pkg.dir}/`")
    for table in ("package", "dependencies"):
        if not isinstance(ws.get(table), dict):
            ok = False
            out.warn("A2", "Cargo.toml", None, f"workspace.{table}",
                     f"多 crate repo 應該用 [workspace.{table}] 統一設定")
    return ("virtual" if not cargo.has_root_package else "root-package-workspace"), ok


def check_lints(cargo: Cargo, policy: dict, out: Findings) -> None:
    wanted = policy.get("lints", {})
    if cargo.root is None:
        return
    for group, table in wanted.items():
        for name, level in table.items():
            if cargo.is_workspace:
                lints = cargo.workspace().get("lints") or {}
                actual = lint_level((lints.get(group) or {}).get(name))
                if actual != level:
                    out.warn("A3", "Cargo.toml", None, f"{group}::{name}",
                             f"未設定 [workspace.lints.{group}] {name} = \"{level}\"")
                    continue
                for pkg in cargo.member_packages():
                    if pkg.lints().get("workspace") is not True:
                        out.warn("A3", pkg.manifest_path, None, f"{group}::{name}",
                                 f"`{pkg.name}` 沒有 [lints] workspace = true，不會套用 workspace 的 {group} lint")
            else:
                for pkg in cargo.member_packages():
                    actual = lint_level((pkg.lints().get(group) or {}).get(name))
                    if actual != level:
                        out.warn("A3", pkg.manifest_path, None, f"{group}::{name}",
                                 f"未設定 [lints.{group}] {name} = \"{level}\"")


def check_docs(files: list[str], policy: dict, out: Findings) -> None:
    allowed = set(policy["root_md"])
    for path in files:
        if "/" not in path and not path.startswith(".") and path.lower().endswith(".md") and path not in allowed:
            out.error("A4", path, None, path, f"根目錄不能放 `{path}`；文件請放 docs/")
    for required in policy["docs_required"]:
        if required.endswith("/"):
            if not any(f.startswith(required) for f in files):
                out.error("A5", required, None, required, f"缺少 `{required}`（目錄底下至少要有一個檔案）")
        elif required not in files:
            out.error("A5", required, None, required, f"缺少 `{required}`")

