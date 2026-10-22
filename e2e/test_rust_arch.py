"""S3 rust-arch checks (docs/standards/rust-layout.md) on small git fixtures."""

from __future__ import annotations

import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from aw.scripts.rust_arch import cli
from aw.scripts.rust_arch.files import count_lines
from aw.scripts.rust_arch.model import POLICY_PATH, load_policy

POLICY = POLICY_PATH.read_text(encoding="utf-8")
POLICY = POLICY[POLICY.index("\n[policy]") + 1:]
DOCS = {
    "README.md": "# demo\n",
    "docs/README.md": "# docs\n",
    "docs/architecture.md": "# architecture\n",
    "docs/glossary.md": "# glossary\n",
    "docs/domain/README.md": "# domain\n",
    "docs/adr/README.md": "# adr\n",
    "docs/operations/README.md": "# operations\n",
}
WORKSPACE = """[workspace]
resolver = "2"
members = ["crates/*"]

[workspace.package]
version = "0.1.0"
edition = "2021"

[workspace.dependencies]
serde = "1"

[workspace.lints.clippy]
mod_module_files = "deny"
"""
LAYERS = ("domain", "application", "infrastructure", "interfaces")


def member(name: str, deps: str = "") -> str:
    return (
        f'[package]\nname = "{name}"\nversion.workspace = true\nedition.workspace = true\n\n'
        f"[lints]\nworkspace = true\n\n[dependencies]\n{deps}"
    )


def context(name: str, depends: str, layers: dict[str, str]) -> str:
    body = "".join(f'{layer} = "{path}"\n' for layer, path in layers.items())
    return f'\n[[contexts]]\nname = "{name}"\ndepends_on = [{depends}]\n[contexts.layers]\n{body}'


def ddd(body: str, enforce: bool = True) -> str:
    return f"schema = 1\n\n[migration]\nenforce = {str(enforce).lower()}\n\n{POLICY}\n{body}"


def module_form() -> dict[str, str]:
    """Layer form (a): each context crate holds its layers as modules."""
    billing = "crates/demo-billing/src"
    orders = "crates/demo-orders/src"
    return {
        **DOCS,
        "Cargo.toml": WORKSPACE,
        "crates/demo-shared-kernel/Cargo.toml": member("demo-shared-kernel"),
        "crates/demo-shared-kernel/src/lib.rs": "pub mod money;\n",
        "crates/demo-shared-kernel/src/money.rs": "pub struct Money(pub i64);\n",
        "crates/demo-billing/Cargo.toml": member(
            "demo-billing", 'demo-shared-kernel = { path = "../demo-shared-kernel" }\nserde = { workspace = true }\n'
        ),
        f"{billing}/lib.rs": "".join(f"pub mod {layer};\n" for layer in LAYERS),
        f"{billing}/domain.rs": "use demo_shared_kernel::money::Money;\n\npub struct Invoice {\n    pub total: Money,\n}\n",
        f"{billing}/application.rs": (
            "use crate::domain::Invoice;\nuse demo_shared_kernel::money::Money;\n\npub struct Invoicer;\n\n"
            "impl Invoicer {\n    pub fn issue(&self) -> Invoice {\n        Invoice { total: Money(0) }\n    }\n}\n"
        ),
        f"{billing}/infrastructure.rs": "use crate::domain::Invoice;\n\npub fn store(_invoice: &Invoice) {}\n",
        f"{billing}/interfaces.rs": "use crate::application::Invoicer;\n\npub fn serve(_invoicer: Invoicer) {}\n",
        "crates/demo-orders/Cargo.toml": member("demo-orders", 'demo-billing = { path = "../demo-billing" }\n'),
        f"{orders}/lib.rs": "pub mod domain;\npub mod application;\n",
        f"{orders}/domain.rs": "pub struct Order;\n",
        f"{orders}/application.rs": "use demo_billing::application::Invoicer;\n\npub fn checkout(_invoicer: &Invoicer) {}\n",
        "ddd.toml": ddd(
            '[shared_kernel]\npath = "crates/demo-shared-kernel/src"\n'
            + context("billing", "", {layer: f"{billing}/{layer}" for layer in LAYERS})
            + context("orders", '"billing"', {"domain": f"{orders}/domain", "application": f"{orders}/application"})
        ),
    }


