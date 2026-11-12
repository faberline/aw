use aw::application::work::{ClaimOutcome, claim, release, report, status};
use aw::domain::work::signal::Outcome;
use aw::domain::work::stage::Stage;

use crate::support::World;

fn claim_issue(w: &World, agent: &str) {
    match claim(w.services(), agent, None, &[], &[]).unwrap() {
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
fn review_passed_behind_base_updates_a_higher_priority_pr_before_an_up_to_date_lower_one() {
    let w = World::new();
    w.tracker.issue(7, &["agent:claude", "stage:review", "p0"]);
    w.tracker.pr(5, "claude/issue-7", "Closes #7");
    // Up to date and green, but p2: it does not hold back the p0 branch.
    w.tracker.issue(8, &["agent:claude", "stage:review", "p2"]);
    w.tracker.pr(6, "claude/issue-8", "Closes #8");
    claim_issue(&w, "claude");
    w.tracker.fail_merge.set(true);
    w.tracker.behind.set(3);
    w.tracker
        .behind_heads
        .borrow_mut()
        .insert("claude/issue-8".into(), 0);
    let reported = report(w.services(), "claude", None, Outcome::Passed, "ok").unwrap();
    assert!(reported.updated_branch);
    assert_eq!(reported.waiting_for, None);
    assert_eq!(w.tracker.calls(), ["update_branch #5", "edit_comment #7"]);
}

#[test]
fn review_passed_behind_base_waits_for_an_up_to_date_pr_of_the_same_or_higher_priority() {
    let w = World::new();
    w.tracker.issue(7, &["agent:claude", "stage:review", "p1"]);
    w.tracker.pr(5, "claude/issue-7", "Closes #7");
    // Other agents' review issues, so `claude` claims #7.
    w.tracker.issue(8, &["agent:codex", "stage:review", "p0"]);
    w.tracker.pr(6, "codex/issue-8", "Closes #8");
    w.tracker.issue(9, &["agent:codex", "stage:review", "p1"]);
    w.tracker.pr(4, "codex/issue-9", "Closes #9");
    claim_issue(&w, "claude");
    w.tracker.fail_merge.set(true);
    w.tracker.behind.set(3);
    w.tracker
        .behind_heads
        .borrow_mut()
        .insert("codex/issue-9".into(), 0);
    let reported = report(w.services(), "claude", None, Outcome::Passed, "ok").unwrap();
    assert!(!reported.updated_branch);
    assert_eq!(reported.waiting_for, Some(4));
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

/// #7 (claimed, behind) and #8 (up to date, same priority) at review.
fn queue_world() -> World {
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
    *w.tracker.required.borrow_mut() = vec!["gate".into()];
    w
}

#[test]
fn an_up_to_date_pr_without_the_required_check_does_not_block_the_update() {
    let w = queue_world();
    // Old CI names only: `gate` is missing on its head.
    w.tracker.run("claude/issue-8", "lint", false);
    let reported = report(w.services(), "claude", None, Outcome::Passed, "ok").unwrap();
    assert!(reported.updated_branch);
    assert_eq!(reported.waiting_for, None);
    assert_eq!(w.tracker.calls(), ["update_branch #5", "edit_comment #7"]);
}

#[test]
fn an_up_to_date_pr_with_a_failed_required_check_does_not_block_the_update() {
    let w = queue_world();
    w.tracker
        .red_heads
        .borrow_mut()
        .push("claude/issue-8".into());
    w.tracker.run("claude/issue-8", "gate", false);
    let reported = report(w.services(), "claude", None, Outcome::Passed, "ok").unwrap();
    assert!(reported.updated_branch);
}

#[test]
fn an_up_to_date_pr_with_the_required_check_running_blocks_the_update() {
    let w = queue_world();
    w.tracker.run("claude/issue-8", "gate", true);
    let reported = report(w.services(), "claude", None, Outcome::Passed, "ok").unwrap();
    assert!(!reported.updated_branch);
    assert_eq!(reported.waiting_for, Some(6));
    assert_eq!(w.tracker.calls(), ["edit_comment #7"]);
}

#[test]
fn an_up_to_date_pr_with_the_required_check_green_blocks_the_update() {
    let w = queue_world();
    w.tracker.run("claude/issue-8", "gate", false);
    let reported = report(w.services(), "claude", None, Outcome::Passed, "ok").unwrap();
    assert_eq!(reported.waiting_for, Some(6));
}

#[test]
fn unreadable_required_checks_keep_the_old_rule_and_say_so() {
    let w = queue_world();
    w.tracker.required_unreadable.set(true);
    // Old rule: up to date and not red holds the update back.
    let reported = report(w.services(), "claude", None, Outcome::Passed, "ok").unwrap();
    assert_eq!(reported.waiting_for, Some(6));
    assert!(
        reported
            .warnings
            .iter()
            .any(|x| x.contains("could not read the required checks"))
    );
    // Old rule: a red pull request frees the update, and the note says so.
    let w = queue_world();
    w.tracker.required_unreadable.set(true);
    w.tracker
        .red_heads
        .borrow_mut()
        .push("claude/issue-8".into());
    let reported = report(w.services(), "claude", None, Outcome::Passed, "ok").unwrap();
    assert!(reported.updated_branch);
    assert!(
        w.tracker
            .last_comment(7)
            .contains("could not read the required checks")
    );
}

#[test]
fn review_does_not_merge_while_a_higher_priority_pr_waits_for_its_required_checks() {
    let w = World::new();
    w.tracker.issue(7, &["agent:claude", "stage:review", "p2"]);
    w.tracker.pr(5, "claude/issue-7", "Closes #7");
    w.tracker.issue(8, &["agent:codex", "stage:review", "p0"]);
    w.tracker.pr(6, "codex/issue-8", "Closes #8");
    // #8 was just updated: up to date, `gate` runs. #7 would merge now.
    w.tracker.run("codex/issue-8", "gate", true);
    *w.tracker.required.borrow_mut() = vec!["gate".into()];
    claim_issue(&w, "claude");
    let reported = report(w.services(), "claude", None, Outcome::Passed, "ok").unwrap();
    assert_eq!(reported.waiting_for, Some(6));
    assert_eq!(w.tracker.calls(), ["edit_comment #7"]);
    assert!(w.tracker.last_comment(7).contains("waiting to update"));
}

#[test]
fn review_merges_when_the_higher_priority_pr_is_green_or_of_equal_priority() {
    // Equal priority, required check running: no hold, merge as today.
    let w = World::new();
    w.tracker.issue(7, &["agent:claude", "stage:review"]);
    w.tracker.pr(5, "claude/issue-7", "Closes #7");
    w.tracker.issue(8, &["agent:claude", "stage:review"]);
    w.tracker.pr(6, "claude/issue-8", "Closes #8");
    w.tracker.run("claude/issue-8", "gate", true);
    *w.tracker.required.borrow_mut() = vec!["gate".into()];
    claim_issue(&w, "claude");
    let reported = report(w.services(), "claude", None, Outcome::Passed, "ok").unwrap();
    assert_eq!(reported.waiting_for, None);
    assert!(w.tracker.calls()[0].starts_with("merge #5"));
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
fn review_closed_closes_the_pull_request_and_deletes_its_branch() {
    let w = World::new();
    w.tracker.issue(7, &["agent:claude", "stage:review"]);
    w.tracker.pr(5, "claude/issue-7", "Closes #7");
    claim_issue(&w, "claude");
    let r = report(
        w.services(),
        "claude",
        None,
        Outcome::Closed,
        "wrong design; start again",
    )
    .unwrap();
    assert_eq!(r.outcome, Outcome::Closed);
    assert_eq!(r.link.as_deref(), Some("https://github.com/o/r/pull/5"));
    assert!(r.warnings.is_empty());
    assert_eq!(
        w.tracker.calls(),
        [
            "pr_comment #5",
            "close_pr #5",
            "delete_branch claude/issue-7",
            "edit_comment #7",
            "add_labels #7 stage:tdd",
            "remove_label #7 stage:review"
        ]
    );
    assert!(w.tracker.last_comment(7).contains("wrong design"));
}

#[test]
fn review_closed_keeps_going_when_the_branch_stays() {
    let w = World::new();
    w.tracker.issue(7, &["agent:claude", "stage:review"]);
    w.tracker.pr(5, "claude/issue-7", "Closes #7");
    claim_issue(&w, "claude");
    w.tracker.fail_delete_branch.set(true);
    let r = report(w.services(), "claude", None, Outcome::Closed, "superseded").unwrap();
    assert_eq!(r.warnings.len(), 1);
    assert!(r.warnings[0].contains("claude/issue-7"));
    assert!(w.tracker.calls().contains(&"close_pr #5".to_string()));
    assert!(
        w.tracker
            .calls()
            .contains(&"add_labels #7 stage:tdd".to_string())
    );
}

#[test]
fn review_closed_needs_a_message_and_a_review_stage() {
    let w = World::new();
    w.tracker.issue(7, &["agent:claude", "stage:review"]);
    w.tracker.pr(5, "claude/issue-7", "Closes #7");
    claim_issue(&w, "claude");
    assert!(report(w.services(), "claude", None, Outcome::Closed, " ").is_err());
    assert!(w.tracker.calls().is_empty());

    let w = World::new();
    w.tracker.issue(1, &["agent:codex", "stage:spec"]);
    claim_issue(&w, "codex");
    assert!(report(w.services(), "codex", None, Outcome::Closed, "x").is_err());
    assert!(w.tracker.calls().is_empty());
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
    match claim(w.services(), "claude", None, &[], &[]).unwrap() {
        ClaimOutcome::Claimed(c) => assert_eq!((c.issue, c.stage), (1, Stage::Tdd)),
        ClaimOutcome::Nothing(why) => panic!("nothing to claim: {why}"),
    }
}

#[test]
fn tdd_respec_moves_the_issue_back_to_spec_without_blocked() {
    let w = World::new();
    w.tracker.issue(1, &["agent:claude", "stage:tdd"]);
    claim_issue(&w, "claude");
    assert!(report(w.services(), "claude", None, Outcome::Respec, " ").is_err());
    report(
        w.services(),
        "claude",
        None,
        Outcome::Respec,
        "spec lacks tests/a.rs",
    )
    .unwrap();
    let labels = w.tracker.issues.borrow()[0].labels.clone();
    assert_eq!(labels, ["agent:claude", "stage:spec"]);
}

#[test]
fn respec_applies_to_tdd_only() {
    let w = World::new();
    w.tracker.issue(1, &["agent:claude", "stage:spec"]);
    claim_issue(&w, "claude");
    assert!(report(w.services(), "claude", None, Outcome::Respec, "x").is_err());
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
fn tdd_blocked_for_a_spec_gap_is_reported_as_respec() {
    // The two messages of faberline/relay#134 and faberline/lumen#307.
    for message in [
        "spec unclear: the issue body does not meet the spec checklist.\n\nMissing points:\n1. Steps",
        "tdd blocked: the spec is missing points that the tdd checklist needs.\n\n1. the SHA",
    ] {
        let w = World::new();
        w.tracker.issue(1, &["agent:claude", "stage:tdd"]);
        claim_issue(&w, "claude");
        let r = report(w.services(), "claude", None, Outcome::Blocked, message).unwrap();
        assert_eq!(r.outcome, Outcome::Respec);
        assert!(
            r.warnings.iter().any(|w| w.contains("reported `respec`")),
            "{:?}",
            r.warnings
        );
        let labels = w.tracker.issues.borrow()[0].labels.clone();
        assert_eq!(labels, ["agent:claude", "stage:spec"]);
        let signals = w.tracker.signals(1);
        assert_eq!(signals.len(), 1);
        assert_eq!(signals[0].outcome, Outcome::Respec);
        // The signal keeps the list of the missing points.
        assert!(w.tracker.last_comment(1).contains(message));
    }
}

#[test]
fn tdd_blocked_for_a_person_stays_blocked() {
    let w = World::new();
    w.tracker.issue(1, &["agent:claude", "stage:tdd"]);
    claim_issue(&w, "claude");
    let r = report(
        w.services(),
        "claude",
        None,
        Outcome::Blocked,
        "the push was refused for branch claude/issue-1",
    )
    .unwrap();
    assert_eq!(r.outcome, Outcome::Blocked);
    let labels = w.tracker.issues.borrow()[0].labels.clone();
    assert_eq!(labels, ["agent:claude", "stage:tdd", "blocked"]);
}

#[test]
fn a_spec_gap_message_changes_only_a_tdd_blocked_report() {
    // review keeps its `spec unclear:` blocked signal for the planner.
    let w = World::new();
    w.tracker.issue(7, &["agent:claude", "stage:review"]);
    w.tracker.pr(5, "claude/issue-7", "Closes #7");
    claim_issue(&w, "claude");
    let r = report(
        w.services(),
        "claude",
        None,
        Outcome::Blocked,
        "spec unclear: the spec names no test",
    )
    .unwrap();
    assert_eq!(r.outcome, Outcome::Blocked);
    let labels = w.tracker.issues.borrow()[0].labels.clone();
    assert_eq!(labels, ["agent:claude", "stage:review", "blocked"]);

    let w = World::new();
    w.tracker.issue(1, &["agent:claude", "stage:spec"]);
    claim_issue(&w, "claude");
    let r = report(
        w.services(),
        "claude",
        None,
        Outcome::Blocked,
        "spec incomplete: the owner must choose",
    )
    .unwrap();
    assert_eq!(r.outcome, Outcome::Blocked);
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
        match claim(w.services(), "claude", Some(stage), &[], &[]).unwrap() {
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

#[test]
fn review_closed_never_deletes_the_default_branch() {
    let w = World::new();
    w.tracker.issue(7, &["agent:claude", "stage:review"]);
    w.tracker.pr(5, "main", "Closes #7");
    claim_issue(&w, "claude");
    let r = report(w.services(), "claude", None, Outcome::Closed, "superseded").unwrap();
    assert_eq!(r.warnings.len(), 1);
    assert!(r.warnings[0].contains("default branch"));
    assert!(
        !w.tracker
            .calls()
            .iter()
            .any(|c| c.starts_with("delete_branch"))
    );
    assert!(w.tracker.calls().contains(&"close_pr #5".to_string()));
}

#[test]
fn review_closed_is_an_error_when_the_pull_request_is_gone() {
    let w = World::new();
    w.tracker.issue(7, &["agent:claude", "stage:review"]);
    w.tracker.pr(5, "claude/issue-7", "Closes #7");
    claim_issue(&w, "claude");
    w.tracker.prs.borrow_mut().clear();
    let err = report(w.services(), "claude", None, Outcome::Closed, "superseded").unwrap_err();
    assert!(format!("{err:#}").contains("no longer open"));
    assert!(w.tracker.calls().is_empty());
    assert_eq!(w.tracker.claims(7).len(), 1);
}

/// A claim of `claude` on issue 2 that is 7 hours old: older than the
/// recent comments (6 hours). Issue 1 is open and has no claim.
fn long_run(w: &World, stage: Stage, branch: &str) {
    use crate::support::NOW;
    use aw::domain::work::claim::ClaimMark;
    w.tracker.issue(1, &["agent:claude", "stage:spec"]);
    w.tracker.issue(2, &["agent:claude", "stage:spec"]);
    let mark = ClaimMark {
        agent: "claude".into(),
        stage,
        branch: branch.into(),
        pr: None,
        claimed_at: NOW - 7 * 60 * 60,
    };
    w.tracker
        .add_comment(2, &mark.render(), "2027-01-15T01:00:00Z");
}

/// The state of a tdd run that took 7 hours: the checkout is on the work
/// branch of the claim.
fn long_tdd_run(w: &World) {
    long_run(w, Stage::Tdd, "claude/issue-2");
    *w.workspace.head.borrow_mut() = Some("claude/issue-2".into());
}

#[test]
fn report_and_release_find_a_tdd_claim_older_than_the_recent_comments_on_its_branch() {
    let w = World::new();
    long_tdd_run(&w);
    // `aw status` reads the recent comments only.
    assert_eq!(status(w.services(), "claude", None).unwrap(), None);
    assert_eq!(
        *w.tracker.reads.borrow(),
        ["comments_since 2027-01-15T02:00:00Z"]
    );
    // `aw report` then reads each open issue, and finds the claim: the
    // checkout is on the branch of the claim.
    w.tracker.reads.borrow_mut().clear();
    let done = report(w.services(), "claude", None, Outcome::Blocked, "a question").unwrap();
    assert_eq!((done.issue, done.stage), (2, Stage::Tdd));
    assert_eq!(
        *w.tracker.reads.borrow(),
        [
            "comments_since 2027-01-15T02:00:00Z",
            "comments #1",
            "comments #2"
        ]
    );
    // The stage filter applies to the old claim too.
    let w = World::new();
    long_tdd_run(&w);
    assert!(release(w.services(), "claude", Some(Stage::Spec)).is_err());
    assert!(release(w.services(), "codex", None).is_err());
    let (released, _) = release(w.services(), "claude", Some(Stage::Tdd)).unwrap();
    assert_eq!(released.issue, 2);
    assert!(w.tracker.claims(2).is_empty());
}

#[test]
fn report_and_release_find_a_review_claim_older_than_the_recent_comments_on_its_branch() {
    let w = World::new();
    long_run(&w, Stage::Review, "claude/issue-2");
    *w.workspace.head.borrow_mut() = Some("claude/issue-2".into());
    let (released, _) = release(w.services(), "claude", None).unwrap();
    assert_eq!((released.issue, released.stage), (2, Stage::Review));
    assert!(w.tracker.claims(2).is_empty());
}

#[test]
fn a_dead_claim_on_another_issue_is_not_the_claim_of_this_run() {
    // The live claim was missing from the recent comments, so a second
    // run took issue 1. The checkout is on the branch of issue 1; the old
    // claim of issue 2 names another branch.
    for stage in [Stage::Tdd, Stage::Review] {
        let w = World::new();
        long_run(&w, stage, "claude/issue-2");
        *w.workspace.head.borrow_mut() = Some("claude/issue-1".into());
        let err = report(w.services(), "claude", None, Outcome::Blocked, "a question").unwrap_err();
        assert_eq!(
            format!("{err:#}"),
            "`claude` holds no claim; run `aw claim` first"
        );
        let err = release(w.services(), "claude", None).unwrap_err();
        assert_eq!(format!("{err:#}"), "`claude` holds no claim");
        // Nothing was written.
        assert_eq!(w.tracker.claims(2).len(), 1);
        assert!(w.tracker.calls().is_empty());
    }
}

#[test]
fn an_old_claim_is_refused_without_a_checkout_on_its_branch() {
    // Detached HEAD.
    let w = World::new();
    long_tdd_run(&w);
    *w.workspace.head.borrow_mut() = None;
    assert!(release(w.services(), "claude", None).is_err());
    // The directory is a checkout of another repository, on a branch of
    // the same name.
    let w = World::new();
    long_tdd_run(&w);
    *w.workspace.origin.borrow_mut() = Some("other/repo".into());
    assert!(release(w.services(), "claude", None).is_err());
    // The directory is not a git checkout.
    let w = World::new();
    long_tdd_run(&w);
    *w.workspace.origin.borrow_mut() = None;
    assert!(release(w.services(), "claude", None).is_err());
    assert_eq!(w.tracker.claims(2).len(), 1);
}

#[test]
fn a_spec_claim_older_than_the_recent_comments_is_not_found() {
    // A spec claim names the default branch and a spec run never changes
    // the checkout, so nothing tells this run from a dead run on another
    // issue. Whatever branch the checkout is on, the fallback refuses it.
    for head in [Some("main"), Some("claude/issue-2"), None] {
        let w = World::new();
        long_run(&w, Stage::Spec, "main");
        *w.workspace.head.borrow_mut() = head.map(String::from);
        let err = report(w.services(), "claude", None, Outcome::Ready, "the spec").unwrap_err();
        assert_eq!(
            format!("{err:#}"),
            "`claude` holds no claim; run `aw claim` first"
        );
        assert!(release(w.services(), "claude", Some(Stage::Spec)).is_err());
        assert_eq!(w.tracker.claims(2).len(), 1);
        assert!(w.tracker.calls().is_empty());
    }
}

#[test]
fn a_dead_spec_claim_does_not_hide_the_tdd_claim_of_this_run() {
    // Without a stage, each old claim is judged for its own stage.
    let w = World::new();
    long_tdd_run(&w);
    w.tracker.issue(3, &["agent:claude", "stage:spec"]);
    use crate::support::NOW;
    use aw::domain::work::claim::ClaimMark;
    let spec = ClaimMark {
        agent: "claude".into(),
        stage: Stage::Spec,
        branch: "main".into(),
        pr: None,
        // Newer than the tdd claim, but a spec claim is refused.
        claimed_at: NOW - 6 * 60 * 60 - 60,
    };
    w.tracker
        .add_comment(3, &spec.render(), "2027-01-15T01:30:00Z");
    let (released, _) = release(w.services(), "claude", None).unwrap();
    assert_eq!((released.issue, released.stage), (2, Stage::Tdd));
    assert_eq!(w.tracker.claims(3).len(), 1);
}

const SPLIT: &str = "Too large: a port and an adapter.\n\n\
    ## Child: Add the port\nThe trait and its tests.\n\n\
    ## Child: Add the adapter\nThe `gh` adapter.\nWaits for the port.\n";

fn split_world() -> World {
    let w = World::new();
    w.tracker.issue(1, &["agent:claude", "stage:spec", "p1"]);
    w.tracker.milestones.borrow_mut().insert(1, 4);
    claim_issue(&w, "claude");
    w
}

#[test]
fn spec_split_makes_the_children_and_closes_the_parent() {
    let w = split_world();
    let r = report(w.services(), "claude", None, Outcome::Split, SPLIT).unwrap();
    assert_eq!(r.outcome, Outcome::Split);
    assert_eq!(r.children, [201, 202]);
    assert_eq!(r.link, None);
    assert!(r.warnings.is_empty(), "{:?}", r.warnings);
    assert_eq!(
        w.tracker.calls(),
        [
            "create_issue #201 Add the port",
            "create_issue #202 Add the adapter",
            "edit_comment #1",
            "close_issue #1",
        ]
    );
    // Each child: the milestone and the priority of the parent, the agent
    // label and the label that makes it ready for spec.
    for (_, new) in w.tracker.created.borrow().iter() {
        assert_eq!(new.labels, ["agent:claude", "stage:spec", "p1"]);
        assert_eq!(new.milestone, Some(4));
        assert!(
            new.body
                .starts_with("<!-- aw:child parent=1 -->\nPart of #1.")
        );
    }
    let created = w.tracker.created.borrow();
    assert!(created[1].1.body.ends_with("Waits for the port."));
    // One comment on the parent links the children.
    let comment = w.tracker.last_comment(1);
    assert_eq!(w.tracker.signals(1)[0].outcome, Outcome::Split);
    assert!(comment.contains("Too large: a port and an adapter."));
    assert!(comment.contains("Replaced by:\n- #201 Add the port\n- #202 Add the adapter"));
    assert!(w.tracker.claims(1).is_empty());
    assert_eq!(*w.tracker.closed.borrow(), [1]);
    assert_eq!(status(w.services(), "claude", None).unwrap(), None);
}

#[test]
fn a_child_of_a_split_is_ready_for_spec() {
    let w = split_world();
    report(w.services(), "claude", None, Outcome::Split, SPLIT).unwrap();
    match claim(w.services(), "claude", None, &[], &[]).unwrap() {
        ClaimOutcome::Claimed(c) => {
            assert_eq!(c.issue, 201);
            assert_eq!(c.stage, Stage::Spec);
        }
        ClaimOutcome::Nothing(why) => panic!("nothing to claim: {why}"),
    }
}

#[test]
fn a_split_without_priority_or_milestone_copies_none() {
    let w = World::new();
    w.tracker.issue(1, &["agent:claude", "stage:spec"]);
    claim_issue(&w, "claude");
    report(w.services(), "claude", None, Outcome::Split, SPLIT).unwrap();
    for (_, new) in w.tracker.created.borrow().iter() {
        assert_eq!(new.labels, ["agent:claude", "stage:spec"]);
        assert_eq!(new.milestone, None);
    }
}

#[test]
fn a_split_makes_at_most_five_children_and_at_least_two() {
    let w = split_world();
    let part = |n: usize| format!("## Child: Part {n}\nText {n}.\n");
    let six: String = (1..=6).map(part).collect();
    let err = report(w.services(), "claude", None, Outcome::Split, &six).unwrap_err();
    assert!(err.to_string().contains("at most 5"), "{err}");
    let one = report(w.services(), "claude", None, Outcome::Split, &part(1)).unwrap_err();
    assert!(one.to_string().contains("at least 2"), "{one}");
    assert!(report(w.services(), "claude", None, Outcome::Split, " ").is_err());
    // Nothing changed: the claim stays and the run can report again.
    assert!(w.tracker.calls().is_empty(), "{:?}", w.tracker.calls());
    assert_eq!(w.tracker.claims(1).len(), 1);
    let five: String = (1..=5).map(part).collect();
    let r = report(w.services(), "claude", None, Outcome::Split, &five).unwrap();
    assert_eq!(r.children.len(), 5);
}

#[test]
fn a_child_of_a_split_may_split_once_more() {
    let w = split_world();
    report(w.services(), "claude", None, Outcome::Split, SPLIT).unwrap();
    // #201 is a child (depth 1). It keeps the milestone and the priority.
    claim_issue(&w, "claude");
    let r = report(w.services(), "claude", None, Outcome::Split, SPLIT).unwrap();
    assert_eq!(r.outcome, Outcome::Split);
    assert_eq!(r.children, [203, 204]);
    assert!(r.warnings.is_empty(), "{:?}", r.warnings);
    let created = w.tracker.created.borrow();
    for (_, new) in created[2..].iter() {
        assert_eq!(new.labels, ["agent:claude", "stage:spec", "p1"]);
        assert_eq!(new.milestone, Some(4));
        assert!(
            new.body
                .starts_with("<!-- aw:child parent=201 -->\nPart of #201.")
        );
    }
    drop(created);
    // The child closes as replaced, like a first-level parent.
    assert_eq!(*w.tracker.closed.borrow(), [1, 201]);
    assert_eq!(w.tracker.signals(201)[0].outcome, Outcome::Split);
    assert!(
        w.tracker
            .last_comment(201)
            .contains("Replaced by:\n- #203 Add the port\n- #204 Add the adapter")
    );
    assert!(w.tracker.claims(201).is_empty());
}

#[test]
fn a_grandchild_is_not_split_again() {
    let w = split_world();
    report(w.services(), "claude", None, Outcome::Split, SPLIT).unwrap();
    claim_issue(&w, "claude");
    report(w.services(), "claude", None, Outcome::Split, SPLIT).unwrap();
    // #203 is a grandchild (depth 2): #1 to #201 to #203. Take #202 out of
    // the queue, so the claim goes to #203.
    w.tracker.issues.borrow_mut().retain(|i| i.number != 202);
    claim_issue(&w, "claude");
    let made = w.tracker.calls().len();
    let err = report(w.services(), "claude", None, Outcome::Split, SPLIT).unwrap_err();
    let text = err.to_string();
    assert!(
        text.contains("#203 is a child of #201, which is a child of #1"),
        "{text}"
    );
    assert!(text.contains("Report `blocked`"), "{text}");
    assert_eq!(w.tracker.calls().len(), made, "{:?}", w.tracker.calls());
    assert_eq!(w.tracker.claims(203).len(), 1);
    // `blocked` is still open to the run.
    let r = report(
        w.services(),
        "claude",
        None,
        Outcome::Blocked,
        "Decision: one crate or two.",
    )
    .unwrap();
    assert_eq!(r.outcome, Outcome::Blocked);
}

#[test]
fn a_split_that_makes_no_child_keeps_the_claim() {
    let w = split_world();
    w.tracker.fail_create_from.set(1);
    let err = report(w.services(), "claude", None, Outcome::Split, SPLIT).unwrap_err();
    assert!(
        err.to_string()
            .contains("could not make the first child of #1"),
        "{err}"
    );
    assert!(w.tracker.calls().is_empty(), "{:?}", w.tracker.calls());
    assert_eq!(w.tracker.claims(1).len(), 1);
    assert!(w.tracker.signals(1).is_empty());
}

#[test]
fn a_split_that_stops_part_way_is_reported_as_blocked() {
    let w = split_world();
    w.tracker.fail_create_from.set(2);
    let r = report(w.services(), "claude", None, Outcome::Split, SPLIT).unwrap();
    assert_eq!(r.outcome, Outcome::Blocked);
    assert_eq!(r.children, [201]);
    assert!(
        r.warnings[0].contains("made 1 of 2 children"),
        "{:?}",
        r.warnings
    );
    let comment = w.tracker.last_comment(1);
    assert_eq!(w.tracker.signals(1)[0].outcome, Outcome::Blocked);
    assert!(comment.contains("Made:\n- #201 Add the port"), "{comment}");
    assert!(
        comment.contains("Not made:\n- Add the adapter"),
        "{comment}"
    );
    assert!(w.tracker.closed.borrow().is_empty());
    let labels = w.tracker.issues.borrow()[0].labels.clone();
    assert_eq!(labels, ["agent:claude", "stage:spec", "p1", "blocked"]);
}

#[test]
fn a_parent_that_cannot_be_closed_gets_blocked() {
    let w = split_world();
    w.tracker.fail_close_issue.set(true);
    let r = report(w.services(), "claude", None, Outcome::Split, SPLIT).unwrap();
    assert_eq!(r.outcome, Outcome::Split);
    assert_eq!(r.children, [201, 202]);
    assert!(
        r.warnings[0].contains("could not close #1"),
        "{:?}",
        r.warnings
    );
    let labels = w.tracker.issues.borrow()[0].labels.clone();
    assert_eq!(labels, ["agent:claude", "stage:spec", "p1", "blocked"]);
}

#[test]
fn split_applies_to_spec_only() {
    let w = World::new();
    w.tracker.issue(1, &["agent:claude", "stage:tdd"]);
    claim_issue(&w, "claude");
    let err = report(w.services(), "claude", None, Outcome::Split, SPLIT).unwrap_err();
    assert!(err.to_string().contains("does not apply to tdd"), "{err}");
    assert!(w.tracker.created.borrow().is_empty());
}
