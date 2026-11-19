//! T1 and T2: the facts of `aw.toml` against the two release tables that the
//! scripts still read: `apps.sh` (T1) and the receipt maker (T2). The tables
//! are read by the adapter; the rules here compare.

use std::collections::{BTreeMap, BTreeSet};

use serde::Deserialize;

use super::app_check::ContractRepo;
use super::app_rules::Fail;
use super::facts::AppFacts;

/// One app of the receipt maker `APPS` table.
#[derive(Clone, Debug, Default, Deserialize, PartialEq, Eq)]
pub struct MakerApp {
    pub targets: Vec<String>,
    pub backend: String,
    pub functional: Vec<String>,
    pub candidate_schema: String,
    pub receipt_schema: String,
    pub final_jobs: Vec<String>,
}

/// What the receipt maker holds: its apps and six constants.
#[derive(Clone, Debug, Default, Deserialize, PartialEq, Eq)]
pub struct Maker {
    pub apps: BTreeMap<String, MakerApp>,
    pub constants: BTreeMap<String, Option<String>>,
}

type Check = Result<(), Fail>;

fn fail(rule: &'static str, message: impl Into<String>) -> Check {
    Err(Fail {
        rule,
        message: message.into(),
    })
}

fn refused(rule: &'static str, result: Result<Vec<String>, String>) -> Result<Vec<String>, Fail> {
    result.map_err(|message| Fail { rule, message })
}

/// T1: `apps.sh` says what the facts say.
fn apps_sh(repo: &dyn ContractRepo, app: &AppFacts) -> Check {
    let path = format!("{}/apps.sh", app.scripts_dir);
    if !repo.exists(&path) {
        return fail("T1", format!("missing file: {path}"));
    }
    let name = app.name.as_str();
    let same = |function: &str, expected: &[String]| -> Check {
        let actual = refused("T1", repo.shell_values(&path, function, name))?;
        if actual != expected {
            return fail(
                "T1",
                format!("apps.sh {function} {name} = {actual:?}, aw.toml says {expected:?}"),
            );
        }
        Ok(())
    };
    let one = |value: &str| vec![value.to_string()];
    same("release_app_root", &one(&app.root))?;
    same("release_app_scripts_dir", &one(&app.scripts_dir))?;
    same("release_app_targets", &app.targets)?;
    same("release_app_gke_backend", &one(&app.gke_backend))?;
    same("release_app_receipt_name", &one(&app.receipt_name))?;
    same("release_app_receipt_schema", &one(&app.receipt_schema))?;
    same(
        "release_app_promotion_input_prefix",
        &one(&app.promotion_prefix),
    )?;
    same("release_app_manifest_schema", &one(&app.manifest_schema))?;
    same("release_app_candidate_jobs", &app.candidate_jobs)?;
    let predicate = |function: &str, expected: bool, what: &str| -> Check {
        let actual = repo
            .shell_predicate(&path, function, name)
            .map_err(|message| Fail {
                rule: "T1",
                message,
            })?;
        if actual != expected {
            return fail("T1", format!("apps.sh disagrees on whether {name} {what}"));
        }
        Ok(())
    };
    predicate(
        "release_app_uses_shared_scripts",
        app.shared_scripts,
        "uses the shared scripts",
    )?;
    predicate(
        "release_app_promotion_takes_attempt",
        app.takes_attempt,
        "promotion takes a run attempt",
    )?;
    if app.shared_scripts {
        same("release_app_functional_fields", &app.functional)?;
    }
    let loom = repo
        .shell_predicate(&path, "release_app_known", "loom")
        .map_err(|message| Fail {
            rule: "T1",
            message,
        })?;
    if loom {
        return fail(
            "T1",
            "apps.sh must not know loom: it has no candidate or promotion",
        );
    }
    Ok(())
}

/// T2: the receipt maker says what the facts say.
fn receipt_maker(repo: &dyn ContractRepo, app: &AppFacts, shared: &BTreeSet<String>) -> Check {
    let path = format!("{}/make-gke-release-receipt.py", app.scripts_dir);
    if !repo.exists(&path) {
        return fail("T2", format!("missing file: {path}"));
    }
    let mut expected: BTreeMap<&str, &str> = BTreeMap::from([
        ("REPOSITORY", app.repository.as_str()),
        ("IMAGE_OWNER", app.image_owner.as_str()),
    ]);
    for (key, value) in &app.maker_constants {
        expected.insert(key, value);
    }
    let wanted: Vec<String> = expected.keys().map(|k| k.to_string()).collect();
    let maker = repo.maker(&path, &wanted).map_err(|message| Fail {
        rule: "T2",
        message,
    })?;
    let known: BTreeSet<String> = maker.apps.keys().cloned().collect();
    if &known != shared {
        return fail(
            "T2",
            format!("receipt maker knows {known:?}, shared-script apps are {shared:?}"),
        );
    }
    let name = &app.name;
    let entry = &maker.apps[name];
    let differ = |what: &str| {
        fail(
            "T2",
            format!("receipt maker {what} for {name} differ from aw.toml"),
        )
    };
    if entry.targets != app.targets {
        return differ("targets");
    }
    if entry.backend != app.gke_backend {
        return fail(
            "T2",
            format!(
                "receipt maker backend for {name} is {}, aw.toml says {}",
                entry.backend, app.gke_backend
            ),
        );
    }
    if entry.functional != app.functional {
        return differ("functional fields");
    }
    if entry.candidate_schema != app.manifest_schema {
        return differ("candidate schema");
    }
    if entry.receipt_schema != app.receipt_schema {
        return differ("receipt schema");
    }
    if entry.final_jobs != app.candidate_jobs {
        return differ("job inventory");
    }
    for (attribute, expected) in expected {
        let actual = maker.constants.get(attribute).cloned().flatten();
        if actual.as_deref() != Some(expected) {
            return fail(
                "T2",
                format!("receipt maker {attribute} = {actual:?}, expected {expected:?}"),
            );
        }
    }
    Ok(())
}

/// T1 and T2 of one app: the first broken rule of each.
pub fn tables(repo: &dyn ContractRepo, app: &AppFacts, shared: &BTreeSet<String>) -> Vec<Fail> {
    [apps_sh(repo, app), receipt_maker(repo, app, shared)]
        .into_iter()
        .filter_map(Result::err)
        .collect()
}
