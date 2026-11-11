//! The decision: from one verified snapshot of a milestone to its state and
//! the one next action. No reads, no writes.

use std::collections::{BTreeMap, BTreeSet};

use serde_json::{Value, json};

use super::digest::{digest, is_sha};
use super::require;
use super::tag::rc_number;
use crate::domain::work::ports::PortResult;

pub const ASSESSMENT: &str = "AW_RELEASE_ASSESSMENT_V1";

/// Conclusions of a run or a job that are a confirmed failure. Every other
/// conclusion that is not `success` is unknown, and unknown never passes.
pub const FAILURES: [&str; 4] = ["failure", "timed_out", "action_required", "startup_failure"];

/// The mark at the start of the repair issue of one failed tag run.
pub fn repair_marker(
    repo: &str,
    milestone: u64,
    tag: &str,
    head: &str,
    evidence: &Value,
) -> String {
    let attempts: BTreeSet<(String, u64, u64)> = evidence["failures"]
        .as_array()
        .into_iter()
        .flatten()
        .map(|r| {
            (
                r["workflow"].as_str().unwrap_or_default().to_string(),
                r["run"].as_u64().unwrap_or(0),
                r["attempt"].as_u64().unwrap_or(0),
            )
        })
        .collect();
    let attempts: Vec<Value> = attempts
        .into_iter()
        .map(|(w, r, a)| json!([w, r, a]))
        .collect();
    format!(
        "<!-- aw-release-failure:{} -->",
        digest(&json!([repo, milestone, tag, head, attempts]))
    )
}

fn numbers(rows: &Value) -> Vec<u64> {
    rows.as_array()
        .into_iter()
        .flatten()
        .filter_map(|r| r["number"].as_u64())
        .collect()
}

/// Does the frozen commit `head` still hold all the approved goal work and
/// the current contract?
pub fn covers_release(snapshot: &Value, head: &str) -> bool {
    if snapshot["head"].as_str() == Some(head) {
        return true; // The delivery on the current head is verified by `evaluate`.
    }
    let proof = &snapshot["release_commits"][head];
    let mut pulls = numbers(&snapshot["pulls"]);
    pulls.sort_unstable();
    proof["landed"] == true
        && proof["pulls"] == json!(pulls)
        && proof["contract_sha256"].as_str() == Some(&digest(&snapshot["contract"]))
}

fn landed(pull: &Value) -> bool {
    pull["_landed"] == true
}

fn merged(pull: &Value) -> bool {
    pull["merged_at"].as_str().is_some_and(|at| !at.is_empty())
}

fn update(result: &mut Value, pairs: Value) {
    if let (Some(target), Value::Object(pairs)) = (result.as_object_mut(), pairs) {
        target.extend(pairs);
    }
}