def crate_form() -> dict[str, str]:
    """Layer form (b): billing's domain is its own crate."""
    files = module_form()
    del files["crates/demo-billing/src/domain.rs"]
    files["crates/demo-billing/src/lib.rs"] = "pub mod application;\npub mod infrastructure;\npub mod interfaces;\n"
    files["crates/demo-billing-domain/Cargo.toml"] = member(
        "demo-billing-domain", 'demo-shared-kernel = { path = "../demo-shared-kernel" }\nserde = { workspace = true }\n'
    )
    files["crates/demo-billing-domain/src/lib.rs"] = "pub mod invoice;\n"
    files["crates/demo-billing-domain/src/invoice.rs"] = module_form()["crates/demo-billing/src/domain.rs"]
    files["crates/demo-billing/Cargo.toml"] += 'demo-billing-domain = { path = "../demo-billing-domain" }\n'
    for layer in ("application", "infrastructure"):
        path = f"crates/demo-billing/src/{layer}.rs"
        files[path] = files[path].replace("use crate::domain::Invoice;", "use demo_billing_domain::invoice::Invoice;")
    files["ddd.toml"] = files["ddd.toml"].replace(
        'domain = "crates/demo-billing/src/domain"', 'domain = "crates/demo-billing-domain/src"'
    )
    return files


def single_crate() -> dict[str, str]:
    return {
        **DOCS,
        "Cargo.toml": '[package]\nname = "demo"\nversion = "0.1.0"\nedition = "2021"\n\n'
                      '[lints.clippy]\nmod_module_files = "deny"\n',
        "src/lib.rs": "pub mod alpha;\n",
        "src/alpha.rs": "pub fn alpha() {}\n",
    }


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=test", "-c", "user.email=test@example.com", "-c", "commit.gpgsign=false",
         "-C", str(repo), *args],
        check=True, capture_output=True, text=True,
    ).stdout.strip()


class Fixture(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name) / "demo"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def write(self, files: dict[str, str]) -> Path:
        for path, text in files.items():
            target = self.repo / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        if not (self.repo / ".git").exists():
            git(self.repo, "init", "-q")
        return self.repo

    def check(self, files: dict[str, str] | None = None, *args: str) -> tuple[int, dict]:
        if files is not None:
            self.write(files)
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = cli.main(["check", "--repo", str(self.repo), "--json", "-", *args])
        payload = json.loads(buffer.getvalue())
        self.assertEqual(payload["schema"], "rust-arch-report/1")
        self.assertEqual(payload["exit_code"], code)
        return code, payload["repos"][0]

    def found(self, repo: dict) -> set[tuple[str, str]]:
        return {(f["rule"], f["subject"]) for f in repo["findings"] if f["suppressed_by"] is None}

    def assertClean(self, files: dict[str, str]) -> None:
        code, repo = self.check(files)
        self.assertEqual(repo["findings"], [], repo["findings"])
        self.assertEqual((code, repo["status"], repo["d2"]), (0, "pass", True))


class LineCountTest(unittest.TestCase):
    def test_counts_newlines_plus_unterminated_tail(self) -> None:
        self.assertEqual([count_lines(b""), count_lines(b"a"), count_lines(b"a\n"), count_lines(b"a\nb")], [0, 1, 1, 2])


class CleanRepoTest(Fixture):
    def test_module_form_passes(self) -> None:
        self.assertClean(module_form())

    def test_crate_form_passes(self) -> None:
        self.assertClean(crate_form())


