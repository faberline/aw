"""Client-specific configuration, with private backups and atomic file writes."""

from __future__ import annotations

import fcntl
import json
import os
import shutil
import stat
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
from uuid import uuid4

import tomlkit

from aw.mcp.client import DEFAULT_URL, base_url, default_state_dir
from aw.mcp.store import CLIENTS


def clients(value: str) -> list[str]:
    if value == "all":
        return ["codex", "claude-code", "agy"]
    if value not in CLIENTS:
        raise ValueError("client must be codex, claude-code, agy, or all")
    return [value]


def user_paths(home: Path, environment: Mapping[str, str]) -> dict[str, Path]:
    codex_home = Path(environment.get("CODEX_HOME") or home / ".codex")
    claude_home = environment.get("CLAUDE_CONFIG_DIR")
    return {
        "codex": codex_home / "config.toml",
        "claude-code": Path(claude_home) / ".claude.json" if claude_home else home / ".claude.json",
        "agy": home / ".gemini" / "config" / "mcp_config.json",
    }


def config_path(client: str, scope: str, worktree: Path | None = None,
                override: Path | None = None) -> Path:
    if client not in CLIENTS:
        raise ValueError("config path requires one client")
    if scope not in {"user", "project"}:
        raise ValueError("scope must be user or project")
    if override is not None:
        return override.expanduser().absolute()
    if scope == "user":
        if worktree is not None:
            raise ValueError("--worktree requires --scope project")
        path = user_paths(Path.home(), os.environ)[client]
    else:
        if worktree is None or not worktree.is_dir():
            raise ValueError("project scope requires --worktree with an existing directory")
        relative = {
            "codex": ".codex/config.toml", "claude-code": ".mcp.json", "agy": ".agents/mcp_config.json",
        }[client]
        path = worktree.resolve() / relative
    return path.expanduser().absolute()


def entry(client: str, url: str, state_dir: Path) -> dict:
    """Use a local proxy so native config files contain no service credentials."""
    base_url(url)
    result = {
        # Preserve the venv path. Resolving its symlink loses the AW installation.
        "command": str(Path(sys.executable).absolute()),
        "args": ["-m", "aw.main", "mcp", "proxy", "--url", url,
                 "--state-dir", str(state_dir.expanduser().absolute())],
    }
    if client == "claude-code":
        result["type"] = "stdio"
    return result


@dataclass(frozen=True)
class Snapshot:
    content: bytes
    device: int
    inode: int
    modified: int
    mode: int


def read_config(path: Path) -> Snapshot | None:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise ValueError(f"config must be an owned regular file: {path}")
        with os.fdopen(fd, "rb") as source:
            fd = -1
            content = source.read(4 * 1024 * 1024 + 1)
        if len(content) > 4 * 1024 * 1024:
            raise ValueError(f"config exceeds 4 MiB: {path}")
        return Snapshot(content, info.st_dev, info.st_ino, info.st_mtime_ns, stat.S_IMODE(info.st_mode))
    finally:
        if fd != -1:
            os.close(fd)


def document(client: str, snapshot: Snapshot | None, path: Path):
    try:
        text = snapshot.content.decode("utf-8") if snapshot is not None else ""
        result = tomlkit.parse(text) if client == "codex" else json.loads(text or "{}")
        if not isinstance(result, dict):
            raise ValueError("config root is not an object")
        return result
    except (ValueError, TypeError):
        # Config files can contain other credentials. Do not echo parser context.
        raise ValueError(f"invalid {'TOML' if client == 'codex' else 'JSON'} config: {path}") from None


@dataclass
class Plan:
    client: str
    path: Path
    snapshot: Snapshot | None
    content: bytes
    entry: dict
    changed: bool


