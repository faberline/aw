"""SaaS service skeleton checks (docs/standards/saas-service.md) on small git fixtures."""

from __future__ import annotations

import subprocess
from pathlib import Path

from aw.scripts import saas_service

SLOT_DEPS = {"access": "service-auth", "replication": "raft-runtime", "operator": "kube"}


def git_repo(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    run = lambda *a: subprocess.run(["git", "-C", str(root), *a], check=True,
                                    capture_output=True)
    run("init", "-q")
    run("add", "-A")
    run("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "fixture")
    return root


def package(name: str, deps: tuple[str, ...] = ()) -> str:
    body = f'[package]\nname = "{name}"\n[dependencies]\n'
    return body + "".join(f"{d}.workspace = true\n" for d in deps)


def context(name: str, pkg: str) -> str:
    return (f'[[contexts]]\nname = "{name}"\n[contexts.layers]\n'
            f'domain = "crates/{pkg}/src/domain"\n')


def skeleton(products: tuple[str, ...] = ("queue",)) -> dict[str, str]:
    files = {
        "aw.toml": '[project]\nname = "svc"\nkind = "saas-service"\n',
        "Cargo.toml": '[workspace]\nmembers = ["crates/*"]\n',
        "clients/codegen.toml": "",
        "k8s/base/kustomization.yaml": "",
        "k8s/operator/kustomization.yaml": "",
        "crates/svc/Cargo.toml": package("svc"),
        "crates/svc-shared-kernel/Cargo.toml": package("svc-shared-kernel"),
        "crates/svc-client/Cargo.toml": package("svc-client"),
    }
    for overlay in saas_service.OVERLAYS:
        files[f"k8s/overlays/{overlay}/kustomization.yaml"] = ""
    ddd = '[shared_kernel]\npath = "crates/svc-shared-kernel/src"\n'
    for name in ("access", "replication", "storage", "operator", *products):
        pkg = f"svc-{name}"
        files[f"crates/{pkg}/Cargo.toml"] = package(
            pkg, (SLOT_DEPS[name],) if name in SLOT_DEPS else ())
        ddd += context(name, pkg)
    files["ddd.toml"] = ddd
    return files


def check(tmp_path: Path, files: dict[str, str]) -> dict:
    return saas_service.run([git_repo(tmp_path / "svc", files)])["repos"][0]


def found(result: dict) -> list[tuple[str, str]]:
    return sorted((f["check"], f["message"]) for f in result["findings"])


def test_full_skeleton_passes(tmp_path: Path) -> None:
    result = check(tmp_path, skeleton())
    assert result["ok"], result["findings"]


def test_missing_kind_and_storage_slot_fail(tmp_path: Path) -> None:
    files = skeleton()
    files["aw.toml"] = '[project]\nname = "svc"\n'
    del files["crates/svc-storage/Cargo.toml"]
    files["ddd.toml"] = files["ddd.toml"].replace(context("storage", "svc-storage"), "")
    assert found(check(tmp_path, files)) == [
        ("SV1", 'set [project] kind = "saas-service"'),
        ("SV3", "add package svc-storage"),
    ]


def test_layer_named_packages_are_rejected(tmp_path: Path) -> None:
    files = skeleton()
    files["crates/svc-http/Cargo.toml"] = package("svc-http")
    files["crates/svc-core/Cargo.toml"] = package("svc-core")
    assert found(check(tmp_path, files)) == [
        ("SV4", "svc-core is not a slot or a ddd.toml context"),
        ("SV4", "svc-http is not a slot or a ddd.toml context"),
    ]


def test_more_than_three_product_contexts_fail(tmp_path: Path) -> None:
    result = check(tmp_path, skeleton(("a", "b", "c", "d")))
    assert found(result) == [("SV4", "4 product contexts; at most 3")]


def test_slot_without_its_library_fails(tmp_path: Path) -> None:
    files = skeleton()
    files["crates/svc-replication/Cargo.toml"] = package("svc-replication")
    assert found(check(tmp_path, files)) == [
        ("SV5", "svc-replication does not depend on raft-runtime"),
    ]


def test_renamed_dependency_counts_by_package(tmp_path: Path) -> None:
    files = skeleton()
    files["crates/svc-access/Cargo.toml"] = (
        '[package]\nname = "svc-access"\n[dependencies]\n'
        'auth = { package = "service-auth", workspace = true }\n')
    assert check(tmp_path, files)["ok"]


def test_context_paths_must_stay_in_their_package(tmp_path: Path) -> None:
    files = skeleton()
    files["ddd.toml"] = files["ddd.toml"].replace(
        'domain = "crates/svc-queue/src/domain"', 'domain = "src/queue"')
    assert found(check(tmp_path, files)) == [
        ("SV6", "context queue layer paths must be in crates/svc-queue/"),
    ]


def test_single_package_repo_fails_layout(tmp_path: Path) -> None:
    files = {k: v for k, v in skeleton().items() if not k.startswith("crates/")}
    files["Cargo.toml"] = '[package]\nname = "svc"\n'
    checks = {c for c, _ in found(check(tmp_path, files))}
    assert checks == {"SV2", "SV3"}


def test_missing_overlay_and_codegen_fail(tmp_path: Path) -> None:
    files = skeleton()
    del files["k8s/overlays/gke-test/kustomization.yaml"]
    del files["clients/codegen.toml"]
    assert [(f["check"], f["path"]) for f in check(tmp_path, files)["findings"]] == [
        ("SV7", "k8s/overlays/gke-test/kustomization.yaml"),
        ("SV8", "clients/codegen.toml"),
    ]


def test_exception_waives_one_finding_and_needs_a_reason(tmp_path: Path) -> None:
    files = skeleton()
    del files["clients/codegen.toml"]
    files["aw.toml"] += ('[[standards.saas_service.exception]]\ncheck = "SV8"\n'
                         'reason = "no public API yet"\n'
                         '[[standards.saas_service.exception]]\ncheck = "SV9"\n'
                         'reason = "unknown"\n')
    result = check(tmp_path, files)
    assert found(result) == [("SV0", "exception 2 needs a known check and a reason")]
    assert [f["check"] for f in result["excepted"]] == ["SV8"]


def test_cli_exit_codes(tmp_path: Path, capsys) -> None:
    good = git_repo(tmp_path / "good", skeleton())
    assert saas_service.main(["check", "--repo", str(good)]) == 0
    files = skeleton()
    del files["clients/codegen.toml"]
    bad = git_repo(tmp_path / "bad", files)
    assert saas_service.main(["check", "--repo", str(bad), "--format", "json"]) == 1
    assert saas_service.main(["check", "--repo", str(tmp_path / "none")]) == 2
    capsys.readouterr()
