use aw::application::work::{ClaimOutcome, claim};
use aw::domain::work::claim::{Claim, STALE_AFTER_SECONDS};
use aw::domain::work::signal::{Outcome, Signal};
use aw::domain::work::stage::Stage;

use crate::support::{NOW, World};

fn claimed(world: &World, agent: &str) -> Claim {
    match claim(world.services(), agent).unwrap() {
        ClaimOutcome::Claimed(c) => c,
        ClaimOutcome::Nothing(why) => panic!("expected a claim, got nothing: {why}"),
    }
}

fn nothing(world: &World, agent: &str) -> String {
    match claim(world.services(), agent).unwrap() {
        ClaimOutcome::Nothing(why) => why,
        ClaimOutcome::Claimed(c) => panic!("expected nothing, claimed #{}", c.issue),
    }
}

fn signal(stage: Stage, agent: &str, outcome: Outcome) -> String {
    Signal {
        stage,
        agent: agent.into(),
        outcome,
    }
    .render("", None)
}

#[test]
fn takes_highest_priority_then_oldest() {
    let w = World::new();
    w.tracker.issue(1, &["agent:codex", "stage:spec", "p2"]);
    w.tracker.issue(2, &["agent:codex", "stage:spec", "p1"]);
    w.tracker.issue(3, &["agent:codex", "stage:spec", "p1"]);
    w.tracker.issue(4, &["agent:claude", "stage:spec", "p0"]);
    let c = claimed(&w, "codex");
    assert_eq!(c.issue, 2);
    assert_eq!(c.stage, Stage::Spec);
    assert_eq!(c.branch, "main");
    assert_eq!(w.store.claim.borrow().as_ref(), Some(&c));
}

#[test]
fn skips_blocked_done_and_stageless_issues() {
    let w = World::new();
    w.tracker.issue(1, &["agent:codex", "stage:tdd", "blocked"]);
    w.tracker.issue(2, &["agent:codex", "stage:done"]);
    w.tracker.issue(3, &["agent:codex"]);
    nothing(&w, "codex");
}

#[test]
fn skips_an_issue_this_agent_already_reported_for_this_stage() {
    let w = World::new();
    w.tracker.issue(1, &["agent:codex", "stage:spec"]);
    w.tracker.add_comment(
        1,
        &signal(Stage::Spec, "codex", Outcome::Ready),
        "2026-02-01T00:00:00Z",
    );
    nothing(&w, "codex");
}

#[test]
fn a_new_stage_or_agent_makes_the_issue_eligible_again() {
    let w = World::new();
    w.tracker.issue(1, &["agent:codex", "stage:tdd"]);
    w.tracker.add_comment(
        1,
        &signal(Stage::Spec, "codex", Outcome::Ready),
        "2026-02-01T00:00:00Z",
    );
    assert_eq!(claimed(&w, "codex").issue, 1);

    let w = World::new();
    w.tracker.issue(1, &["agent:claude", "stage:spec"]);
    w.tracker.add_comment(
        1,
        &signal(Stage::Spec, "codex", Outcome::Blocked),
        "2026-02-01T00:00:00Z",
    );
    assert_eq!(claimed(&w, "claude").issue, 1);
}

#[test]
fn spec_switches_to_the_default_branch_and_fast_forwards() {
    let w = World::new();
    w.tracker.issue(1, &["agent:codex", "stage:spec"]);
    claimed(&w, "codex");
    assert_eq!(
        w.workspace.calls(),
        ["fetch", "switch main", "ff origin/main"]
    );
}

#[test]
fn tdd_creates_the_work_branch_from_the_default_branch() {
    let w = World::new();
    w.tracker.issue(7, &["agent:codex", "stage:tdd"]);
    let c = claimed(&w, "codex");
    assert_eq!(c.branch, "codex/issue-7");
    assert_eq!(
        w.workspace.calls(),
        ["fetch", "create codex/issue-7 from origin/main"]
    );
}

#[test]
fn tdd_continues_a_pushed_work_branch() {
    let w = World::new();
    w.workspace.remote.borrow_mut().push("codex/issue-7".into());
    w.tracker.issue(7, &["agent:codex", "stage:tdd"]);
    claimed(&w, "codex");
    assert_eq!(
        w.workspace.calls(),
        ["fetch", "create codex/issue-7 from origin/codex/issue-7"]
    );
}