class LayoutRulesTest(Fixture):
    def test_a1_tests_layout(self) -> None:
        files = module_form()
        files["crates/demo-billing/tests/loose.rs"] = "#[test]\nfn loose() {}\n"
        files["crates/demo-billing/tests/env.rs"] = '#[test]\nfn env() {\n    std::env::set_var("A", "1");\n}\n'
        files["crates/demo-orders/tests/it/smoke.rs"] = "#[test]\nfn smoke() {}\n"
        files["crates/demo-orders/tests/it/e2e/suite.rs"] = "#[test]\nfn suite() {}\n"
        files["e2e/run.sh"] = "true\n"
        found = self.check(files)[1]
        rules = {(f["rule"], f["path"]) for f in found["findings"]}
        self.assertIn(("A1", "crates/demo-billing/tests/loose.rs"), rules)
        self.assertNotIn(("A1", "crates/demo-billing/tests/env.rs"), rules)
        self.assertIn(("A1", "crates/demo-orders/tests/it/"), rules)
        self.assertIn(("A1", "e2e/"), rules)
        # rust-tests.md: e2e suites are modules of the tests/it binary.
        self.assertNotIn(("A1", "crates/demo-orders/tests/it/e2e/"), rules)

    def test_a1_standalone_test_exceptions(self) -> None:
        files = module_form()
        billing = "crates/demo-billing/tests"
        files["crates/demo-shared-kernel/tests/only.rs"] = "#[test]\nfn only() {}\n"
        files["crates/demo-shared-kernel/tests/fixtures/case.rs"] = "fn main() {}\n"
        files[f"{billing}/it/main.rs"] = "mod smoke;\n"
        files[f"{billing}/it/smoke.rs"] = "#[test]\nfn smoke() {}\n"
        files[f"{billing}/alloc.rs"] = "//! Counts allocations.\n//!\n//! isolation: installs a #[global_allocator]\n\n#[test]\nfn a() {}\n"
        files[f"{billing}/blank.rs"] = "//! isolation:\n#[test]\nfn b() {}\n"
        files[f"{billing}/late.rs"] = "#[test]\nfn c() {}\n//! isolation: too late\n"
        files[f"{billing}/common/mod.rs"] = "pub fn helper() {}\n"
        repo = self.check(files)[1]
        flagged = {f["path"]: f["message"] for f in repo["findings"] if f["rule"] == "A1"}
        self.assertEqual(set(flagged), {f"{billing}/blank.rs", f"{billing}/late.rs", f"{billing}/common/"})
        self.assertIn("沒有寫理由", flagged[f"{billing}/blank.rs"])

    def test_a2_single_crate_workspace_wrapper(self) -> None:
        files = single_crate()
        files["Cargo.toml"] += '\n[workspace]\nmembers = ["."]\n'
        repo = self.check(files)[1]
        self.assertIn(("A2", "[workspace]"), self.found(repo))
        self.assertIs(repo["d2"], False)

    def test_a2_d2_layout_is_warning_only(self) -> None:
        files = {k.replace("crates/demo-orders", "crates/orders"): v.replace("crates/demo-orders", "crates/orders")
                 for k, v in module_form().items()}
        code, repo = self.check(files)
        self.assertIn(("A2", "demo-orders"), self.found(repo))
        self.assertEqual((code, repo["status"], repo["d2"]), (0, "pass", False))

    def test_a3_a4_a5(self) -> None:
        files = module_form()
        files["Cargo.toml"] = files["Cargo.toml"].replace('mod_module_files = "deny"', 'mod_module_files = "warn"')
        files["NOTES.md"] = "notes\n"
        files[".loop-prompt.md"] = "hidden\n"
        del files["docs/glossary.md"]
        found = self.found(self.check(files)[1])
        self.assertIn(("A3", "clippy::mod_module_files"), found)
        self.assertIn(("A4", "NOTES.md"), found)
        self.assertNotIn(("A4", ".loop-prompt.md"), found)
        self.assertIn(("A5", "docs/glossary.md"), found)


