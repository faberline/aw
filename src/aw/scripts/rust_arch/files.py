"""Git-backed file inventory and the shared line-count definition."""

from __future__ import annotations

import subprocess
from pathlib import Path

from .model import ToolError

GIT = ("git", "--no-optional-locks", "-c", "core.fsmonitor=false")


def git(repo: Path, *args: str, check: bool = True) -> str | None:
    proc = subprocess.run(
        [*GIT, "-C", str(repo), *args],
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        if check:
            message = proc.stderr.decode("utf-8", "replace").strip()
            raise ToolError(f"git {' '.join(args)} failed in {repo}: {message}")
        return None
    return proc.stdout.decode("utf-8", "replace")


def is_vendored(path: str) -> bool:
    return path.startswith("vendor/") or "/vendor/" in path


def repo_files(repo: Path) -> list[str]:
    """Tracked plus untracked-not-ignored files, minus deleted and vendor/."""
    listed = git(repo, "ls-files", "-co", "--exclude-standard", "-z") or ""
    deleted = set((git(repo, "ls-files", "-d", "-z") or "").split("\0"))
    out = set()
    for path in listed.split("\0"):
        if not path or path in deleted or is_vendored(path):
            continue
        if (repo / path).is_file():
            out.add(path)
    return sorted(out)


def count_lines(data: bytes) -> int:
    """Number of newlines, plus one when a non-empty file lacks a final newline."""
    lines = data.count(b"\n")
    if data and not data.endswith(b"\n"):
        lines += 1
    return lines


def file_lines(repo: Path, path: str) -> int:
    return count_lines((repo / path).read_bytes())


def head(repo: Path) -> str | None:
    out = git(repo, "rev-parse", "HEAD", check=False)
    return out.strip() if out else None


def is_dirty(repo: Path) -> bool:
    return bool((git(repo, "status", "--porcelain", check=False) or "").strip())


def merge_base(repo: Path, ref: str) -> str:
    out = git(repo, "merge-base", "HEAD", ref, check=False)
    if not out:
        raise ToolError(f"{repo.name}: cannot find merge-base of HEAD and {ref}")
    return out.strip()


def show(repo: Path, rev: str, path: str) -> str | None:
    return git(repo, "show", f"{rev}:{path}", check=False)


def tree_files(repo: Path, rev: str) -> set[str]:
    out = git(repo, "ls-tree", "-r", "--name-only", "-z", rev) or ""
    return {p for p in out.split("\0") if p and not is_vendored(p)}


def is_git_repo(path: Path) -> bool:
    return (path / ".git").exists()