#[test]
fn tdd_continues_the_open_pull_request_branch() {
    let w = World::new();
    w.workspace.remote.borrow_mut().push("issue/7".into());
    w.tracker.pr(51, "issue/7", "Closes #7\n\nfirst try");
    w.tracker.issue(7, &["agent:codex", "stage:tdd"]);
    let c = claimed(&w, "codex");
    assert_eq!(c.branch, "issue/7");
    assert_eq!(c.pr, Some(51));
    assert_eq!(
        w.workspace.calls(),
        ["fetch", "create issue/7 from origin/issue/7"]
    );
}

#[test]
fn tdd_reuses_a_local_work_branch() {
    let w = World::new();
    w.workspace.local.borrow_mut().push("codex/issue-7".into());
    w.workspace.remote.borrow_mut().push("codex/issue-7".into());
    w.tracker.issue(7, &["agent:codex", "stage:tdd"]);
    claimed(&w, "codex");
    assert_eq!(
        w.workspace.calls(),
        ["fetch", "switch codex/issue-7", "ff origin/codex/issue-7"]
    );
}

#[test]
fn review_checks_out_the_pull_request_head() {
    let w = World::new();
    w.tracker.issue(7, &["agent:claude", "stage:review"]);
    w.tracker.pr(5, "codex/issue-7", "Closes #7");
    let c = claimed(&w, "claude");
    assert_eq!(c.pr, Some(5));
    assert_eq!(c.branch, "codex/issue-7");
    assert_eq!(
        w.workspace.calls(),
        ["fetch", "detach origin/codex/issue-7"]
    );
}

#[test]
fn review_blocks_when_there_is_no_pull_request_and_moves_on() {
    let w = World::new();
    w.tracker.issue(1, &["agent:claude", "stage:review", "p0"]);
    w.tracker.issue(2, &["agent:claude", "stage:spec"]);
    assert_eq!(claimed(&w, "claude").issue, 2);
    let signals = w.tracker.signals(1);
    assert_eq!(signals.len(), 1);
    assert_eq!(signals[0].outcome, Outcome::Blocked);
    assert_eq!(signals[0].stage, Stage::Review);

    // The blocked signal keeps the issue out of the next claim.
    w.store.claim.borrow_mut().take();
    w.tracker.issues.borrow_mut().retain(|i| i.number == 1);
    nothing(&w, "claude");
}

#[test]
fn review_refuses_a_self_authored_pull_request() {
    let w = World::new();
    w.tracker.issue(7, &["agent:codex", "stage:review"]);
    w.tracker.pr(5, "codex/issue-7", "Closes #7");
    nothing(&w, "codex");
    assert!(
        w.tracker
            .last_comment(7)
            .contains("another agent must review it")
    );
}

#[test]
fn an_active_claim_means_nothing_to_do() {
    let w = World::new();
    w.tracker.issue(1, &["agent:codex", "stage:spec"]);
    let first = claimed(&w, "codex");
    let why = nothing(&w, "codex");
    assert!(why.contains("#1"), "{why}");
    assert_eq!(w.store.claim.borrow().as_ref(), Some(&first));
}

#[test]
fn a_stale_claim_is_replaced() {
    let w = World::new();
    w.tracker.issue(1, &["agent:codex", "stage:spec"]);
    claimed(&w, "codex");
    w.clock.0.set(NOW + STALE_AFTER_SECONDS);
    let again = claimed(&w, "codex");
    assert_eq!(again.claimed_at, NOW + STALE_AFTER_SECONDS);
}

#[test]
fn a_dirty_tree_is_an_error() {
    let w = World::new();
    w.workspace.clean.set(false);
    w.tracker.issue(1, &["agent:codex", "stage:spec"]);
    assert!(claim(w.services(), "codex").is_err());
    assert!(w.store.claim.borrow().is_none());
}

#[test]
fn a_bad_agent_id_is_an_error() {
    let w = World::new();
    assert!(claim(w.services(), "Codex").is_err());
    assert!(claim(w.services(), "").is_err());
}