class DomainRulesTest(Fixture):
    DOMAIN = "crates/demo-billing/src/domain.rs"

    def test_b1_undeclared_module_path_attr_and_orphan_crate(self) -> None:
        files = module_form()
        files["crates/demo-billing/src/lib.rs"] += '#[path = "odd.rs"]\npub mod extra;\n'
        files["crates/demo-billing/src/extra.rs"] = "pub fn extra() {}\n"
        files["crates/demo-tools/Cargo.toml"] = member("demo-tools")
        files["crates/demo-tools/src/lib.rs"] = "pub mod tool;\n"
        files["crates/demo-tools/src/tool.rs"] = "pub fn tool() {}\n"
        found = self.found(self.check(files)[1])
        self.assertIn(("B1", "crates/demo-billing/src/extra"), found)
        self.assertIn(("B1", "#[path]"), found)
        self.assertIn(("B1", "demo-tools"), found)

    def test_b2_domain_purity(self) -> None:
        files = module_form()
        files["crates/demo-billing/Cargo.toml"] += 'tokio = "1"\nuuid = "1"\n'
        files[self.DOMAIN] += (
            "use tokio::sync::Mutex;\n\npub fn load() -> Vec<u8> {\n    std::fs::read(\"x\").unwrap()\n}\n\n"
            "pub fn mint() -> uuid::Uuid {\n    uuid::Uuid::new_v4()\n}\n\n"
            "pub fn log() {\n    println!(\"x\");\n}\n\n#[cfg(test)]\nmod tests {\n    use std::process::Command;\n}\n"
        )
        code, repo = self.check(files)
        found = self.found(repo)
        wanted = {("B2", "tokio"), ("B2", "std::fs"), ("B2", "println!"), ("B2", "uuid::Uuid::new_v4")}
        self.assertTrue(wanted <= found, found)
        self.assertNotIn(("B2", "std::process"), found)
        self.assertEqual((code, repo["status"]), (1, "fail"))

    def test_b2_crate_form_cargo_deps(self) -> None:
        files = crate_form()
        files["crates/demo-billing-domain/Cargo.toml"] += 'tokio = "1"\n'
        files["crates/demo-billing-domain/build.rs"] = "fn main() {}\n"
        found = self.found(self.check(files)[1])
        self.assertIn(("B2", "tokio"), found)
        self.assertIn(("B2", "build.rs"), found)

    def test_b3_layer_direction(self) -> None:
        files = module_form()
        files[self.DOMAIN] = "use crate::application::Invoicer;\n" + files[self.DOMAIN]
        files["crates/demo-billing/src/interfaces.rs"] += "use crate::infrastructure::store;\n"
        found = self.found(self.check(files)[1])
        self.assertIn(("B3", "domain->application"), found)
        self.assertIn(("B3", "interfaces->infrastructure"), found)

    def test_b3_crate_form_domain_depends_on_own_context(self) -> None:
        files = crate_form()
        files["crates/demo-billing-domain/Cargo.toml"] += 'demo-billing = { path = "../demo-billing" }\n'
        self.assertIn(("B3", "demo-billing"), self.found(self.check(files)[1]))

    def test_b4_cross_context(self) -> None:
        files = module_form()
        files["crates/demo-billing/src/application.rs"] += "pub use crate::infrastructure::store;\n"
        files["crates/demo-orders/src/application.rs"] += (
            "use demo_billing::infrastructure::store;\nuse demo_billing::application::store as reexported;\n"
        )
        repo = self.check(files)[1]
        hits = [f for f in repo["findings"] if f["rule"] == "B4"]
        self.assertEqual({f["subject"] for f in hits}, {"billing.infrastructure"})
        self.assertIn("共 2 處", hits[0]["message"])

    def test_b4_through_private_crate_alias(self) -> None:
        # `use demo_billing as billing;` at the crate root is private, yet every
        # module of the crate reaches billing through `crate::billing::`.
        files = module_form()
        files["crates/demo-orders/src/lib.rs"] = "use demo_billing as billing;\n\n" + files["crates/demo-orders/src/lib.rs"]
        files["crates/demo-orders/src/application.rs"] += "use crate::billing::infrastructure::store;\n"
        self.assertIn(("B4", "billing.infrastructure"), self.found(self.check(files)[1]))

    def test_b4_missing_depends_on(self) -> None:
        files = module_form()
        files["ddd.toml"] = files["ddd.toml"].replace('depends_on = ["billing"]', "depends_on = []")
        found = self.found(self.check(files)[1])
        self.assertIn(("B4", "demo-billing"), found)
        self.assertIn(("B4", "billing.application"), found)

    def test_b1_naming_in_crate_form(self) -> None:
        files = {k.replace("demo-billing-domain", "billing-core"): v.replace("demo-billing-domain", "billing-core")
                 .replace("demo_billing_domain", "billing_core") for k, v in crate_form().items()}
        self.assertIn(("B1", "billing-core"), self.found(self.check(files)[1]))

    def test_b1_several_contexts_in_one_crate(self) -> None:
        files = module_form()
        files["crates/demo-billing/src/lib.rs"] += "pub mod audit;\n"
        files["crates/demo-billing/src/audit.rs"] = "pub mod domain;\n"
        files["crates/demo-billing/src/audit/domain.rs"] = "pub struct Entry;\n"
        files["ddd.toml"] += context("audit", "", {"domain": "crates/demo-billing/src/audit/domain"})
        self.assertClean(files)
        strict = {**load_policy(), "one_context_per_crate": True}
        files["ddd.toml"] = files["ddd.toml"].replace("one_context_per_crate = false", "one_context_per_crate = true")
        with mock.patch.object(cli, "load_policy", return_value=strict):
            code, repo = self.check(files)
        self.assertIn(("B1", "demo-billing"), self.found(repo))
        self.assertEqual(code, 1)

    def test_b5_thin_lib_allows_declarations(self) -> None:
        files = module_form()
        files["crates/demo-billing/src/lib.rs"] = (
            "//! Billing context.\n#![forbid(unsafe_code)]\n\nuse std::fmt;\n\n/// Layers.\n"
            + "".join(f"#[allow(missing_docs)]\npub mod {layer};\n" for layer in LAYERS)
            + "pub use application::Invoicer;\n\n#[cfg(test)]\nmod tests {\n    #[test]\n    fn t() {}\n}\n"
        )
        self.assertClean(files)

    def test_b5_is_a_warning_and_b6_an_error(self) -> None:
        files = module_form()
        files["crates/demo-billing/src/lib.rs"] += "fn helper() {}\n\nmod inline {}\n"
        code, repo = self.check(files)
        self.assertEqual({(f["rule"], f["severity"], f["subject"]) for f in repo["findings"]},
                         {("B5", "warning", "fn helper"), ("B5", "warning", "inline mod")})
        self.assertEqual((code, repo["status"]), (0, "pass"))

    def test_b6_multi_file_binary(self) -> None:
        files = module_form()
        files["crates/demo-cli/Cargo.toml"] = member("demo-cli")
        files["crates/demo-cli/src/bin/tool/main.rs"] = "mod args;\n\nfn main() {\n    args::parse();\n}\n"
        files["crates/demo-cli/src/bin/tool/args.rs"] = "pub struct Args;\n\npub fn parse() -> Args {\n    Args\n}\n"
        files["ddd.toml"] += '\n[assembly]\ncrates = ["demo-cli"]\n'
        self.assertClean(files)

    def test_b6_entry_allows_cfg_test_mod(self) -> None:
        files = module_form()
        files["crates/demo-cli/Cargo.toml"] = member("demo-cli")
        files["crates/demo-cli/src/main.rs"] = (
            "mod cli;\n\nfn main() {}\n\n#[cfg(test)]\nmod tests {\n    #[test]\n    fn parses() {}\n}\n"
        )
        files["crates/demo-cli/src/cli.rs"] = "pub fn run() {}\n"
        files["ddd.toml"] += '\n[assembly]\ncrates = ["demo-cli"]\n'
        self.assertClean(files)

    def test_b5_b6_roots_and_binaries(self) -> None:
        files = module_form()
        files["crates/demo-billing/src/lib.rs"] += "fn helper() {}\n"
        files["crates/demo-cli/Cargo.toml"] = member("demo-cli")
        files["crates/demo-cli/src/main.rs"] = "use std::env;\n\nstruct Flag;\n\nfn main() {}\n"
        files["crates/demo-cli/src/bin/tool/main.rs"] = "fn main() {}\n"
        files["crates/demo-cli/src/bin/tool/extra.rs"] = "pub fn extra() {}\n"
        files["ddd.toml"] += '\n[assembly]\ncrates = ["demo-cli"]\n'
        found = self.found(self.check(files)[1])
        self.assertIn(("B5", "fn helper"), found)
        self.assertIn(("B6", "struct_item"), found)
        self.assertNotIn(("B6", "src/bin/<name>/"), found)
        self.assertNotIn(("B1", "demo-cli"), found)


