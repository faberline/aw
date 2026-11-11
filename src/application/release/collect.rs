//! Read one snapshot of a milestone from the code host. Every file comes
//! from one exact commit. The snapshot is the only input of the decision.

use std::collections::{BTreeMap, BTreeSet};

use serde_json::{Map, Value, json};

use super::api::Api;
use super::evidence::{Gate, entry, full_evidence};
use crate::domain::release::contract::{CONTRACT_PATH, contract};
use crate::domain::release::digest::{digest, is_sha};
use crate::domain::release::policy::{POLICY_PATH, Policy};
use crate::domain::release::tag::{closes, encode, rc_number};
use crate::domain::release::{fail, require};
use crate::domain::work::ports::PortResult;

/// Is one commit an ancestor of another? Each pair is read one time.
struct Ancestry<'a> {
    api: &'a Api<'a>,
    known: BTreeMap<(String, String), bool>,
}

impl Ancestry<'_> {
    fn holds(&mut self, commit: Option<&str>, target: &str) -> PortResult<bool> {
        let Some(commit) = commit.filter(|c| is_sha(c)) else {
            return Err(fail("merged PR has no exact commit"));
        };
        if commit == target {
            return Ok(true);
        }
        let key = (commit.to_string(), target.to_string());
        if let Some(known) = self.known.get(&key) {
            return Ok(*known);
        }
        let compared = self.api.get(&format!("/compare/{commit}...{target}"))?;
        let holds = compared["behind_by"].as_u64() == Some(0)
            && compared["merge_base_commit"]["sha"] == commit;
        self.known.insert(key, holds);
        Ok(holds)
    }
}

fn merged(pull: &Value) -> bool {
    pull["merged_at"].as_str().is_some_and(|at| !at.is_empty())
}

/// The snapshot of milestone `number`: the contract, the issues, the pull
/// requests, the tags, the proof of the latest candidate and of the final
/// tag, and the publication. It stops when an input is missing, changed or
/// unknown, and when the default branch moves during the reads.
pub fn collect(g: &Gate<'_>, number: u64) -> PortResult<Value> {
    require(number > 0, "existing milestone number required")?;
    let api = g.api;
    let repo = api.repo();
    let head = api.default_head()?;
    let (release_config, release_blob) = api.json(CONTRACT_PATH, &head)?;
    let (policy_value, policy_blob) = api.json(POLICY_PATH, &head)?;
    let policy = Policy::parse(&policy_value, repo)?;
    let milestone = api.get(&format!("/milestones/{number}"))?;
    require(
        milestone["number"].as_u64() == Some(number),
        "milestone identity differs",
    )?;
    let item = contract(&release_config, repo, &milestone, &policy.checks())?;
    let issues: Vec<Value> = api
        .pages(&format!("/issues?state=all&milestone={number}"), None)?
        .into_iter()
        .filter(|row| row.get("pull_request").is_none())
        .collect();
    let numbers: BTreeSet<u64> = issues.iter().filter_map(|i| i["number"].as_u64()).collect();
    let mut ancestry = Ancestry {
        api,
        known: BTreeMap::new(),
    };
    let mut pulls: Vec<Value> = Vec::new();
    for mut pull in api.pages("/pulls?state=all", None)? {
        let related: BTreeSet<u64> = closes(pull["body"].as_str().unwrap_or_default())
            .into_iter()
            .filter(|n| numbers.contains(n))
            .collect();
        if related.is_empty() && pull["milestone"]["number"].as_u64() != Some(number) {
            continue;
        }
        let landed = merged(&pull) && ancestry.holds(pull["merge_commit_sha"].as_str(), &head)?;
        let Some(fields) = pull.as_object_mut() else {
            return Err(fail("invalid pull request inventory"));
        };
        fields.insert("_issues".to_string(), json!(related));
        fields.insert("_landed".to_string(), json!(landed));
        pulls.push(pull);
    }
    let mut tags: BTreeMap<String, String> = BTreeMap::new();
    for row in api.pages("/tags", None)? {
        let (Some(name), Some(sha)) = (row["name"].as_str(), row["commit"]["sha"].as_str()) else {
            return Err(fail("invalid tag inventory"));
        };
        require(
            is_sha(sha) && tags.insert(name.to_string(), sha.to_string()).is_none(),
            "invalid or duplicate tag inventory",
        )?;
    }
    let final_tag = format!("v{}", item["version"].as_str().unwrap_or_default());
    let latest = tags
        .keys()
        .filter_map(|name| rc_number(&final_tag, name).map(|n| (n, name.as_str())))
        .max();
    let proven: Vec<&str> = latest
        .map(|(_, name)| name)
        .into_iter()
        .chain(tags.contains_key(&final_tag).then_some(final_tag.as_str()))
        .collect();
    let mut evidence = Map::new();
    let mut release_commits = Map::new();
    for tag in &proven {
        let sha = tags[*tag].as_str();
        let (tagged_value, tagged_policy_blob) = api.json(POLICY_PATH, sha)?;
        let tagged_policy = Policy::parse(&tagged_value, repo)?;
        require(
            policy.retains(&tagged_policy),
            "current policy dropped or changed a prior release gate",
        )?;
        let (tagged_config, tagged_release_blob) = api.json(CONTRACT_PATH, sha)?;
        require(
            tagged_config["version"].as_u64() == Some(1)
                && tagged_config["repo"] == repo
                && tagged_config["milestones"].is_object(),
            "invalid tagged release configuration",
        )?;
        let tagged_contract = &tagged_config["milestones"][number.to_string()];
        let mut within: Vec<u64> = Vec::new();
        for pull in pulls.iter().filter(|p| merged(p)) {
            if ancestry.holds(pull["merge_commit_sha"].as_str(), sha)? {
                within.extend(pull["number"].as_u64());
            }
        }
        within.sort_unstable();
        release_commits.insert(
            sha.to_string(),
            json!({
                "landed": ancestry.holds(Some(sha), &head)?,
                "pulls": within,
                "contract_sha256": digest(tagged_contract),
                "plan_sha256": digest(&tagged_value),
                "release_blob": tagged_release_blob,
                "policy_blob": tagged_policy_blob,
            }),
        );
        evidence.insert(tag.to_string(), full_evidence(g, &tagged_policy, tag, sha)?);
    }
    if proven.is_empty() {
        // Check the entry before a tag that cannot start the full tests.
        entry(g, &policy, &head, &format!("{final_tag}-rc.1"))?;
    }
    let publication = if tags.contains_key(&final_tag) {
        api.optional(&format!("/releases/tags/{}", encode(&final_tag)))?
    } else {
        None
    };
    let snapshot = json!({
        "repo": repo, "head": head, "milestone": milestone, "contract": item,
        "issues": issues, "pulls": pulls, "tags": tags, "evidence": evidence,
        "publication": publication, "release_commits": release_commits,
        "plan_sha256": digest(&policy_value), "release_blob": release_blob,
        "policy_blob": policy_blob,
    });
    require(
        api.default_head()? == head,
        "default branch changed during release assessment",
    )?;
    Ok(snapshot)
}
