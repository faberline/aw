"""Findings, severities, and per-repo report records."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path

import tomllib

ERROR = "error"
WARNING = "warning"
POLICY_PATH = Path(__file__).with_name("policy.toml")
SUPPRESSIBLE = frozenset({"B1", "B2", "B3", "B4", "B5", "B6"})


class ToolError(Exception):
    """A tool or configuration failure that maps to exit code 2."""


def load_policy(path: Path = POLICY_PATH) -> dict:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))["policy"]
    except (OSError, tomllib.TOMLDecodeError, KeyError) as exc:
        raise ToolError(f"cannot load canonical policy {path}: {exc}") from exc


@dataclass
class Finding:
    rule: str
    severity: str
    path: str
    line: int | None
    subject: str
    message: str
    suppressed_by: str | None = None
    count: int = 1

    def to_json(self) -> dict:
        return {
            "rule": self.rule,
            "severity": self.severity,
            "path": self.path,
            "line": self.line,
            "subject": self.subject,
            "message": self.message,
            "suppressed_by": self.suppressed_by,
        }


class Findings:
    """Collects findings, folding repeats of one (rule, path, subject)."""

    def __init__(self) -> None:
        self._by_key: dict[tuple[str, str, str], Finding] = {}

    def add(
        self,
        rule: str,
        severity: str,
        path: str,
        line: int | None,
        subject: str,
        message: str,
    ) -> None:
        key = (rule, path, subject)
        seen = self._by_key.get(key)
        if seen is None:
            self._by_key[key] = Finding(rule, severity, path, line, subject, message)
            return
        seen.count += 1
        if line is not None and (seen.line is None or line < seen.line):
            seen.line = line

    def error(self, rule: str, path: str, line: int | None, subject: str, message: str) -> None:
        self.add(rule, ERROR, path, line, subject, message)

    def warn(self, rule: str, path: str, line: int | None, subject: str, message: str) -> None:
        self.add(rule, WARNING, path, line, subject, message)

    def items(self) -> list[Finding]:
        out = []
        for finding in self._by_key.values():
            if finding.count > 1:
                finding = replace(finding, message=f"{finding.message}（共 {finding.count} 處）")
            out.append(finding)
        return sorted(out, key=lambda f: (f.rule, f.path, f.line or 0, f.subject))


def apply_exceptions(findings: list[Finding], exceptions: list[dict]) -> None:
    for index, entry in enumerate(exceptions):
        paths = set(entry.get("paths", []))
        for finding in findings:
            if (
                finding.suppressed_by is None
                and finding.rule in SUPPRESSIBLE
                and finding.rule == entry.get("rule")
                and finding.subject == entry.get("subject")
                and finding.path in paths
            ):
                finding.suppressed_by = f"exceptions[{index}]"


@dataclass
class RepoReport:
    repo: str
    path: str
    head: str | None = None
    dirty: bool = False
    language: str = "n/a"
    layout: str = "n/a"
    config: str = "none"
    enforced: bool = False
    status: str = "n/a"
    d2: bool | None = None
    findings: list[Finding] = field(default_factory=list)
    large_files: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def active(self) -> list[Finding]:
        return [f for f in self.findings if f.suppressed_by is None]

    def counts(self) -> dict:
        by_rule: dict[str, dict[str, int]] = {}
        errors = warnings = 0
        for finding in self.active():
            slot = by_rule.setdefault(finding.rule, {"error": 0, "warning": 0})
            slot[finding.severity] += 1
            if finding.severity == ERROR:
                errors += 1
            else:
                warnings += 1
        return {"error": errors, "warning": warnings, "by_rule": dict(sorted(by_rule.items()))}

    def to_json(self) -> dict:
        return {
            "repo": self.repo,
            "path": self.path,
            "head": self.head,
            "dirty": self.dirty,
            "language": self.language,
            "layout": self.layout,
            "config": self.config,
            "enforced": self.enforced,
            "status": self.status,
            "d2": self.d2,
            "counts": self.counts(),
            "large_files": self.large_files,
            "notes": self.notes,
            "findings": [f.to_json() for f in self.findings],
        }