class SizeRulesTest(Fixture):
    KERNEL = "crates/demo-shared-kernel/src"

    def sized(self, **lines: int) -> dict[str, str]:
        files = module_form()
        for name, count in lines.items():
            files[f"{self.KERNEL}/lib.rs"] += f"pub mod {name};\n"
            files[f"{self.KERNEL}/{name}.rs"] = "// line\n" * count
        return files

    def test_c1_c2(self) -> None:
        found = self.found(self.check(self.sized(medium=401, huge=1001))[1])
        self.assertIn(("C1", f"{self.KERNEL}/medium.rs"), found)
        self.assertIn(("C2", f"{self.KERNEL}/huge.rs"), found)

    def test_c3_allowlist(self) -> None:
        files = self.sized(listed=1200, shrunk=1100, over=1300)
        entries = {"listed": 1200, "shrunk": 1150, "over": 1250, "gone": 1500}
        files["ddd.toml"] += "".join(
            f'\n[[large_files]]\npath = "{self.KERNEL}/{name}.rs"\nmax_lines = {limit}\nreason = "generated table"\n'
            for name, limit in entries.items()
        )
        repo = self.check(files)[1]
        by_subject = {f["subject"].rsplit("/", 1)[-1]: (f["rule"], f["severity"]) for f in repo["findings"]}
        self.assertNotIn("listed.rs", by_subject)
        self.assertEqual(by_subject["shrunk.rs"], ("C3", "warning"))
        self.assertEqual(by_subject["over.rs"], ("C3", "error"))
        self.assertEqual(by_subject["gone.rs"], ("C3", "error"))
        self.assertEqual(repo["large_files"]["over_error"], 3)
        self.assertEqual(repo["large_files"]["top"][0], {"path": f"{self.KERNEL}/over.rs", "lines": 1300})


