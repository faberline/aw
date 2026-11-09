use aw::application::work::{ClaimOutcome, claim, release, report, status};
use aw::domain::work::signal::Outcome;
use aw::domain::work::stage::Stage;

use crate::support::World;

fn claim_issue(w: &World, agent: &str) {
    match claim(w.services(), agent, None, &[]).unwrap() {
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
    let r = report(w.services(), "codex", None, Outcome::Ready, "spec written").unwrap();
    assert_eq!(r.stage, Stage::Spec);
    assert_eq!(r.link, None);
    assert_eq!(
        w.tracker.calls(),
        [
            "edit_comment #1",
            "add_labels #1 stage:tdd",
            "remove_label #1 stage:spec"
        ]
    );
    let signals = w.tracker.signals(1);
    assert_eq!(signals.len(), 1);
    assert_eq!(signals[0].outcome, Outcome::Ready);
    assert!(w.tracker.last_comment(1).contains("spec written"));
    assert!(w.tracker.claims(1).is_empty());
    assert_eq!(status(w.services(), "codex", None).unwrap(), None);
    assert!(w.workspace.calls().is_empty(), "{:?}", w.workspace.calls());
}

#[test]
fn tdd_ready_pushes_and_opens_a_closing_pull_request() {
    let w = World::new();
    w.tracker.issue(7, &["agent:codex", "stage:tdd"]);
    claim_issue(&w, "codex");
    let r = report(
        w.services(),
        "codex",
        None,
        Outcome::Ready,
        "## Summary\nDone",
    )
    .unwrap();
    assert_eq!(w.workspace.calls()[..2], ["status", "push codex/issue-7"]);
    assert_eq!(
        w.tracker.calls(),
        [
            "create_pr codex/issue-7 -> main: Issue 7",
            "edit_comment #7",
            "add_labels #7 stage:review",
            "remove_label #7 stage:tdd"
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
fn tdd_ready_detaches_when_the_default_branch_is_in_another_worktree() {
    let w = World::new();
    w.tracker.issue(7, &["agent:codex", "stage:tdd"]);
    claim_issue(&w, "codex");
    w.workspace.switch_fails.set(true);
    let r = report(w.services(), "codex", None, Outcome::Ready, "done").unwrap();
    let calls = w.workspace.calls();
    assert!(calls.contains(&"switch main".to_string()), "{calls:?}");
    assert_eq!(calls.last().map(String::as_str), Some("detach origin/main"));
    assert!(
        r.warnings
            .iter()
            .any(|w| w.contains("detached at `origin/main`")),
        "{:?}",
        r.warnings
    );
}

#[test]
fn tdd_ready_updates_an_open_pull_request() {
    let w = World::new();
    w.tracker.issue(7, &["agent:codex", "stage:tdd"]);
    w.tracker.pr(5, "codex/issue-7", "Closes #7\n\nold");
    claim_issue(&w, "codex");
    report(w.services(), "codex", None, Outcome::Ready, "new").unwrap();
    assert_eq!(
        w.tracker.calls(),
        [
            "edit_pr #5",
            "edit_comment #7",
            "add_labels #7 stage:review",
            "remove_label #7 stage:tdd"
        ]
    );
    assert_eq!(w.tracker.prs.borrow()[0].body, "Closes #7\n\nnew");
}

#[test]
fn tdd_ready_refuses_a_checkout_of_another_repository() {
    let w = World::new();
    w.tracker.issue(7, &["agent:codex", "stage:tdd"]);
    claim_issue(&w, "codex");
    w.workspace.origin.replace(Some("o/other".into()));
    assert!(report(w.services(), "codex", None, Outcome::Ready, "").is_err());
    assert!(w.workspace.calls().is_empty());
    assert_eq!(w.tracker.claims(7).len(), 1);
}

#[test]
fn review_passed_squash_merges_without_approval_or_git() {
    let w = World::new();
    w.tracker.issue(7, &["agent:claude", "stage:review"]);
    w.tracker.pr(5, "codex/issue-7", "Closes #7");
    claim_issue(&w, "claude");
    w.workspace.origin.replace(None);
    report(w.services(), "claude", None, Outcome::Passed, "").unwrap();
    assert_eq!(
        w.tracker.calls(),
        [
            "merge #5: Issue 7 (#5) | Closes #7",
            "edit_comment #7",
            "add_labels #7 stage:done",
            "remove_label #7 stage:review"
        ]
    );
    assert!(w.workspace.calls().is_empty());
}

#[test]
fn review_passed_behind_base_updates_the_branch_and_stays_at_review() {
    let w = World::new();
    w.tracker.issue(7, &["agent:claude", "stage:review"]);
    w.tracker.pr(5, "claude/issue-7", "Closes #7");
    claim_issue(&w, "claude");
    w.tracker.fail_merge.set(true);
    w.tracker.behind.set(3);
    let reported = report(w.services(), "claude", None, Outcome::Passed, "ok").unwrap();
    assert!(reported.updated_branch);
    assert_eq!(
        reported.link.as_deref(),
        Some("https://github.com/o/r/pull/5")
    );
    assert_eq!(w.tracker.calls(), ["update_branch #5", "edit_comment #7"]);
    // The claim is gone, no signal is posted and the stage label stays.
    assert!(w.tracker.claims(7).is_empty());
    assert!(w.tracker.signals(7).is_empty());
    assert!(w.tracker.last_comment(7).contains("branch updated"));
    let labels = w.tracker.issues.borrow()[0].labels.clone();
    assert_eq!(labels, ["agent:claude", "stage:review"]);
}

#[test]
fn review_passed_behind_base_waits_while_another_review_pr_is_up_to_date() {
    let w = World::new();
    w.tracker.issue(7, &["agent:claude", "stage:review"]);
    w.tracker.pr(5, "claude/issue-7", "Closes #7");
    w.tracker.issue(8, &["agent:claude", "stage:review"]);
    w.tracker.pr(6, "claude/issue-8", "Closes #8");
    claim_issue(&w, "claude");
    w.tracker.fail_merge.set(true);
    w.tracker.behind.set(3);
    w.tracker
        .behind_heads
        .borrow_mut()
        .insert("claude/issue-8".into(), 0);
    let reported = report(w.services(), "claude", None, Outcome::Passed, "ok").unwrap();
    assert!(!reported.updated_branch);
    assert_eq!(reported.waiting_for, Some(6));
    // No branch update: only the claim turns into the note.
    assert_eq!(w.tracker.calls(), ["edit_comment #7"]);
    assert!(w.tracker.claims(7).is_empty());
    assert!(w.tracker.signals(7).is_empty());
    assert!(w.tracker.last_comment(7).contains("waiting to update"));
    let labels = w.tracker.issues.borrow()[0].labels.clone();
    assert_eq!(labels, ["agent:claude", "stage:review"]);
    let json = serde_json::to_value(&reported).unwrap();
    assert_eq!(json["waiting_for"], 6);
}

#[test]
fn review_passed_behind_base_updates_when_other_review_prs_are_behind_blocked_or_red() {
    let w = World::new();
    w.tracker.issue(7, &["agent:claude", "stage:review"]);
    w.tracker.pr(5, "claude/issue-7", "Closes #7");
    // Behind like this one: it does not go first.
    w.tracker.issue(8, &["agent:claude", "stage:review"]);
    w.tracker.pr(6, "claude/issue-8", "Closes #8");
    // Up to date, but blocked: it does not merge next.
    w.tracker
        .issue(9, &["agent:claude", "stage:review", "blocked"]);
    w.tracker.pr(4, "claude/issue-9", "Closes #9");
    // Up to date, but not at review.
    w.tracker.issue(10, &["agent:claude", "stage:tdd"]);
    w.tracker.pr(3, "claude/issue-10", "Closes #10");
    // Up to date, but a check failed: it cannot merge next.
    w.tracker.issue(11, &["agent:claude", "stage:review"]);
    w.tracker.pr(2, "claude/issue-11", "Closes #11");
    w.tracker
        .red_heads
        .borrow_mut()
        .push("claude/issue-11".into());
    claim_issue(&w, "claude");
    w.tracker.fail_merge.set(true);
    w.tracker.behind.set(3);
    for head in ["claude/issue-9", "claude/issue-10", "claude/issue-11"] {
        w.tracker.behind_heads.borrow_mut().insert(head.into(), 0);
    }
    let reported = report(w.services(), "claude", None, Outcome::Passed, "ok").unwrap();
    assert!(reported.updated_branch);
    assert_eq!(reported.waiting_for, None);
    assert_eq!(w.tracker.calls(), ["update_branch #5", "edit_comment #7"]);
}

#[test]
fn review_passed_merge_failure_up_to_date_is_an_error() {
    let w = World::new();
    w.tracker.issue(7, &["agent:claude", "stage:review"]);
    w.tracker.pr(5, "claude/issue-7", "Closes #7");
    claim_issue(&w, "claude");
    w.tracker.fail_merge.set(true);
    let err = report(w.services(), "claude", None, Outcome::Passed, "").unwrap_err();
    assert!(format!("{err:#}").contains("could not merge #5"));
    assert!(w.tracker.calls().is_empty());
    assert_eq!(w.tracker.claims(7).len(), 1);
}

#[test]
fn review_failed_requests_changes_or_falls_back_to_a_comment() {
    let w = World::new();
    w.tracker.issue(7, &["agent:claude", "stage:review"]);
    w.tracker.pr(5, "codex/issue-7", "Closes #7");
    claim_issue(&w, "claude");
    w.tracker.fail_request_changes.set(true);
    report(
        w.services(),
        "claude",
        None,
        Outcome::Failed,
        "tests missing",
    )
    .unwrap();
    assert_eq!(
        w.tracker.calls(),
        [
            "pr_comment #5",
            "edit_comment #7",
            "add_labels #7 stage:tdd",
            "remove_label #7 stage:review"
        ]
    );
    assert!(w.tracker.last_comment(7).contains("tests missing"));
}

#[test]
fn outcome_must_fit_the_stage_and_failures_need_a_message() {
    let w = World::new();
    w.tracker.issue(1, &["agent:codex", "stage:spec"]);
    claim_issue(&w, "codex");
    assert!(report(w.services(), "codex", None, Outcome::Passed, "x").is_err());
    assert!(report(w.services(), "codex", None, Outcome::Blocked, "  ").is_err());
    assert!(w.tracker.calls().is_empty());
    assert_eq!(w.tracker.claims(1).len(), 1);
}

#[test]
fn tdd_with_a_dirty_tree_stays_put_with_a_warning() {
    let w = World::new();
    w.tracker.issue(1, &["agent:codex", "stage:tdd"]);
    claim_issue(&w, "codex");
    w.workspace.clean.set(false);
    let r = report(w.services(), "codex", None, Outcome::Blocked, "need input").unwrap();
    assert_eq!(r.warnings.len(), 1);
    assert_eq!(w.workspace.calls(), ["status"]);
    assert!(w.tracker.claims(1).is_empty());
}

#[test]
fn report_without_a_claim_is_an_error() {
    let w = World::new();
    w.tracker.issue(1, &["agent:codex", "stage:spec"]);
    assert!(report(w.services(), "codex", None, Outcome::Ready, "").is_err());
}

#[test]
fn report_only_sees_the_agents_own_claim() {
    let w = World::new();
    w.tracker
        .issue(1, &["agent:codex", "agent:claude", "stage:spec"]);
    claim_issue(&w, "codex");
    assert!(report(w.services(), "claude", None, Outcome::Ready, "").is_err());
    assert_eq!(w.tracker.claims(1).len(), 1);
}

#[test]
fn release_deletes_the_claim_without_a_signal() {
    let w = World::new();
    w.tracker.issue(1, &["agent:codex", "stage:spec"]);
    claim_issue(&w, "codex");
    let (c, _) = release(w.services(), "codex", None).unwrap();
    assert_eq!(c.issue, 1);
    assert_eq!(w.tracker.calls(), ["delete_comment #1"]);
    assert!(w.tracker.signals(1).is_empty());
    assert!(w.tracker.claims(1).is_empty());
    assert!(w.workspace.calls().is_empty());
    assert!(release(w.services(), "codex", None).is_err());
}

#[test]
fn tdd_ready_refuses_uncommitted_changes() {
    let w = World::new();
    w.tracker.issue(7, &["agent:codex", "stage:tdd"]);
    claim_issue(&w, "codex");
    w.workspace.clean.set(false);
    assert!(report(w.services(), "codex", None, Outcome::Ready, "").is_err());
    assert_eq!(w.workspace.calls(), ["status"]);
    assert_eq!(w.tracker.claims(7).len(), 1);
}

#[test]
fn the_same_agent_claims_the_next_stage_at_once() {
    let w = World::new();
    w.tracker.issue(1, &["agent:claude", "stage:spec"]);
    claim_issue(&w, "claude");
    report(w.services(), "claude", None, Outcome::Ready, "spec").unwrap();
    let labels = w.tracker.issues.borrow()[0].labels.clone();
    assert_eq!(labels, ["agent:claude", "stage:tdd"]);
    match claim(w.services(), "claude", None, &[]).unwrap() {
        ClaimOutcome::Claimed(c) => assert_eq!((c.issue, c.stage), (1, Stage::Tdd)),
        ClaimOutcome::Nothing(why) => panic!("nothing to claim: {why}"),
    }
}

#[test]
fn blocked_keeps_the_stage_and_adds_blocked() {
    let w = World::new();
    w.tracker.issue(1, &["agent:claude", "stage:spec"]);
    claim_issue(&w, "claude");
    report(
        w.services(),
        "claude",
        None,
        Outcome::Blocked,
        "needs a person",
    )
    .unwrap();
    let labels = w.tracker.issues.borrow()[0].labels.clone();
    assert_eq!(labels, ["agent:claude", "stage:spec", "blocked"]);
}

#[test]
fn a_stage_reports_and_releases_only_its_own_claim() {
    let w = World::new();
    w.tracker.issue(1, &["agent:claude", "stage:spec"]);
    w.tracker.issue(7, &["agent:claude", "stage:review"]);
    w.tracker.pr(5, "claude/issue-7", "Closes #7");
    for stage in [Stage::Spec, Stage::Review] {
        match claim(w.services(), "claude", Some(stage), &[]).unwrap() {
            ClaimOutcome::Claimed(_) => {}
            ClaimOutcome::Nothing(why) => panic!("nothing to claim: {why}"),
        }
    }
    let r = report(
        w.services(),
        "claude",
        Some(Stage::Spec),
        Outcome::Ready,
        "spec",
    )
    .unwrap();
    assert_eq!((r.issue, r.stage), (1, Stage::Spec));
    assert!(release(w.services(), "claude", Some(Stage::Spec)).is_err());
    let (c, _) = release(w.services(), "claude", Some(Stage::Review)).unwrap();
    assert_eq!((c.issue, c.stage), (7, Stage::Review));
    assert_eq!(status(w.services(), "claude", None).unwrap(), None);
}
