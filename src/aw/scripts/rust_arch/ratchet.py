"""Ratchet against a base ref (R1): allowlists and policy may only tighten."""

from __future__ import annotations

from pathlib import Path

from . import config
from .config import Config
from .files import merge_base, show, tree_files
from .model import Findings


class _Ratchet:
    def __init__(self, base: Config, head: Config, base_files: set[str], head_files: set[str],
                 base_raw: dict, out: Findings) -> None:
        self.base, self.head, self.out = base, head, out
        self.base_files, self.head_files = base_files, head_files
        self.old_renames = {(r.get("from"), tuple(r.get("to") or [])) for r in base_raw.get("renames") or []
                            if isinstance(r, dict)}
        self.moved: dict[str, str] = {}

    def r1(self, subject: str, message: str) -> None:
        self.out.error("R1", config.CONFIG_NAME, None, subject, message)

    def policy(self) -> None:
        for key in config.policy_diff(self.base.policy, self.head.policy):
            self.r1(f"policy.{key}", f"[policy] 的 `{key}` 跟 base 不同；policy 只能透過 canonical policy.toml 調整")
        if self.base.enforce and not self.head.enforce:
            self.r1("migration.enforce", "`migration.enforce` 不能從 true 改回 false")

    def renames(self) -> None:
        for entry in self.head.renames:
            source, targets = entry["from"], entry["to"]
            if source not in self.base_files:
                if (source, tuple(targets)) in self.old_renames:
                    self.out.warn("P2", config.CONFIG_NAME, None, f"renames:{source}",
                                  f"renames 的 `{source}` 已經在 base 完成，請移除這筆")
                else:
                    self.r1(f"renames:{source}", f"renames 的 from `{source}` 在 base 不存在")
                continue
            if source in self.head_files:
                self.r1(f"renames:{source}", f"renames 的 from `{source}` 在目前版本仍然存在")
                continue
            bad = [t for t in targets if t not in self.head_files or t in self.base_files]
            if bad:
                self.r1(f"renames:{source}", "renames 的 to 必須是這次新增的檔案：" + "、".join(bad))
                continue
            for target in targets:
                self.moved[target] = source
            old = self.base.large_files.get(source)
            total = sum(self.head.large_files[t]["max_lines"] for t in targets if t in self.head.large_files)
            if old is not None and total > old["max_lines"]:
                self.r1(f"renames:{source}",
                        f"`{source}` 拆出的檔案 max_lines 合計 {total}，超過原本的 {old['max_lines']}")

    def large_files(self) -> None:
        for path, entry in self.head.large_files.items():
            old = self.base.large_files.get(path)
            if old is not None:
                if entry["max_lines"] > old["max_lines"]:
                    self.r1(f"large_files:{path}",
                            f"`{path}` 的 max_lines 從 {old['max_lines']} 調高到 {entry['max_lines']}")
                continue
            if self.moved.get(path) in self.base.large_files:
                continue
            self.r1(f"large_files:{path}", f"新增了 large_files 登記 `{path}`；大檔清單只能縮小")

    def exceptions(self) -> None:
        allowed: dict[tuple, set[str]] = {}
        for entry in self.base.exceptions:
            key = (entry.get("rule"), entry.get("subject"))
            allowed.setdefault(key, set()).update(entry.get("paths") or [])
        for entry in self.head.exceptions:
            key = (entry.get("rule"), entry.get("subject"))
            label = f"exceptions:{key[0]}:{key[1]}"
            if key not in allowed:
                self.r1(label, f"新增了例外 {key[0]} `{key[1]}`；例外只能減少")
                continue
            added = [p for p in entry.get("paths") or []
                     if p not in allowed[key] and self.moved.get(p) not in allowed[key]]
            if added:
                self.r1(label, f"例外 {key[0]} `{key[1]}` 新增了路徑：" + "、".join(added))


def check(repo: Path, ref: str, head_raw: dict | None, head: Config | None, head_files: list[str],
          canonical: dict, out: Findings, notes: list[str]) -> None:
    rev = merge_base(repo, ref)
    text = show(repo, rev, config.CONFIG_NAME)
    if text is None:
        notes.append(f"R1: base {rev[:12]} 沒有 ddd.toml，略過 ratchet")
        return
    if head_raw is None or head is None:
        out.error("R1", config.CONFIG_NAME, None, "ddd.toml", f"base {rev[:12]} 有 ddd.toml，目前版本卻刪掉了")
        return
    base_raw = config.read_text(text, f"{repo.name}/{config.CONFIG_NAME}@{rev[:12]}")
    base = config.parse(base_raw, canonical, Findings())
    ratchet = _Ratchet(base, head, tree_files(repo, rev), set(head_files), base_raw, out)
    ratchet.policy()
    ratchet.renames()
    ratchet.large_files()
    ratchet.exceptions()