class ConfigRulesTest(Fixture):
    def test_p1_policy_drift_and_p2_schema(self) -> None:
        files = module_form()
        files["ddd.toml"] = files["ddd.toml"].replace("size_warn = 400", "size_warn = 500") + (
            '\n[[exceptions]]\nrule = "B2"\npaths = ["crates/*.rs"]\nsubject = "std::fs"\nreason = "TODO"\n\n[bogus]\nx = 1\n'
        )
        found = self.found(self.check(files)[1])
        self.assertIn(("P1", "size_warn"), found)
        self.assertIn(("P2", "exceptions[0].paths"), found)
        self.assertIn(("P2", "exceptions[0].reason"), found)
        self.assertIn(("P2", "ddd.toml.bogus"), found)

    def test_exceptions_suppress_matching_findings(self) -> None:
        files = module_form()
        domain = DomainRulesTest.DOMAIN
        files[domain] += 'pub fn load() -> Vec<u8> {\n    std::fs::read("x").unwrap()\n}\n'
        files["ddd.toml"] += f'\n[[exceptions]]\nrule = "B2"\npaths = ["{domain}"]\nsubject = "std::fs"\nreason = "legacy loader"\n'
        code, repo = self.check(files)
        self.assertEqual(repo["findings"][0]["suppressed_by"], "exceptions[0]")
        self.assertEqual((code, repo["status"]), (0, "pass"))


