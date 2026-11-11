//! Use cases of the milestone release gate: assess a milestone, do the one
//! next action, and prove a tag inside its own workflow run.
//! `docs/standards/release-tooling.md` is the standard.

pub mod api;
pub mod collect;
pub mod evidence;

use serde_json::{Value, json};

use crate::domain::release::contract::CONTRACT_PATH;
use crate::domain::release::digest::{digest, is_sha, is_sha256};
use crate::domain::release::policy::{POLICY_PATH, Policy};
use crate::domain::release::ports::{Provider, WorkflowParser};
use crate::domain::release::state::{covers_release, evaluate};
use crate::domain::release::tag::{encode, release_version};
use crate::domain::release::{fail, require};
use crate::domain::work::ports::{PortError, PortResult};

use api::Api;
use collect::collect;
use evidence::{Gate, entry};

pub const PREFLIGHT: &str = "AW_RELEASE_PREFLIGHT_V1";

/// The ports the release gate uses. The composition root builds it.
#[derive(Clone, Copy)]
pub struct ReleaseServices<'a> {
    pub provider: &'a dyn Provider,
    pub workflows: &'a dyn WorkflowParser,
}

/// The result of `advance`.
#[derive(Debug, PartialEq)]
pub enum Advanced {
    /// The one write was done and read back. The value is the record.
    Done(Value),
    /// No write. The value is the assessment.
    Nothing(Value),
}

/// Assess milestone `milestone`: the state, the one next action and the
/// fingerprint of every input. It only reads.
pub fn status(s: ReleaseServices<'_>, milestone: u64) -> PortResult<Value> {
    let api = Api::new(s.provider)?;
    let gate = Gate {
        api: &api,
        workflows: s.workflows,
    };
    evaluate(&collect(&gate, milestone)?)
}

/// Do the one next action of milestone `milestone`, when the inputs still
/// have the fingerprint `expected`. This version writes only a tag; for
/// each other action it returns the assessment and writes nothing.
pub fn advance(s: ReleaseServices<'_>, milestone: u64, expected: &str) -> PortResult<Advanced> {
    require(is_sha256(expected), "invalid assessment fingerprint")?;
    let api = Api::new(s.provider)?;
    let gate = Gate {
        api: &api,
        workflows: s.workflows,
    };
    let snapshot = collect(&gate, milestone)?;
    let assessment = evaluate(&snapshot)?;
    require(
        assessment["fingerprint"] == expected,
        "release inputs changed; assess again",
    )?;
    if assessment["action"] != "tag" {
        return Ok(Advanced::Nothing(assessment));
    }
    let tag = assessment["tag"].as_str().unwrap_or_default();
    let release_head = assessment["release_head"].as_str().unwrap_or_default();
    require(
        release_version(tag).is_some() && is_sha(release_head),
        "invalid tag action",
    )?;
    require(
        snapshot["tags"].get(tag).is_none(),
        "tag exists; never overwrite",
    )?;
    // The tag must start the reviewed full tests at its own commit.
    let (tagged_policy, _) = api.json(POLICY_PATH, release_head)?;
    entry(
        &gate,
        &Policy::parse(&tagged_policy, api.repo())?,
        release_head,
        tag,
    )?;
    require(
        digest(&collect(&gate, milestone)?) == expected,
        "release changed before write",
    )?;
    let response = api.create_tag(tag, release_head)?;
    require(
        response["ref"] == format!("refs/tags/{tag}") && response["object"]["sha"] == release_head,
        "tag write outcome unknown; inspect before retry",
    )?;
    let actual = api.get(&format!("/git/ref/tags/{}", encode(tag)))?;
    require(
        actual["object"]["sha"] == release_head,
        "tag readback differs",
    )?;
    Ok(Advanced::Done(json!({
        "action": "tag", "milestone": milestone, "head": snapshot["head"],
        "release_head": release_head, "tag": tag, "verified": true,
    })))
}

/// The milestone whose contract has the version of `tag`, read from the
/// default branch. There must be exactly one.
fn milestone_of(api: &Api<'_>, tag: &str) -> PortResult<u64> {
    let (config, _) = api.json(CONTRACT_PATH, &api.default_head()?)?;
    let version = release_version(tag);
    let found: Option<Vec<u64>> = config["milestones"]
        .as_object()
        .into_iter()
        .flatten()
        .filter(|(_, item)| item["version"].as_str() == version)
        .map(|(number, _)| number.parse().ok().filter(|n| *n > 0))
        .collect();
    match found.as_deref() {
        Some([number]) => Ok(*number),
        _ => Err(fail("release version has no unique existing milestone")),
    }
}

/// Prove, inside the run of `tag`, that the tag is at `commit`, that the
/// commit holds the approved goal work and that the milestone is ready for
/// this tag. It only reads. The result names the frozen policy and
/// contract blobs.
pub fn preflight(
    s: ReleaseServices<'_>,
    tag: &str,
    commit: &str,
    milestone: Option<u64>,
) -> PortResult<Value> {
    require(release_version(tag).is_some(), "invalid release tag")?;
    require(is_sha(commit), "exact release commit required")?;
    let api = Api::new(s.provider)?;
    let gate = Gate {
        api: &api,
        workflows: s.workflows,
    };
    let number = match milestone {
        Some(number) => number,
        None => milestone_of(&api, tag)?,
    };
    let snapshot = collect(&gate, number)?;
    require(
        snapshot["tags"][tag] == commit && covers_release(&snapshot, commit),
        "tag or frozen goal delivery changed",
    )?;
    let mut open = snapshot.clone();
    open["milestone"]["state"] = json!("open");
    let assessment = evaluate(&open)?;
    let final_tag = format!(
        "v{}",
        snapshot["contract"]["version"].as_str().unwrap_or_default()
    );
    let ready = matches!(
        assessment["state"].as_str(),
        Some("testing" | "ready_final" | "ready_publish" | "ready_close")
    ) && (assessment["tag"] == tag || assessment["tag"] == final_tag);
    if !ready {
        let reasons: Vec<&str> = assessment["reasons"]
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(Value::as_str)
            .collect();
        return Err(PortError(format!(
            "release: milestone work or release inputs are not ready: {}",
            reasons.join("; ")
        )));
    }
    let source = &snapshot["release_commits"][commit];
    require(source.is_object(), "release commit has no frozen proof")?;
    Ok(json!({
        "schema": PREFLIGHT, "status": "success", "mode": "all", "repo": api.repo(),
        "head": commit, "base": commit, "tag": tag, "milestone": number,
        "plan_sha256": source["plan_sha256"], "release_blob": source["release_blob"],
        "policy_blob": source["policy_blob"], "qualification": "not_evaluated",
    }))
}
