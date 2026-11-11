//! The release contract, `.github/aw-release.json`: which milestone is
//! which version, and which issues and checks prove each goal.

use std::collections::BTreeSet;

use serde_json::{Value, json};

use super::digest::digest;
use super::require;
use super::tag::is_version;
use crate::domain::work::ports::PortResult;

/// Where the contract is, in the repository.
pub const CONTRACT_PATH: &str = ".github/aw-release.json";

/// The parts of a milestone that the user approved: a change of one of
/// them changes `goal_sha256`.
pub fn goal_identity(milestone: &Value) -> Value {
    json!({
        "number": milestone["number"],
        "title": milestone["title"],
        "description": milestone["description"],
    })
}

fn keys(value: &Value, expected: &[&str]) -> bool {
    value.as_object().is_some_and(|map| {
        map.len() == expected.len() && expected.iter().all(|k| map.contains_key(*k))
    })
}

fn unique<T: Ord>(items: &[T]) -> bool {
    items.iter().collect::<BTreeSet<_>>().len() == items.len()
}

/// Check version 1 of the contract for `repo` and the live `milestone`.
/// `checks` are the check ids of the gates policy. Returns the item of the
/// milestone.
pub fn contract(
    config: &Value,
    repo: &str,
    milestone: &Value,
    checks: &BTreeSet<String>,
) -> PortResult<Value> {
    require(
        keys(config, &["version", "repo", "milestones"])
            && config["version"].as_u64() == Some(1)
            && config["repo"].as_str() == Some(repo)
            && config["milestones"].is_object(),
        "invalid reviewed release configuration",
    )?;
    let versions: Vec<&Value> = config["milestones"]
        .as_object()
        .into_iter()
        .flatten()
        .map(|(_, item)| &item["version"])
        .filter(|version| !version.is_null())
        .collect();
    require(
        versions
            .iter()
            .all(|a| versions.iter().filter(|b| a == *b).count() == 1),
        "release version belongs to more than one milestone",
    )?;
    let number = milestone["number"].as_u64().unwrap_or(0);
    let item = &config["milestones"][number.to_string()];
    require(
        number > 0
            && keys(
                item,
                &["version", "goal_sha256", "acceptance", "publication"],
            ),
        "existing milestone needs a reviewed release contract",
    )?;
    require(
        item["version"].as_str().is_some_and(is_version),
        "invalid Semantic Version",
    )?;
    require(
        milestone["description"]
            .as_str()
            .is_some_and(|d| !d.trim().is_empty())
            && item["goal_sha256"].as_str() == Some(&digest(&goal_identity(milestone))),
        "milestone goal changed or is empty",
    )?;
    let acceptance = item["acceptance"].as_array().filter(|a| !a.is_empty());
    require(acceptance.is_some(), "goal acceptance is missing")?;
    let mut names = Vec::new();
    for goal in acceptance.into_iter().flatten() {
        let name = goal["goal"].as_str().unwrap_or_default();
        require(
            keys(goal, &["goal", "issues", "checks"]) && !name.trim().is_empty(),
            "invalid goal acceptance",
        )?;
        names.push(name);
        let issues: Option<Vec<u64>> = goal["issues"]
            .as_array()
            .and_then(|rows| rows.iter().map(|n| n.as_u64().filter(|n| *n > 0)).collect());
        require(
            issues.is_some_and(|rows| !rows.is_empty() && unique(&rows)),
            "goal needs explicit issue evidence",
        )?;
        let named: Option<Vec<&str>> = goal["checks"]
            .as_array()
            .and_then(|rows| rows.iter().map(Value::as_str).collect());
        require(
            named.is_some_and(|rows| {
                !rows.is_empty() && unique(&rows) && rows.iter().all(|c| checks.contains(*c))
            }),
            "goal needs real full-plan checks",
        )?;
    }
    require(unique(&names), "duplicate goal acceptance")?;
    require(
        item["publication"].as_str() == Some("github-release"),
        "unsupported publication proof",
    )?;
    Ok(item.clone())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn milestone() -> Value {
        json!({"number": 8, "title": "core@0.5.0", "description": "The goal", "state": "open"})
    }

    fn good() -> Value {
        json!({
            "version": 1,
            "repo": "o/r",
            "milestones": {"8": {
                "version": "0.5.0",
                "goal_sha256": digest(&goal_identity(&milestone())),
                "acceptance": [{"goal": "It works", "issues": [3, 4], "checks": ["unit"]}],
                "publication": "github-release"
            }}
        })
    }

    fn checks() -> BTreeSet<String> {
        ["unit".to_string(), "e2e".to_string()].into()
    }

    fn broken(change: impl FnOnce(&mut Value)) -> String {
        let mut value = good();
        change(&mut value);
        contract(&value, "o/r", &milestone(), &checks())
            .unwrap_err()
            .0
    }

    #[test]
    fn returns_the_item_of_the_milestone() {
        let item = contract(&good(), "o/r", &milestone(), &checks()).unwrap();
        assert_eq!(item["version"], "0.5.0");
    }

    #[test]
    fn the_goal_digest_is_the_one_of_the_old_tool() {
        // python3 release_state.digest(goal_identity(milestone))
        assert_eq!(
            digest(&goal_identity(
                &json!({"number": 1, "title": "t", "description": "d", "state": "open"})
            )),
            digest(&json!({"description": "d", "number": 1, "title": "t"}))
        );
    }

    #[test]
    fn refuses_any_other_configuration() {
        let c = "invalid reviewed release configuration";
        assert!(
            contract(&good(), "o/x", &milestone(), &checks())
                .unwrap_err()
                .0
                .contains(c)
        );
        assert!(broken(|v| v["version"] = json!(1.0)).contains(c));
        assert!(broken(|v| v["ready_rule"] = json!("x")).contains(c));
        assert!(broken(|v| v["milestones"] = json!([])).contains(c));
        let m = "needs a reviewed release contract";
        assert!(broken(|v| v["milestones"] = json!({})).contains(m));
        assert!(broken(|v| v["milestones"]["8"]["tag"] = json!("v0.5.0")).contains(m));
        assert!(
            broken(|v| v["milestones"]["8"]["version"] = json!("0.5")).contains("Semantic Version")
        );
        assert!(
            broken(|v| v["milestones"]["8"]["goal_sha256"] = json!("0")).contains("goal changed")
        );
        assert!(
            broken(|v| v["milestones"]["8"]["publication"] = json!("crates"))
                .contains("publication")
        );
    }

    #[test]
    fn refuses_one_version_for_two_milestones() {
        let v = "release version belongs to more than one milestone";
        // Also when the other milestone is not the one asked for.
        let err = broken(|c| {
            c["milestones"]["7"] = json!({"version": "0.4.0"});
            c["milestones"]["9"] = json!({"version": "0.4.0"});
        });
        assert!(err.contains(v));
        assert!(broken(|c| c["milestones"]["9"] = c["milestones"]["8"].clone()).contains(v));
        let mut other = good();
        other["milestones"]["9"] = json!({"version": "0.6.0"});
        assert!(contract(&other, "o/r", &milestone(), &checks()).is_ok());
    }

    #[test]
    fn refuses_a_changed_or_empty_goal() {
        let mut changed = milestone();
        changed["title"] = json!("core@0.6.0");
        assert!(contract(&good(), "o/r", &changed, &checks()).is_err());
        let mut empty = milestone();
        empty["description"] = json!("  ");
        let mut config = good();
        config["milestones"]["8"]["goal_sha256"] = json!(digest(&goal_identity(&empty)));
        assert!(
            contract(&config, "o/r", &empty, &checks())
                .unwrap_err()
                .0
                .contains("empty")
        );
        assert!(contract(&good(), "o/r", &json!({"title": "x"}), &checks()).is_err());
    }

    #[test]
    fn refuses_weak_acceptance() {
        let at = |v: &mut Value, key: &str, value: Value| {
            v["milestones"]["8"]["acceptance"][0][key] = value
        };
        assert!(
            broken(|v| v["milestones"]["8"]["acceptance"] = json!([]))
                .contains("acceptance is missing")
        );
        assert!(broken(|v| at(v, "goal", json!(" "))).contains("invalid goal acceptance"));
        assert!(broken(|v| at(v, "extra", json!(1))).contains("invalid goal acceptance"));
        for issues in [json!([]), json!([3, 3]), json!([0]), json!(["3"]), json!(3)] {
            assert!(
                broken(|v| at(v, "issues", issues.clone())).contains("issue evidence"),
                "{issues}"
            );
        }
        for names in [
            json!([]),
            json!(["unit", "unit"]),
            json!(["none"]),
            json!([1]),
            json!("unit"),
        ] {
            assert!(
                broken(|v| at(v, "checks", names.clone())).contains("full-plan checks"),
                "{names}"
            );
        }
        assert!(
            broken(|v| {
                let first = v["milestones"]["8"]["acceptance"][0].clone();
                v["milestones"]["8"]["acceptance"]
                    .as_array_mut()
                    .unwrap()
                    .push(first);
            })
            .contains("duplicate goal acceptance")
        );
    }
}