class RatchetTest(Fixture):
    KERNEL = SizeRulesTest.KERNEL

    def commit_base(self, files: dict[str, str]) -> str:
        self.write(files)
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "base")
        return git(self.repo, "rev-parse", "HEAD")

    def listed(self, entries: dict[str, int]) -> str:
        return "".join(
            f'\n[[large_files]]\npath = "{self.KERNEL}/{name}.rs"\nmax_lines = {limit}\nreason = "generated table"\n'
            for name, limit in entries.items()
        )

    def test_r1_allowlists_only_tighten(self) -> None:
        files = SizeRulesTest.sized(self, big=1200)
        base_ddd = files["ddd.toml"]
        files["ddd.toml"] = base_ddd + self.listed({"big": 1200})
        base = self.commit_base(files)
        files["ddd.toml"] = base_ddd.replace("enforce = true", "enforce = false") + self.listed({"big": 1300}) + (
            f'\n[[exceptions]]\nrule = "B2"\npaths = ["{DomainRulesTest.DOMAIN}"]\nsubject = "std::fs"\nreason = "legacy"\n'
        )
        found = self.found(self.check(files, "--base", base)[1])
        self.assertIn(("R1", f"large_files:{self.KERNEL}/big.rs"), found)
        self.assertIn(("R1", "migration.enforce"), found)
        self.assertIn(("R1", "exceptions:B2:std::fs"), found)

    def test_r1_renames_carry_the_budget(self) -> None:
        files = SizeRulesTest.sized(self, big=1200)
        base_ddd = files["ddd.toml"]
        files["ddd.toml"] = base_ddd + self.listed({"big": 1200})
        base = self.commit_base(files)
        (self.repo / self.KERNEL / "big.rs").unlink()
        del files[f"{self.KERNEL}/big.rs"]
        files[f"{self.KERNEL}/lib.rs"] = files[f"{self.KERNEL}/lib.rs"].replace("pub mod big;\n", "pub mod left;\npub mod right;\n")
        files[f"{self.KERNEL}/left.rs"] = "// line\n" * 1100
        files[f"{self.KERNEL}/right.rs"] = "// line\n" * 100
        rename = f'\n[[renames]]\nfrom = "{self.KERNEL}/big.rs"\nto = ["{self.KERNEL}/left.rs", "{self.KERNEL}/right.rs"]\n'
        files["ddd.toml"] = base_ddd + self.listed({"left": 1100}) + rename
        code, repo = self.check(files, "--base", base)
        self.assertEqual((code, repo["findings"]), (0, []))
        files["ddd.toml"] = base_ddd + self.listed({"left": 1250}) + rename
        found = self.found(self.check(files, "--base", base)[1])
        self.assertIn(("R1", f"renames:{self.KERNEL}/big.rs"), found)

    def test_r1_skipped_without_base_config(self) -> None:
        files = module_form()
        base_ddd = files.pop("ddd.toml")
        base = self.commit_base(files)
        files["ddd.toml"] = base_ddd
        code, repo = self.check(files, "--base", base)
        self.assertEqual(code, 0)
        self.assertTrue(any("略過 ratchet" in note for note in repo["notes"]))


class ModesTest(Fixture):
    def test_undeclared_repo_is_report_only(self) -> None:
        code, repo = self.check(single_crate())
        self.assertEqual((code, repo["status"], repo["config"]), (0, "report", "none"))
        self.assertEqual(self.found(repo), {("B1", "demo")})
        self.assertTrue(any("ddd: undeclared" in note for note in repo["notes"]))

    def test_strict_and_baseline(self) -> None:
        self.write(single_crate())
        self.assertEqual(self.check(None, "--strict")[0], 1)
        files = module_form()
        files["NOTES.md"] = "notes\n"
        self.write(files)
        self.assertEqual(self.check(None)[0], 1)
        code, repo = self.check(None, "--baseline")
        self.assertEqual((code, repo["status"], repo["enforced"]), (0, "report", False))

    def test_tool_error_exits_2(self) -> None:
        files = module_form()
        files["Cargo.toml"] = "[workspace\n"
        code, repo = self.check(files)
        self.assertEqual((code, repo["status"]), (2, "error"))

    def test_non_rust_repo_is_na(self) -> None:
        code, repo = self.check({"README.md": "# x\n"})
        self.assertEqual((code, repo["status"], repo["language"]), (0, "n/a", "n/a"))


if __name__ == "__main__":
    unittest.main()
