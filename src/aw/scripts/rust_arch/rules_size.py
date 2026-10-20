"""Large-file rules: warn size (C1), error size (C2), and the large_files allowlist (C3)."""

from __future__ import annotations

from pathlib import Path

from .cargo import Cargo, under
from .config import Config
from .files import file_lines
from .model import Findings


def sources(files: list[str], cargo: Cargo) -> list[str]:
    """Every .rs file under the src directory of the Cargo package that owns it."""
    out = []
    for path in files:
        if not path.endswith(".rs"):
            continue
        pkg = cargo.owner(path)
        if pkg is not None and under(path, pkg.src_dir) and pkg.src_dir != pkg.dir:
            out.append(path)
    return out


def check(repo: Path, files: list[str], cargo: Cargo, policy: dict, cfg: Config | None,
          out: Findings, top: int) -> dict:
    warn, error = policy["size_warn"], policy["size_error"]
    allow = cfg.large_files if cfg is not None else {}
    lines = {path: file_lines(repo, path) for path in sources(files, cargo)}
    for path, count in lines.items():
        entry = allow.get(path)
        if count > error and entry is None:
            out.error("C2", path, None, path,
                      f"{count} 行，超過 {error} 行上限；請拆檔，或在 ddd.toml 的 [[large_files]] 登記")
        elif warn < count <= error and entry is None:
            out.warn("C1", path, None, path, f"{count} 行，超過 {warn} 行建議上限")
    for path, entry in allow.items():
        where = f"large_files[{entry['index']}]"
        count = lines.get(path)
        limit = entry["max_lines"]
        if count is None:
            out.error("C3", "ddd.toml", None, path, f"{where} 的 `{path}` 不存在，請移除這筆登記")
        elif count <= error:
            out.error("C3", "ddd.toml", None, path,
                      f"{where} 的 `{path}` 已降到 {count} 行（未超過 {error}），請移除這筆登記")
        elif count > limit:
            out.error("C3", path, None, path, f"{count} 行，超過 {where} 登記的 max_lines = {limit}")
        elif count < limit:
            out.warn("C3", "ddd.toml", None, path,
                     f"`{path}` 已縮到 {count} 行，請把 {where} 的 max_lines 從 {limit} 調降到 {count}")
    ranked = sorted(lines.items(), key=lambda item: (-item[1], item[0]))
    return {
        "over_warn": sum(1 for count in lines.values() if count > warn),
        "over_error": sum(1 for count in lines.values() if count > error),
        "allowlisted": len(allow),
        "top": [{"path": path, "lines": count} for path, count in ranked[:top]],
    }