/// One next action for the snapshot. The caller must have read every input
/// from the code host and must read them again before a write.
pub fn evaluate(snapshot: &Value) -> PortResult<Value> {
    let milestone = &snapshot["milestone"];
    let item = &snapshot["contract"];
    let mut result = json!({
        "schema": ASSESSMENT,
        "repo": snapshot["repo"],
        "milestone": milestone["number"],
        "head": snapshot["head"],
        "version": item["version"],
        "release_head": snapshot["head"],
        "state": "hold",
        "action": null,
        "reasons": [],
        "fingerprint": digest(snapshot),
    });
    if milestone["state"] == "closed" {
        let mut open_view = snapshot.clone();
        open_view["milestone"]["state"] = json!("open");
        if evaluate(&open_view)?["state"] == "ready_close" {
            update(&mut result, json!({"state": "complete"}));
        } else {
            let why = "milestone was closed without verified final release completion";
            update(&mut result, json!({"reasons": [why]}));
        }
        return Ok(result);
    }
    require(milestone["state"] == "open", "unknown milestone state")?;
    let issues = snapshot["issues"]
        .as_array()
        .filter(|rows| !rows.is_empty());
    require(issues.is_some(), "milestone issue inventory is empty")?;
    let issues = issues.map(Vec::as_slice).unwrap_or_default();
    let all = numbers(&snapshot["issues"]);
    let known: BTreeSet<u64> = all.iter().copied().collect();
    require(
        all.len() == issues.len() && known.len() == all.len(),
        "duplicate milestone issue",
    )?;
    require(
        issues
            .iter()
            .all(|i| i["state"] == "open" || i["state"] == "closed"),
        "unknown issue state",
    )?;
    let required: BTreeSet<u64> = item["acceptance"]
        .as_array()
        .into_iter()
        .flatten()
        .flat_map(|goal| goal["issues"].as_array().into_iter().flatten())
        .filter_map(Value::as_u64)
        .collect();
    require(
        required.is_subset(&known),
        "goal evidence references an issue outside this milestone",
    )?;
    let pulls = snapshot["pulls"]
        .as_array()
        .map(Vec::as_slice)
        .unwrap_or_default();
    let mut open_issues: Vec<u64> = issues
        .iter()
        .filter(|i| i["state"] != "closed")
        .filter_map(|i| i["number"].as_u64())
        .collect();
    open_issues.sort_unstable();
    let mut unmerged: Vec<u64> = pulls
        .iter()
        .filter(|p| !merged(p) || !landed(p))
        .filter_map(|p| p["number"].as_u64())
        .collect();
    unmerged.sort_unstable();
    if !open_issues.is_empty() || !unmerged.is_empty() {
        let reasons = [
            format!("open issues: {open_issues:?}"),
            format!("unmerged or unlanded PRs: {unmerged:?}"),
        ];
        update(&mut result, json!({"state": "work", "reasons": reasons}));
        return Ok(result);
    }
    let represented: BTreeSet<u64> = pulls
        .iter()
        .filter(|p| landed(p))
        .flat_map(|p| p["_issues"].as_array().into_iter().flatten())
        .filter_map(Value::as_u64)
        .collect();
    require(
        required.is_subset(&represented),
        "closed goal issue lacks a merged landed PR",
    )?;
    let version = item["version"].as_str().unwrap_or_default();
    let tags: Option<BTreeMap<&str, &str>> = snapshot["tags"].as_object().and_then(|map| {
        map.iter()
            .map(|(name, sha)| {
                sha.as_str()
                    .filter(|s| is_sha(s))
                    .map(|s| (name.as_str(), s))
            })
            .collect()
    });
    require(tags.is_some(), "invalid tag inventory")?;
    let tags = tags.unwrap_or_default();
    let final_tag = format!("v{version}");
    let rc = tags
        .iter()
        .filter_map(|(name, sha)| rc_number(&final_tag, name).map(|n| (n, *name, *sha)))
        .max();
    let next_rc = |n: u64| format!("{final_tag}-rc.{n}");
    let evidence = &snapshot["evidence"];
    let active = if let Some(final_sha) = tags.get(final_tag.as_str()) {
        let rc = rc.filter(|rc| rc.2 == *final_sha);
        require(rc.is_some(), "formal tag is not the latest RC commit")?;
        let (_, rc_name, rc_sha) = rc.unwrap_or_default();
        require(
            covers_release(snapshot, rc_sha),
            "formal commit lacks current landed goal work or release contract",
        )?;
        require(
            evidence[rc_name]["status"] == "success",
            "formal tag lacks passing RC evidence",
        )?;
        final_tag.as_str()
    } else if let Some((_, name, _)) = rc {
        name
    } else {
        update(
            &mut result,
            json!({"state": "ready_rc", "action": "tag", "tag": next_rc(1)}),
        );
        return Ok(result);
    };
    let is_final = active == final_tag;
    let (rc_n, _, rc_sha) = rc.unwrap_or_default();
    let active_sha = tags[active];
    update(&mut result, json!({"release_head": active_sha}));
    let missing = json!({"status": "pending", "reasons": ["full test run is missing"]});
    let proof = if evidence[active].is_object() {
        &evidence[active]
    } else {
        &missing
    };
    let status = proof["status"].as_str().unwrap_or_default();
    let reasons = if proof["reasons"].is_array() {
        proof["reasons"].clone()
    } else {
        json!([])
    };
    let new_rc = json!({
        "state": "ready_rc", "action": "tag", "tag": next_rc(rc_n + 1),
        "release_head": snapshot["head"],
    });
    match status {
        "pending" => update(
            &mut result,
            json!({"state": "testing", "tag": active, "reasons": reasons}),
        ),
        "failure" => {
            let number = milestone["number"].as_u64().unwrap_or(0);
            let repo = snapshot["repo"].as_str().unwrap_or_default();
            let marker = repair_marker(repo, number, active, active_sha, proof);
            let repairs: Vec<&Value> = issues
                .iter()
                .filter(|i| i["body"].as_str().is_some_and(|b| b.starts_with(&marker)))
                .collect();
            let repaired = !repairs.is_empty()
                && repairs.iter().all(|i| {
                    i["state"] == "closed"
                        && i["number"]
                            .as_u64()
                            .is_some_and(|n| represented.contains(&n))
                });
            if !is_final && snapshot["head"].as_str() != Some(rc_sha) && repaired {
                update(&mut result, new_rc);
            } else {
                let failures = if proof["failures"].is_array() {
                    proof["failures"].clone()
                } else {
                    json!([])
                };
                let mut reasons = reasons;
                if is_final && let Some(rows) = reasons.as_array_mut() {
                    rows.push(json!(
                        "Keep the formal tag. A code fix needs a new reviewed SemVer version."
                    ));
                }
                update(
                    &mut result,
                    json!({
                        "state": "repair", "action": "failure_issue", "tag": active,
                        "reasons": reasons, "failures": failures,
                    }),
                );
            }
        }
        "success" if !is_final => {
            if covers_release(snapshot, rc_sha) {
                update(
                    &mut result,
                    json!({"state": "ready_final", "action": "tag", "tag": final_tag}),
                );
            } else {
                update(&mut result, new_rc);
            }
        }
        "success" => {
            let publication = &snapshot["publication"];
            if publication.is_null() {
                update(
                    &mut result,
                    json!({"state": "ready_publish", "action": "publish", "tag": final_tag}),
                );
            } else {
                require(
                    publication["tag_name"].as_str() == Some(&final_tag)
                        && publication["draft"] == false
                        && publication["prerelease"] == false
                        && publication["published_at"]
                            .as_str()
                            .is_some_and(|at| !at.is_empty()),
                    "publication is not a final published release",
                )?;
                update(
                    &mut result,
                    json!({"state": "ready_close", "action": "close", "tag": final_tag}),
                );
            }
        }
        _ => require(false, "unknown full-test verdict")?,
    }
    Ok(result)
}
