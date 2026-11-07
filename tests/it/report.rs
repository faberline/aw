use aw::application::work::{ClaimOutcome, claim, release, report, status};
use aw::domain::work::signal::Outcome;
use aw::domain::work::stage::Stage;

use crate::support::World;

fn claim_issue(w: &World, agent: &str) {
    match claim(w.services(), agent).unwrap() {
        ClaimOutcome::Claimed(_) => {}
        ClaimOutcome::Nothing(why) => panic!("nothing to claim: {why}"),
    }
    w.workspace.calls.borrow_mut().clear();
    w.tracker.clear_calls();
}

#[test]
fn spec_ready_turns_the_claim_into_the_signal() {
    let w = World::new();
    w.tracker.issue(1, &["agent:codex", "stage:spec"]);
    claim_issue(&w, "codex");
    let r = report(w.services(), "codex", Outcome::Ready, "spec written").unwrap();
    assert_eq!(r.stage, Stage::Spec);
    assert_eq!(r.link, None);
    assert_eq!(w.tracker.calls(), ["edit_comment #1"]);
    let signals = w.tracker.signals(1);
    assert_eq!(signals.len(), 1);
    assert_eq!(signals[0].outcome, Outcome::Ready);
    assert!(w.tracker.last_comment(1).contains("spec written"));
    assert!(w.tracker.claims(1).is_empty());
    assert_eq!(status(w.services(), "codex").unwrap(), None);
    assert!(w.workspace.calls().is_empty(), "{:?}", w.workspace.calls());
}

#[test]
fn tdd_ready_pushes_and_opens_a_closing_pull_request() {
    let w = World::new();
    w.tracker.issue(7, &["agent:codex", "stage:tdd"]);
    claim_issue(&w, "codex");
    let r = report(w.services(), "codex", Outcome::Ready, "## Summary\nDone").unwrap();
    assert_eq!(w.workspace.calls()[..2], ["status", "push codex/issue-7"]);
    assert_eq!(
        w.tracker.calls(),
        [
            "create_pr codex/issue-7 -> main: Issue 7",
            "edit_comment #7"
        ]
    );
    let pr = w.tracker.prs.borrow()[0].clone();
    assert_eq!(pr.body, "Closes #7\n\n## Summary\nDone");
    assert_eq!(r.link.as_deref(), Some(pr.url.as_str()));
    assert!(w.tracker.last_comment(7).contains(&pr.url));
    // tdd goes back to the default branch.
    assert!(w.workspace.calls().contains(&"switch main".to_string()));
}

#[test]
fn tdd_ready_updates_an_open_pull_request() {
    let w = World::new();
    w.tracker.issue(7, &["agent:codex", "stage:tdd"]);
    w.tracker.pr(5, "codex/issue-7", "Closes #7\n\nold");
    claim_issue(&w, "codex");
    report(w.services(), "codex", Outcome::Ready, "new").unwrap();
    assert_eq!(w.tracker.calls(), ["edit_pr #5", "edit_comment #7"]);
    assert_eq!(w.tracker.prs.borrow()[0].body, "Closes #7\n\nnew");
}

#[test]
fn tdd_ready_refuses_a_checkout_of_another_repository() {
    let w = World::new();
    w.tracker.issue(7, &["agent:codex", "stage:tdd"]);
    claim_issue(&w, "codex");
    w.workspace.origin.replace(Some("o/other".into()));
    assert!(report(w.services(), "codex", Outcome::Ready, "").is_err());
    assert!(w.workspace.calls().is_empty());
    assert_eq!(w.tracker.claims(7).len(), 1);
}

#[test]
fn review_passed_approves_and_squash_merges_without_git() {
    let w = World::new();
    w.tracker.issue(7, &["agent:claude", "stage:review"]);
    w.tracker.pr(5, "codex/issue-7", "Closes #7");
    claim_issue(&w, "claude");
    w.workspace.origin.replace(None);
    report(w.services(), "claude", Outcome::Passed, "").unwrap();
    assert_eq!(
        w.tracker.calls(),
        [
            "approve #5",
            "merge #5: Issue 7 (#5) | Closes #7",
            "edit_comment #7"
        ]
    );
    assert!(w.workspace.calls().is_empty());
}

#[test]
fn review_failed_requests_changes_or_falls_back_to_a_comment() {
    let w = World::new();
    w.tracker.issue(7, &["agent:claude", "stage:review"]);
    w.tracker.pr(5, "codex/issue-7", "Closes #7");
    claim_issue(&w, "claude");
    w.tracker.fail_request_changes.set(true);
    report(w.services(), "claude", Outcome::Failed, "tests missing").unwrap();
    assert_eq!(w.tracker.calls(), ["pr_comment #5", "edit_comment #7"]);
    assert!(w.tracker.last_comment(7).contains("tests missing"));
}

#[test]
fn outcome_must_fit_the_stage_and_failures_need_a_message() {
    let w = World::new();
    w.tracker.issue(1, &["agent:codex", "stage:spec"]);
    claim_issue(&w, "codex");
    assert!(report(w.services(), "codex", Outcome::Passed, "x").is_err());
    assert!(report(w.services(), "codex", Outcome::Blocked, "  ").is_err());
    assert!(w.tracker.calls().is_empty());
    assert_eq!(w.tracker.claims(1).len(), 1);
}

#[test]
fn tdd_with_a_dirty_tree_stays_put_with_a_warning() {
    let w = World::new();
    w.tracker.issue(1, &["agent:codex", "stage:tdd"]);
    claim_issue(&w, "codex");
    w.workspace.clean.set(false);
    let r = report(w.services(), "codex", Outcome::Blocked, "need input").unwrap();
    assert_eq!(r.warnings.len(), 1);
    assert_eq!(w.workspace.calls(), ["status"]);
    assert!(w.tracker.claims(1).is_empty());
}

#[test]
fn report_without_a_claim_is_an_error() {
    let w = World::new();
    w.tracker.issue(1, &["agent:codex", "stage:spec"]);
    assert!(report(w.services(), "codex", Outcome::Ready, "").is_err());
}

#[test]
fn report_only_sees_the_agents_own_claim() {
    let w = World::new();
    w.tracker
        .issue(1, &["agent:codex", "agent:claude", "stage:spec"]);
    claim_issue(&w, "codex");
    assert!(report(w.services(), "claude", Outcome::Ready, "").is_err());
    assert_eq!(w.tracker.claims(1).len(), 1);
}

#[test]
fn release_deletes_the_claim_without_a_signal() {
    let w = World::new();
    w.tracker.issue(1, &["agent:codex", "stage:spec"]);
    claim_issue(&w, "codex");
    let (c, _) = release(w.services(), "codex").unwrap();
    assert_eq!(c.issue, 1);
    assert_eq!(w.tracker.calls(), ["delete_comment #1"]);
    assert!(w.tracker.signals(1).is_empty());
    assert!(w.tracker.claims(1).is_empty());
    assert!(w.workspace.calls().is_empty());
    assert!(release(w.services(), "codex").is_err());
}

#[test]
fn tdd_ready_refuses_uncommitted_changes() {
    let w = World::new();
    w.tracker.issue(7, &["agent:codex", "stage:tdd"]);
    claim_issue(&w, "codex");
    w.workspace.clean.set(false);
    assert!(report(w.services(), "codex", Outcome::Ready, "").is_err());
    assert_eq!(w.workspace.calls(), ["status"]);
    assert_eq!(w.tracker.claims(7).len(), 1);
}
