"""Text and JSON (rust-arch-report/1) rendering."""

from __future__ import annotations

import json

from .model import ERROR, RepoReport

SCHEMA = "rust-arch-report/1"


def to_json(root: str, base: str | None, mode: str, exit_code: int, reports: list[RepoReport]) -> str:
    payload = {
        "schema": SCHEMA,
        "root": root,
        "base": base,
        "mode": mode,
        "exit_code": exit_code,
        "repos": [r.to_json() for r in reports],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def _d2(value: bool | None) -> str:
    return "-" if value is None else ("yes" if value else "no")


def _rules(report: RepoReport) -> str:
    parts = []
    for rule, slot in report.counts()["by_rule"].items():
        parts.append(f"{rule}={slot['error']}" + (f"+{slot['warning']}w" if slot["warning"] else ""))
    return " ".join(parts) or "-"


def _table(rows: list[list[str]]) -> list[str]:
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]))]
    return ["  ".join(cell.ljust(width) for cell, width in zip(row, widths)).rstrip() for row in rows]


def to_text(mode: str, reports: list[RepoReport], top: int, verbose: bool) -> str:
    rows = [["repo", "status", "layout", "D2", "error", "warn", ">size_error", "largest", "rules"]]
    for report in reports:
        counts = report.counts()
        large = report.large_files
        biggest = large.get("top") or []
        rows.append([
            report.repo,
            report.status,
            report.layout,
            _d2(report.d2),
            str(counts["error"]) if report.language == "rust" else "-",
            str(counts["warning"]) if report.language == "rust" else "-",
            str(large.get("over_error", "-")),
            f"{biggest[0]['path']} ({biggest[0]['lines']})" if biggest else "-",
            _rules(report) if report.language == "rust" else "-",
        ])
    lines = [f"rust-arch check（{mode}）", "", *_table(rows)]
    for report in reports:
        active = report.active()
        suppressed = len(report.findings) - len(active)
        if not active and not report.notes:
            continue
        lines += ["", f"## {report.repo}（{report.status}）"]
        lines += [f"  note: {note}" for note in report.notes]
        if suppressed:
            lines.append(f"  已由 exceptions 豁免 {suppressed} 筆")
        by_rule: dict[str, list] = {}
        for finding in active:
            by_rule.setdefault(finding.rule, []).append(finding)
        for rule, items in by_rule.items():
            items.sort(key=lambda f: (f.severity != ERROR, f.path, f.line or 0))
            lines.append(f"  {rule}：{len(items)} 筆")
            shown = items if verbose else items[:top]
            for finding in shown:
                where = finding.path + (f":{finding.line}" if finding.line else "")
                mark = "E" if finding.severity == ERROR else "W"
                lines.append(f"    {mark} {where} — {finding.message}")
            if len(shown) < len(items):
                lines.append(f"    …還有 {len(items) - len(shown)} 筆（加 --verbose 看全部）")
    return "\n".join(lines) + "\n"
