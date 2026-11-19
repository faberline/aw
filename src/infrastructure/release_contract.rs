//! Adapter for `aw release-contract`: a git checkout on this machine and the
//! `[release.apps.<app>]` tables of its `aw.toml`.

use std::path::{Path, PathBuf};

use toml::{Table, Value};

use crate::domain::release::app_check::ContractRepo;
use crate::domain::release::app_tables::Maker;
use crate::domain::release::facts::AppFacts;
use crate::domain::work::ports::{PortError, PortResult};
use crate::infrastructure::work::process::run;

/// The keys of an app table. Any other key is an error, so a typo is caught.
const KEYS: [&str; 26] = [
    "onboarded",
    "scripts_dir",
    "shared_scripts",
    "targets",
    "candidate_jobs",
    "promotion_prefix",
    "takes_attempt",
    "manifest_schema",
    "receipt_name",
    "image_owner",
    "compatibility_line",
    "root",
    "repository",
    "receipt_schema",
    "gke_backend",
    "functional",
    "gates_step",
    "acceptance_names",
    "acceptance_run_name",
    "maker_constants",
    "candidate_gates",
    "ci_gates",
    "acceptance_schema",
    "image_companions",
    "acceptance_companions",
    "acceptance_extra_keys",
];

/// A git checkout; `root` is its top folder.
pub struct LocalContract {
    root: PathBuf,
}

impl LocalContract {
    /// The checkout that holds `dir`.
    pub fn open(dir: &Path) -> PortResult<Self> {
        let out = run(
            "git",
            &["-C", &dir.to_string_lossy(), "rev-parse", "--show-toplevel"],
            None,
        )
        .map_err(|e| {
            PortError(format!(
                "{} is not inside a git checkout: {e}",
                dir.display()
            ))
        })?;
        let root = std::fs::canonicalize(out.trim())
            .map_err(|e| PortError(format!("could not read {}: {e}", out.trim())))?;
        Ok(LocalContract { root })
    }
}

fn text(table: &Table, app: &str, key: &str) -> PortResult<Option<String>> {
    match table.get(key) {
        None => Ok(None),
        Some(Value::String(s)) if !s.is_empty() => Ok(Some(s.clone())),
        Some(_) => Err(PortError(format!(
            "release.apps.{app}.{key} must be a non-empty string"
        ))),
    }
}

fn flag(table: &Table, app: &str, key: &str) -> PortResult<Option<bool>> {
    match table.get(key) {
        None => Ok(None),
        Some(Value::Boolean(b)) => Ok(Some(*b)),
        Some(_) => Err(PortError(format!(
            "release.apps.{app}.{key} must be true or false"
        ))),
    }
}

fn list(table: &Table, app: &str, key: &str) -> PortResult<Option<Vec<String>>> {
    let Some(value) = table.get(key) else {
        return Ok(None);
    };
    let items: Option<Vec<String>> = value.as_array().and_then(|a| {
        a.iter()
            .map(|v| v.as_str().filter(|s| !s.is_empty()).map(str::to_string))
            .collect()
    });
    match items {
        Some(items) if !items.is_empty() => Ok(Some(items)),
        _ => Err(PortError(format!(
            "release.apps.{app}.{key} must be a non-empty list of strings"
        ))),
    }
}

/// One `[release.apps.<name>]` table.
fn app_facts(name: &str, table: &Table) -> PortResult<AppFacts> {
    if let Some(key) = table.keys().find(|k| !KEYS.contains(&k.as_str())) {
        return Err(PortError(format!(
            "release.apps.{name}.{key} is not a known key"
        )));
    }
    let mut app = AppFacts::shared(name);
    if let Some(v) = flag(table, name, "onboarded")? {
        app.onboarded = v;
    }
    if let Some(v) = text(table, name, "scripts_dir")? {
        app.scripts_dir = v;
    }
    if let Some(v) = flag(table, name, "shared_scripts")? {
        app.shared_scripts = v;
    }
    if let Some(v) = list(table, name, "targets")? {
        app.targets = v;
    }
    if let Some(v) = list(table, name, "candidate_jobs")? {
        app.candidate_jobs = v;
    }
    if let Some(v) = text(table, name, "promotion_prefix")? {
        app.promotion_prefix = v;
    }
    if let Some(v) = flag(table, name, "takes_attempt")? {
        app.takes_attempt = v;
    }
    if let Some(v) = text(table, name, "manifest_schema")? {
        app.manifest_schema = v;
    }
    if let Some(v) = text(table, name, "receipt_name")? {
        app.receipt_name = v;
    }
    if let Some(v) = text(table, name, "image_owner")? {
        app.image_owner = v;
    }
    if let Some(v) = text(table, name, "compatibility_line")? {
        app.compatibility_line = v;
    }
    if let Some(v) = text(table, name, "root")? {
        app.root = v;
    }
    if let Some(v) = text(table, name, "repository")? {
        app.repository = v;
    }
    if let Some(v) = text(table, name, "receipt_schema")? {
        app.receipt_schema = v;
    }
    if let Some(v) = text(table, name, "gke_backend")? {
        app.gke_backend = v;
    }
    if let Some(v) = list(table, name, "functional")? {
        app.functional = v;
    }
    if let Some(v) = text(table, name, "gates_step")? {
        app.gates_step = v;
    }
    if let Some(v) = list(table, name, "candidate_gates")? {
        app.candidate_gates = v;
    }
    if let Some(v) = list(table, name, "ci_gates")? {
        app.ci_gates = v;
    }
    if let Some(v) = text(table, name, "acceptance_schema")? {
        app.acceptance_schema = v;
    }
    if let Some(v) = list(table, name, "image_companions")? {
        app.image_companions = v;
    }
    if let Some(v) = list(table, name, "acceptance_companions")? {
        app.acceptance_companions = v;
    }
    if let Some(v) = list(table, name, "acceptance_extra_keys")? {
        app.acceptance_extra_keys = v;
    }
    if let Some(v) = list(table, name, "acceptance_names")? {
        app.acceptance_names = v;
    }
    if let Some(v) = text(table, name, "acceptance_run_name")? {
        app.acceptance_run_name = v;
    }
    if let Some(v) = table.get("maker_constants") {
        let Some(t) = v.as_table() else {
            return Err(PortError(format!(
                "release.apps.{name}.maker_constants must be a table of strings"
            )));
        };
        for (key, value) in t {
            let Some(text) = value.as_str() else {
                return Err(PortError(format!(
                    "release.apps.{name}.maker_constants.{key} must be a string"
                )));
            };
            app.maker_constants.insert(key.clone(), text.to_string());
        }
    }
    if !app.candidate_gates.is_empty() && app.gates_step.is_empty() {
        return Err(PortError(format!(
            "release.apps.{name}.gates_step is required when candidate_gates is set"
        )));
    }
    if app.onboarded && app.targets.is_empty() {
        return Err(PortError(format!(
            "release.apps.{name}.targets is required for an onboarded app"
        )));
    }
    if app.onboarded && app.shared_scripts && app.compatibility_line.is_empty() {
        return Err(PortError(format!(
            "release.apps.{name}.compatibility_line is required when shared_scripts is true"
        )));
    }
    Ok(app)
}

