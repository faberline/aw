"""User guidance installation uses temporary native paths and no model calls."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from aw.mcp.cli import app
from aw.mcp.install import apply
from aw.mcp.rules import (
    AGY_FRONTMATTER, BEGIN, END, GUIDANCE, inspect_rules, prepare_rules, user_rule_paths,
)


def test_native_rule_paths_and_codex_override(tmp_path):
    paths = user_rule_paths(tmp_path, {})
    assert paths == {
        "codex": tmp_path / ".codex/AGENTS.md",
        "claude-code": tmp_path / ".claude/rules/aw.md",
        "agy": tmp_path / ".gemini/config/rules/aw.md",
    }
    override = tmp_path / ".codex/AGENTS.override.md"
    override.parent.mkdir()
    override.write_text("Temporary user guidance\n")
    assert user_rule_paths(tmp_path, {})["codex"] == override
    changed = user_rule_paths(tmp_path, {"CODEX_HOME": str(tmp_path / "codex-profile"),
                                       "CLAUDE_CONFIG_DIR": str(tmp_path / "claude-profile")})
    assert changed["codex"] == tmp_path / "codex-profile/AGENTS.md"
    assert changed["claude-code"] == tmp_path / "claude-profile/rules/aw.md"


def test_rules_preserve_user_guidance_and_have_native_formats(tmp_path):
    paths = user_rule_paths(tmp_path, {})
    original = b"Output style\r\nKeep sentences short.\r\n"
    paths["codex"].parent.mkdir()
    paths["codex"].write_bytes(original)
    for client, path in paths.items():
        plan = prepare_rules(client, path)
        backup = apply(plan)
        content = path.read_bytes()
        assert content.count(GUIDANCE.encode()) == 1
        assert inspect_rules(client, path) == "configured"
        if client == "codex":
            assert content.startswith(original)
            assert backup.read_bytes() == original
            assert backup.stat().st_mode & 0o777 == 0o600
        else:
            assert backup is None
            assert path.stat().st_mode & 0o777 == 0o600
        if client == "agy":
            assert content.startswith(AGY_FRONTMATTER.encode())
        assert not prepare_rules(client, path).changed
        assert apply(prepare_rules(client, path)) is None
        assert path.read_bytes() == content


def test_rule_update_preserves_content_outside_its_block(tmp_path):
    path = tmp_path / "AGENTS.md"
    prefix, suffix = "Existing guidance\r\n\r\n", "\n\nHuman note after the AW block.\n"
    path.write_bytes((prefix + BEGIN + "\nOld AW guidance\n" + END + suffix).encode())
    apply(prepare_rules("codex", path))
    assert path.read_bytes().startswith(prefix.encode())
    assert path.read_bytes().endswith(suffix.encode())
    assert GUIDANCE.encode() in path.read_bytes()
    assert b"Old AW guidance" not in path.read_bytes()


@pytest.mark.parametrize("content", [BEGIN, END, BEGIN + END + BEGIN + END, END + BEGIN])
def test_malformed_rule_markers_are_refused(tmp_path, content):
    path = tmp_path / "AGENTS.md"
    path.write_text(content)
    with pytest.raises(ValueError, match="markers are malformed"):
        prepare_rules("codex", path)
    assert path.read_text() == content


def test_cli_prepares_all_rules_before_writing_and_dry_run_hides_existing_text(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    paths = user_rule_paths(tmp_path, {})
    paths["codex"].parent.mkdir()
    paths["codex"].write_text("Existing private preference: do-not-echo-this\n")
    runner = CliRunner()
    preview = runner.invoke(app, ["install-rules", "--dry-run"])
    assert preview.exit_code == 0, preview.output
    assert "do-not-echo-this" not in preview.output
    assert not paths["claude-code"].exists() and not paths["agy"].exists()
    paths["agy"].parent.mkdir(parents=True)
    paths["agy"].write_text("A human-owned AW rule\n")
    refused = runner.invoke(app, ["install-rules"])
    assert refused.exit_code == 2
    assert "not managed by AW" in refused.output
    assert paths["codex"].read_text() == "Existing private preference: do-not-echo-this\n"
    assert not paths["claude-code"].exists()
