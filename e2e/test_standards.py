"""Core-reuse checks (docs/standards/core-reuse.md) on small git fixtures."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from aw.scripts import standards

CORE_GIT = 'git = "https://github.com/faberline/core"'


def git_repo(root: Path, files: dict[str, str], tags: tuple[str, ...] = ()) -> Path:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    run = lambda *a: subprocess.run(["git", "-C", str(root), *a], check=True,
                                    capture_output=True)
    run("init", "-q")
    run("add", "-A")
    run("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "fixture")
    for tag in tags:
        run("tag", tag)
    return root


def core(tmp_path: Path) -> Path:
    crates = {f"crates/{name}/Cargo.toml": f'[package]\nname = "{name}"\n'
              for name in ("cli-std", "build-stamp", "metrics-prometheus")}
    return git_repo(tmp_path / "core", crates, tags=("v0.1.0", "v0.10.0", "v0.9.3"))


def dep(name: str, tag: str = "v0.10.0") -> str:
    return f'{name} = {{ {CORE_GIT}, tag = "{tag}" }}\n'


def check(tmp_path: Path, files: dict[str, str]) -> dict:
    repo = git_repo(tmp_path / "app", files)
    report = standards.run([repo], core(tmp_path))
    return report["repos"][0]


def checks(result: dict) -> list[str]:
    return sorted(f["check"] for f in result["findings"])


def test_core_release_is_the_highest_semver_tag(tmp_path: Path) -> None:
    tag, crates = standards.core_release(core(tmp_path))
    assert tag == "v0.10.0"
    assert crates == {"cli-std", "build-stamp", "metrics-prometheus"}


def test_conforming_virtual_workspace_passes(tmp_path: Path) -> None:
    result = check(tmp_path, {
        "Cargo.toml": "[workspace]\nmembers = [\"crates/*\"]\n"
                      "[workspace.dependencies]\n" + dep("cli-std") + dep("build-stamp"),
        "crates/app/Cargo.toml": "[package]\nname = \"app\"\n[dependencies]\n"
                                 "cli-std.workspace = true\n"
                                 "[build-dependencies]\nbuild-stamp.workspace = true\n",
        "crates/app/src/main.rs": "fn main() {}\n",
        "crates/app/build.rs": 'fn main() { println!("cargo:rustc-env=APP_GIT_SHA=x"); }\n',
    })
    assert result["ok"], result


def test_each_rule_reports_its_own_violation(tmp_path: Path) -> None:
    result = check(tmp_path, {
        "Cargo.toml": "[workspace]\nmembers = [\"crates/*\"]\n"
                      "[workspace.dependencies]\n" + dep("build-stamp", "v0.9.3"),
        "crates/app/Cargo.toml": "[package]\nname = \"app\"\n[dependencies]\n"
                                 + dep("metrics-prometheus")
                                 + f'build-stamp = {{ {CORE_GIT}, branch = "main" }}\n'
                                 + 'prometheus = "0.13"\n',
        "crates/app/src/main.rs": "fn main() {}\n",
        "crates/app/build.rs": 'fn main() { println!("cargo:rustc-env=APP_GIT_SHA=x"); }\n',
    })
    assert checks(result) == ["CR1", "CR2", "CR3", "CR4", "CR4", "CR5", "CR6", "CR7"]


def test_cr4_skips_manifests_outside_the_workspace(tmp_path: Path) -> None:
    result = check(tmp_path, {
        "Cargo.toml": "[workspace]\nmembers = [\"crates/*\"]\nexclude = [\"crates/old\"]\n"
                      "[workspace.dependencies]\n" + dep("cli-std"),
        "crates/old/Cargo.toml": "[package]\nname = \"old\"\n[dependencies]\n" + dep("cli-std"),
        "tools/x/Cargo.toml": "[package]\nname = \"x\"\n[dependencies]\n" + dep("cli-std"),
    })
    assert result["ok"], result


def test_vendored_manifests_are_ignored(tmp_path: Path) -> None:
    result = check(tmp_path, {
        "Cargo.toml": "[package]\nname = \"lib\"\n",
        "src/lib.rs": "",
        "vendor/x/Cargo.toml": "[package]\nname = \"x\"\n[dependencies]\nprometheus = \"1\"\n",
    })
    assert result["ok"], result


def test_exception_with_reason_waives_one_finding(tmp_path: Path) -> None:
    result = check(tmp_path, {
        "Cargo.toml": "[package]\nname = \"app\"\n[dependencies]\n"
                      + dep("cli-std", "v0.9.3"),
        "src/main.rs": "fn main() {}\n",
        "aw.toml": "[[standards.core_reuse.exception]]\ncheck = \"CR3\"\n"
                   "reason = \"pinned until the migration lands\"\n",
    })
    assert result["ok"], result
    assert [f["check"] for f in result["excepted"]] == ["CR3"]


def test_exception_without_reason_fails(tmp_path: Path) -> None:
    result = check(tmp_path, {
        "Cargo.toml": "[package]\nname = \"lib\"\n",
        "src/lib.rs": "",
        "aw.toml": "[[standards.core_reuse.exception]]\ncheck = \"CR3\"\n",
    })
    assert checks(result) == ["CR0"]


def test_core_may_not_depend_on_another_faberline_repository(tmp_path: Path) -> None:
    root = core(tmp_path)
    extra = root / "crates/extra/Cargo.toml"
    extra.parent.mkdir()
    extra.write_text('[package]\nname = "extra"\n[dependencies]\n'
                     'keep = { git = "https://github.com/faberline/keep", tag = "v1.0.0" }\n')
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    report = standards.run([root], root)
    assert [f["check"] for f in report["repos"][0]["findings"]] == ["CR8"]


def test_cli_exit_codes_and_json(tmp_path: Path, capsys) -> None:
    repo = git_repo(tmp_path / "app", {
        "Cargo.toml": "[package]\nname = \"app\"\n",
        "src/main.rs": "fn main() {}\n",
    })
    root = core(tmp_path)
    assert standards.main(["check", "--repo", str(repo), "--core", str(root),
                           "--format", "json"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["schema"] == "core-reuse-report/1"
    assert [f["check"] for f in report["repos"][0]["findings"]] == ["CR5"]
    assert standards.main(["check", "--repo", str(tmp_path / "missing"),
                           "--core", str(root)]) == 2