/// The Python that prints the tables of the receipt maker as JSON. The maker
/// is the repository's own script; the check imports it as the Python
/// contract script did, and writes no bytecode.
const MAKER_DUMP: &str = r#"
import importlib.util, json, sys
spec = importlib.util.spec_from_file_location("maker", sys.argv[1])
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
names = json.loads(sys.argv[2])
print(json.dumps({
    "apps": {n: {
        "targets": list(e["targets"]),
        "backend": e["backend"],
        "functional": list(e["functional"]),
        "candidate_schema": m.candidate_schema(n),
        "receipt_schema": m.receipt_schema(n),
        "final_jobs": list(m.final_jobs(n)),
    } for n, e in m.APPS.items()},
    "constants": {k: getattr(m, k, None) for k in names},
}))
"#;

impl LocalContract {
    fn shell(
        &self,
        apps_sh: &str,
        function: &str,
        app: &str,
    ) -> Result<std::process::Output, String> {
        std::process::Command::new("bash")
            .args([
                "-c",
                r#"source "$1"; shift; "$@""#,
                "_",
                apps_sh,
                function,
                app,
            ])
            .current_dir(&self.root)
            .output()
            .map_err(|e| format!("could not run bash: {e}"))
    }
}

impl ContractRepo for LocalContract {
    fn shell_values(
        &self,
        apps_sh: &str,
        function: &str,
        app: &str,
    ) -> Result<Vec<String>, String> {
        let out = self.shell(apps_sh, function, app)?;
        if !out.status.success() {
            return Err(format!(
                "apps.sh {function} {app}: exit {}: {}",
                out.status.code().map_or("signal".into(), |c| c.to_string()),
                String::from_utf8_lossy(&out.stderr).trim()
            ));
        }
        Ok(String::from_utf8_lossy(&out.stdout)
            .split('\n')
            .filter(|l| !l.is_empty())
            .map(String::from)
            .collect())
    }

    fn shell_predicate(&self, apps_sh: &str, function: &str, app: &str) -> Result<bool, String> {
        let out = self.shell(apps_sh, function, app)?;
        match out.status.code() {
            Some(0) => Ok(true),
            Some(1) => Ok(false),
            code => Err(format!(
                "apps.sh {function} {app}: exit {}: {}",
                code.map_or("signal".into(), |c| c.to_string()),
                String::from_utf8_lossy(&out.stderr).trim()
            )),
        }
    }

    fn maker(&self, path: &str, constants: &[String]) -> Result<Maker, String> {
        let out = std::process::Command::new("python3")
            .args(["-I", "-B", "-c", MAKER_DUMP])
            .arg(self.root.join(path))
            .arg(serde_json::to_string(constants).map_err(|e| e.to_string())?)
            .output()
            .map_err(|e| format!("could not run python3: {e}"))?;
        if !out.status.success() {
            return Err(format!(
                "could not load {path}: {}",
                String::from_utf8_lossy(&out.stderr).trim()
            ));
        }
        serde_json::from_slice(&out.stdout)
            .map_err(|e| format!("could not read the tables of {path}: {e}"))
    }

    fn read(&self, path: &str) -> Option<String> {
        let full = self.root.join(path);
        if !full.is_file() {
            return None;
        }
        std::fs::read_to_string(full).ok()
    }

    fn exists(&self, path: &str) -> bool {
        self.root.join(path).exists()
    }

    fn apps(&self) -> PortResult<Vec<AppFacts>> {
        let path = self.root.join("aw.toml");
        let source = std::fs::read_to_string(&path)
            .map_err(|e| PortError(format!("could not read {}: {e}", path.display())))?;
        let table = source
            .parse::<Table>()
            .map_err(|e| PortError(format!("could not parse {}: {e}", path.display())))?;
        let Some(apps) = table
            .get("release")
            .and_then(|r| r.get("apps"))
            .and_then(Value::as_table)
        else {
            return Ok(Vec::new());
        };
        apps.iter()
            .map(|(name, value)| match value.as_table() {
                Some(t) => app_facts(name, t),
                None => Err(PortError(format!("release.apps.{name} must be a table"))),
            })
            .collect()
    }
}