def prepare(client: str, path: Path, settings: dict, replace: bool = False) -> Plan:
    snapshot = read_config(path)
    doc = document(client, snapshot, path)
    key = "mcp_servers" if client == "codex" else "mcpServers"
    servers = doc.get(key)
    if servers is None and key not in doc:
        servers = tomlkit.table() if client == "codex" else {}
        doc[key] = servers
    if not isinstance(servers, dict):
        raise ValueError(f"{key} must be an object: {path}")
    previous = servers.get("aw")
    if previous == settings:
        return Plan(client, path, snapshot, snapshot.content, settings, False)
    if "aw" in servers and not replace:
        raise ValueError(f"existing aw entry differs: {path}; use --replace to replace only that entry")
    servers["aw"] = settings
    text = tomlkit.dumps(doc) if client == "codex" else json.dumps(doc, ensure_ascii=False, indent=2) + "\n"
    return Plan(client, path, snapshot, text.encode(), settings, True)


def apply(plan: Plan) -> Path | None:
    """Write one file. Refuse a changed snapshot before replacing its bytes."""
    if not plan.changed:
        return None
    path = plan.path
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = os.open(path.with_name(path.name + ".aw-install.lock"),
                   os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    temporary = None
    try:
        info = os.fstat(lock)
        if info.st_uid != os.getuid() or not stat.S_ISREG(info.st_mode):
            raise ValueError("installer lock must be an owned regular file")
        fcntl.flock(lock, fcntl.LOCK_EX)
        if read_config(path) != plan.snapshot:
            raise ValueError(f"config changed during installation; retry: {path}")
        fd, filename = tempfile.mkstemp(prefix=path.name + ".aw-", dir=path.parent)
        temporary = Path(filename)
        with os.fdopen(fd, "wb") as target:
            target.write(plan.content)
            target.flush()
            os.fsync(target.fileno())
            if plan.snapshot is not None:
                os.fchmod(target.fileno(), plan.snapshot.mode)
        backup = None
        if plan.snapshot is not None:
            backup = path.with_name(path.name + ".aw-backup-" + str(uuid4()))
            backup_fd = os.open(backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
            with os.fdopen(backup_fd, "wb") as target:
                target.write(plan.snapshot.content)
                target.flush()
                os.fsync(target.fileno())
        if read_config(path) != plan.snapshot:
            raise ValueError(f"config changed during installation; retry: {path}")
        os.replace(temporary, path)
        temporary = None
        return backup
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        os.close(lock)


def inspect(client: str, path: Path) -> str:
    snapshot = read_config(path)
    if snapshot is None:
        return "missing"
    doc = document(client, snapshot, path)
    key = "mcp_servers" if client == "codex" else "mcpServers"
    servers = doc.get(key, {})
    if not isinstance(servers, dict):
        raise ValueError(f"{key} must be an object: {path}")
    server = servers.get("aw")
    if server is None:
        return "missing"
    if not isinstance(server, dict):
        return "invalid entry"
    if server.get("disabled") or server.get("enabled") is False:
        return "disabled"
    if server.get("command") and isinstance(server.get("args"), list):
        args = server["args"]
        if not all(isinstance(arg, str) for arg in args):
            return "invalid entry"
        if args[:4] == ["-m", "aw.main", "mcp", "proxy"] or args[:2] == ["mcp", "proxy"]:
            if not isinstance(server["command"], str):
                return "invalid entry"
            if shutil.which(server["command"]) is None:
                return "command missing"
            return "configured"
    return "different entry"


def connection(client: str, path: Path) -> tuple[str, Path]:
    """Read only connection options from a known proxy declaration."""
    doc = document(client, read_config(path), path)
    key = "mcp_servers" if client == "codex" else "mcpServers"
    args = doc[key]["aw"]["args"]

    def option(name, default):
        if name not in args:
            return default
        index = args.index(name)
        if index + 1 == len(args) or not isinstance(args[index + 1], str):
            raise ValueError(f"invalid proxy connection options: {path}")
        return args[index + 1]

    url = option("--url", DEFAULT_URL)
    base_url(url)
    state = Path(option("--state-dir", str(default_state_dir()))).expanduser().absolute()
    return url, state
