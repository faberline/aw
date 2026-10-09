"""Install in temporary native configs. Never change the operator's setup."""

from __future__ import annotations

import json
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from aw.mcp.install import apply, config_path, entry, prepare, user_paths

ROOT = Path(__file__).resolve().parents[1]


def cli(*arguments):
    return subprocess.run([sys.executable, "-m", "aw.main", "mcp", *map(str, arguments)],
                          cwd=ROOT, capture_output=True, text=True, timeout=15)


def native_configs(worktree):
    return {"codex": worktree / ".codex/config.toml", "claude-code": worktree / ".mcp.json",
            "agy": worktree / ".agents/mcp_config.json"}


def test_user_and_project_paths_match_native_clients(tmp_path):
    assert user_paths(tmp_path, {}) == {
        "codex": tmp_path / ".codex/config.toml", "claude-code": tmp_path / ".claude.json",
        "agy": tmp_path / ".gemini/config/mcp_config.json",
    }
    overridden = user_paths(tmp_path, {"CODEX_HOME": str(tmp_path / "codex-home"),
                                      "CLAUDE_CONFIG_DIR": str(tmp_path / "claude-home")})
    assert overridden["codex"] == tmp_path / "codex-home/config.toml"
    assert overridden["claude-code"] == tmp_path / "claude-home/.claude.json"
    for client, path in native_configs(tmp_path).items():
        assert config_path(client, "project", tmp_path) == path


def test_install_preserves_other_settings_and_private_exact_backups(tmp_path):
    paths = native_configs(tmp_path)
    originals = {
        "codex": b'# Keep this comment.\nmodel = "example-model"\n[mcp_servers.other]\ncommand = "other" # Keep inline comment.\n',
        "claude-code": b'{"preferences":{"otherSecret":"unrelated-private-value"},"mcpServers":{"other":{"command":"other"}}}\r\n',
        "agy": b'{"mcpServers":{"other":{"command":"other","disabledTools":["write"]}},"otherSetting":42}\n',
    }
    for client, path in paths.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(originals[client])
    args = ["install", "--client", "all", "--scope", "project", "--worktree", tmp_path,
            "--state-dir", tmp_path / "state"]
    first = cli(*args)
    assert first.returncode == 0, first.stderr
    assert "unrelated-private-value" not in first.stdout + first.stderr
    before_repeat = {}
    for client, path in paths.items():
        before_repeat[client] = path.read_bytes()
        doc = tomllib.loads(path.read_text()) if client == "codex" else json.loads(path.read_text())
        original = tomllib.loads(originals[client].decode()) if client == "codex" else json.loads(originals[client])
        key = "mcp_servers" if client == "codex" else "mcpServers"
        installed = doc[key].pop("aw")
        assert doc == original
        assert installed["command"] == sys.executable
        assert installed["args"][:4] == ["-m", "aw.main", "mcp", "proxy"]
        assert "headers" not in installed and "env" not in installed
        backups = list(path.parent.glob(path.name + ".aw-backup-*"))
        assert len(backups) == 1 and backups[0].read_bytes() == originals[client]
        assert backups[0].stat().st_mode & 0o777 == 0o600
    assert b"# Keep this comment." in paths["codex"].read_bytes()
    assert b"# Keep inline comment." in paths["codex"].read_bytes()
    second = cli(*args)
    assert second.returncode == 0, second.stderr
    for client, path in paths.items():
        assert path.read_bytes() == before_repeat[client]
        assert len(list(path.parent.glob(path.name + ".aw-backup-*"))) == 1


def test_dry_run_writes_nothing_and_shows_no_existing_credentials(tmp_path):
    path = tmp_path / "native.json"
    original = '{"otherSecret":"do-not-print-this"}'
    path.write_text(original)
    result = cli("install", "--client", "agy", "--config-file", path, "--dry-run")
    assert result.returncode == 0, result.stderr
    assert "would update" in result.stdout
    assert "do-not-print-this" not in result.stdout + result.stderr
    assert path.read_text() == original
    assert set(tmp_path.iterdir()) == {path}
    missing = tmp_path / "new-parent/new.json"
    result = cli("install", "--client", "agy", "--config-file", missing, "--dry-run")
    assert result.returncode == 0
    assert not missing.parent.exists()


def test_conflicting_aw_requires_explicit_replace_and_keeps_other_entries(tmp_path):
    path = tmp_path / "native.json"
    original = {"mcpServers": {"aw": {"command": "different"}, "other": {"command": "other"}}}
    path.write_text(json.dumps(original))
    args = ["install", "--client", "claude-code", "--config-file", path]
    refused = cli(*args)
    assert refused.returncode == 2 and "existing aw entry differs" in refused.stderr
    assert json.loads(path.read_text()) == original
    accepted = cli(*args, "--replace")
    assert accepted.returncode == 0, accepted.stderr
    updated = json.loads(path.read_text())
    assert updated["mcpServers"]["aw"]["type"] == "stdio"
    assert updated["mcpServers"]["other"] == original["mcpServers"]["other"]


def test_all_clients_are_validated_before_any_config_is_written(tmp_path):
    paths = native_configs(tmp_path)
    paths["agy"].parent.mkdir(parents=True)
    paths["agy"].write_text('{"broken": secret-parser-context}')
    result = cli("install", "--client", "all", "--scope", "project", "--worktree", tmp_path)
    assert result.returncode == 2
    assert "invalid JSON config" in result.stderr
    assert "secret-parser-context" not in result.stdout + result.stderr
    assert not paths["codex"].exists() and not paths["claude-code"].exists()


def test_install_refuses_a_config_changed_after_preparation(tmp_path):
    path = tmp_path / "native.json"
    path.write_text("{}")
    plan = prepare("agy", path, entry("agy", "http://127.0.0.1:8765/mcp", tmp_path / "state"))
    path.write_text('{"concurrentWriter":true}')
    with pytest.raises(ValueError, match="changed during installation"):
        apply(plan)
    assert json.loads(path.read_text()) == {"concurrentWriter": True}
    assert not list(tmp_path.glob("*.aw-backup-*"))


def test_install_refuses_symlink_configs(tmp_path):
    target, link = tmp_path / "target.json", tmp_path / "link.json"
    target.write_text("{}")
    link.symlink_to(target)
    result = cli("install", "--client", "agy", "--config-file", link)
    assert result.returncode == 2 and target.read_text() == "{}"
