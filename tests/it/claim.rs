use aw::application::work::{ClaimOutcome, claim, status};
use aw::domain::work::claim::{Claim, ClaimMark, STALE_AFTER_SECONDS};
use aw::domain::work::signal::{Outcome, Signal};
use aw::domain::work::stage::Stage;

use crate::support::{NOW, World};

fn claimed(world: &World, agent: &str) -> Claim {
    match claim(world.services(), agent, None, &[], &[]).unwrap() {
        ClaimOutcome::Claimed(c) => c,
        ClaimOutcome::Nothing(why) => panic!("expected a claim, got nothing: {why}"),
    }
}

fn nothing(world: &World, agent: &str) -> String {
    match claim(world.services(), agent, None, &[], &[]).unwrap() {
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
    assert_eq!(status(w.services(), "codex", None).unwrap(), Some(c));
}

#[test]
fn skip_passes_over_the_named_issues() {
    let w = World::new();
    w.tracker.issue(1, &["agent:claude", "stage:spec", "p0"]);
    w.tracker.issue(2, &["agent:claude", "stage:spec", "p1"]);
    w.tracker.issue(3, &["agent:claude", "stage:spec", "p2"]);
    let c = match claim(w.services(), "claude", None, &[1, 2], &[]).unwrap() {
        ClaimOutcome::Claimed(c) => c,
        ClaimOutcome::Nothing(why) => panic!("expected a claim, got nothing: {why}"),
    };
    assert_eq!(c.issue, 3);
    assert!(w.tracker.claims(1).is_empty());
    assert!(w.tracker.claims(2).is_empty());
}

#[test]
fn skipping_every_issue_leaves_nothing() {
    let w = World::new();
    w.tracker.issue(1, &["agent:claude", "stage:spec"]);
    assert!(matches!(
        claim(w.services(), "claude", None, &[1], &[]).unwrap(),
        ClaimOutcome::Nothing(_)
    ));
    assert!(w.tracker.calls().is_empty());
}

#[test]
fn the_claim_is_a_comment_on_the_issue() {
    let w = World::new();
    w.tracker.issue(7, &["agent:claude", "stage:review"]);
    w.tracker.pr(5, "agy/issue-7", "Closes #7");
    claimed(&w, "claude");
    let marks = w.tracker.claims(7);
    assert_eq!(marks.len(), 1);
    assert_eq!(marks[0].agent, "claude");
    assert_eq!(marks[0].stage, Stage::Review);
    assert_eq!(marks[0].branch, "agy/issue-7");
    assert_eq!(marks[0].pr, Some(5));
    assert_eq!(marks[0].claimed_at, NOW);
    assert_eq!(w.tracker.calls(), ["comment #7"]);
    // A claim is not a signal: the issue still waits on this agent.
    assert!(w.tracker.signals(7).is_empty());
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
fn spec_and_review_do_not_touch_the_working_tree() {
    let w = World::new();
    w.workspace.origin.replace(None);
    w.workspace.clean.set(false);
    w.tracker.issue(1, &["agent:claude", "stage:spec", "p0"]);
    w.tracker.issue(7, &["agent:claude", "stage:review"]);
    w.tracker.pr(5, "agy/issue-7", "Closes #7");
    assert_eq!(claimed(&w, "claude").issue, 1);
    w.tracker.issues.borrow_mut().retain(|i| i.number == 7);
    w.tracker.comments.borrow_mut().clear();
    assert_eq!(claimed(&w, "claude").branch, "agy/issue-7");
    assert!(w.workspace.calls().is_empty(), "{:?}", w.workspace.calls());
}

#[test]
fn tdd_creates_the_work_branch_from_the_default_branch() {
    let w = World::new();
    w.tracker.issue(7, &["agent:codex", "stage:tdd"]);
    let c = claimed(&w, "codex");
    assert_eq!(c.branch, "codex/issue-7");
    assert_eq!(
        w.workspace.calls(),
        ["status", "fetch", "create codex/issue-7 from origin/main"]
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
        [
            "status",
            "fetch",
            "create codex/issue-7 from origin/codex/issue-7"
        ]
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
        ["status", "fetch", "create issue/7 from origin/issue/7"]
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
        [
            "status",
            "fetch",
            "switch codex/issue-7",
            "ff origin/codex/issue-7"
        ]
    );
}

#[test]
fn tdd_needs_a_checkout_of_the_repository() {
    let w = World::new();
    w.workspace.origin.replace(None);
    w.tracker.issue(7, &["agent:claude", "stage:tdd", "p0"]);
    w.tracker.issue(8, &["agent:claude", "stage:spec"]);
    assert_eq!(claimed(&w, "claude").issue, 8);
    assert!(w.tracker.claims(7).is_empty());

    let w = World::new();
    w.workspace.origin.replace(Some("o/other".into()));
    w.tracker.issue(7, &["agent:claude", "stage:tdd"]);
    let why = nothing(&w, "claude");
    assert!(why.contains("#7") && why.contains("o/r"), "{why}");
    assert!(w.tracker.calls().is_empty());
}

#[test]
fn tdd_with_a_dirty_tree_is_an_error_and_leaves_no_claim() {
    let w = World::new();
    w.workspace.clean.set(false);
    w.tracker.issue(1, &["agent:codex", "stage:tdd"]);
    assert!(claim(w.services(), "codex", None, &[], &[]).is_err());
    assert!(w.tracker.claims(1).is_empty());
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
    w.tracker.issues.borrow_mut().retain(|i| i.number == 1);
    nothing(&w, "claude");
}

#[test]
fn review_takes_a_pull_request_of_the_same_agent() {
    let w = World::new();
    w.tracker.issue(7, &["agent:claude", "stage:review"]);
    w.tracker.pr(5, "claude/issue-7", "Closes #7");
    let c = claimed(&w, "claude");
    assert_eq!((c.issue, c.stage, c.pr), (7, Stage::Review, Some(5)));
}

#[test]
fn an_active_claim_means_nothing_to_do() {
    let w = World::new();
    w.tracker.issue(1, &["agent:codex", "stage:spec"]);
    w.tracker.issue(2, &["agent:codex", "stage:spec"]);
    let first = claimed(&w, "codex");
    let why = nothing(&w, "codex");
    assert!(why.contains("#1"), "{why}");
    assert_eq!(status(w.services(), "codex", None).unwrap(), Some(first));
    assert!(w.tracker.claims(2).is_empty());
}

#[test]
fn another_agents_claim_does_not_count() {
    let w = World::new();
    w.tracker
        .issue(1, &["agent:codex", "agent:claude", "stage:spec"]);
    claimed(&w, "codex");
    assert_eq!(status(w.services(), "claude", None).unwrap(), None);
}

#[test]
fn a_stale_claim_is_removed_and_replaced() {
    let w = World::new();
    w.tracker.issue(1, &["agent:codex", "stage:spec"]);
    claimed(&w, "codex");
    w.clock.0.set(NOW + STALE_AFTER_SECONDS);
    w.tracker.clear_calls();
    let again = claimed(&w, "codex");
    assert_eq!(again.claimed_at, NOW + STALE_AFTER_SECONDS);
    assert_eq!(w.tracker.calls(), ["delete_comment #1", "comment #1"]);
    assert_eq!(w.tracker.claims(1).len(), 1);
}

#[test]
fn a_bad_agent_id_is_an_error() {
    let w = World::new();
    assert!(claim(w.services(), "Codex", None, &[], &[]).is_err());
    assert!(claim(w.services(), "", None, &[], &[]).is_err());
}

fn claimed_in(w: &World, agent: &str, stage: Stage) -> Claim {
    match claim(w.services(), agent, Some(stage), &[], &[]).unwrap() {
        ClaimOutcome::Claimed(c) => c,
        ClaimOutcome::Nothing(why) => panic!("expected a claim, got nothing: {why}"),
    }
}

#[test]
fn a_stage_takes_only_issues_of_that_stage() {
    let w = World::new();
    w.tracker.issue(1, &["agent:claude", "stage:spec", "p0"]);
    w.tracker.issue(7, &["agent:claude", "stage:review", "p2"]);
    w.tracker.pr(5, "claude/issue-7", "Closes #7");
    let c = claimed_in(&w, "claude", Stage::Review);
    assert_eq!((c.issue, c.stage), (7, Stage::Review));
    assert!(w.tracker.claims(1).is_empty());
}

#[test]
fn a_stage_with_no_issue_says_which_stage() {
    let w = World::new();
    w.tracker.issue(1, &["agent:claude", "stage:spec"]);
    let why = match claim(w.services(), "claude", Some(Stage::Review), &[], &[]).unwrap() {
        ClaimOutcome::Nothing(why) => why,
        ClaimOutcome::Claimed(c) => panic!("expected nothing, claimed #{}", c.issue),
    };
    assert!(why.contains("no open review issue"), "{why}");
    assert!(w.tracker.calls().is_empty());
}

#[test]
fn each_stage_holds_its_own_claim() {
    let w = World::new();
    w.tracker.issue(1, &["agent:claude", "stage:spec"]);
    w.tracker.issue(2, &["agent:claude", "stage:spec"]);
    w.tracker.issue(7, &["agent:claude", "stage:review"]);
    w.tracker.pr(5, "claude/issue-7", "Closes #7");
    let spec = claimed_in(&w, "claude", Stage::Spec);
    // A spec claim does not stop a review claim of the same agent.
    let review = claimed_in(&w, "claude", Stage::Review);
    assert_eq!((spec.issue, review.issue), (1, 7));
    // A second spec claim waits for the first one.
    let why = match claim(w.services(), "claude", Some(Stage::Spec), &[], &[]).unwrap() {
        ClaimOutcome::Nothing(why) => why,
        ClaimOutcome::Claimed(c) => panic!("expected nothing, claimed #{}", c.issue),
    };
    assert!(why.contains("#1"), "{why}");
    assert_eq!(
        status(w.services(), "claude", Some(Stage::Spec)).unwrap(),
        Some(spec)
    );
    assert_eq!(
        status(w.services(), "claude", Some(Stage::Review)).unwrap(),
        Some(review)
    );
    assert_eq!(
        status(w.services(), "claude", Some(Stage::Tdd)).unwrap(),
        None
    );
}

#[test]
fn a_needs_label_is_passed_over_without_the_thing() {
    let w = World::new();
    w.tracker
        .issue(1, &["agent:claude", "stage:spec", "p0", "needs:mac"]);
    w.tracker.issue(2, &["agent:claude", "stage:spec", "p2"]);
    let c = match claim(w.services(), "claude", None, &[], &[]).unwrap() {
        ClaimOutcome::Claimed(c) => c,
        ClaimOutcome::Nothing(why) => panic!("expected a claim, got nothing: {why}"),
    };
    assert_eq!(c.issue, 2);
    assert!(w.tracker.claims(1).is_empty());
}

#[test]
fn a_worker_that_has_the_thing_takes_the_needs_issue() {
    let w = World::new();
    w.tracker
        .issue(1, &["agent:claude", "stage:spec", "p0", "needs:mac"]);
    w.tracker.issue(2, &["agent:claude", "stage:spec", "p2"]);
    let c = match claim(w.services(), "claude", None, &[], &["mac".to_string()]).unwrap() {
        ClaimOutcome::Claimed(c) => c,
        ClaimOutcome::Nothing(why) => panic!("expected a claim, got nothing: {why}"),
    };
    assert_eq!(c.issue, 1);
}

#[test]
fn only_needs_issues_leave_nothing_and_say_why() {
    let w = World::new();
    w.tracker
        .issue(1, &["agent:claude", "stage:spec", "needs:mac"]);
    let why = nothing(&w, "claude");
    assert!(why.contains("#1 needs `mac`"), "{why}");
    assert!(w.tracker.calls().is_empty());
}

fn spec_ready(msg: &str) -> String {
    Signal {
        stage: Stage::Spec,
        agent: "claude".into(),
        outcome: Outcome::Ready,
    }
    .render(msg, None)
}

fn tdd_with_pr(w: &World, head: &str) {
    w.workspace.remote.borrow_mut().push(head.into());
    w.tracker.pr(51, head, "Closes #7");
    w.tracker.issue(7, &["agent:claude", "stage:tdd"]);
}

#[test]
fn tdd_merges_main_into_a_branch_that_is_behind() {
    let w = World::new();
    tdd_with_pr(&w, "issue/7");
    w.workspace.behind.set(true);
    let c = claimed(&w, "claude");
    assert_eq!((c.branch.as_str(), c.pr), ("issue/7", Some(51)));
    assert_eq!(
        w.workspace.calls(),
        [
            "status",
            "fetch",
            "create issue/7 from origin/issue/7",
            "merge origin/main"
        ]
    );
    assert!(!w.tracker.calls().iter().any(|c| c.starts_with("close_pr")));
}

#[test]
fn tdd_starts_a_new_branch_when_the_merge_conflicts() {
    let w = World::new();
    tdd_with_pr(&w, "issue/7");
    w.workspace.behind.set(true);
    w.workspace.conflicts.set(true);
    let c = claimed(&w, "claude");
    assert_eq!((c.branch.as_str(), c.pr), ("issue/7-r2", None));
    assert!(
        w.workspace
            .calls()
            .contains(&"create issue/7-r2 from origin/main".to_string())
    );
    assert_eq!(
        w.tracker.calls(),
        ["comment #7", "pr_comment #51", "close_pr #51"]
    );
}

#[test]
fn a_spec_redo_marker_starts_a_new_branch_without_a_merge() {
    let w = World::new();
    tdd_with_pr(&w, "issue/7");
    w.tracker.add_comment(
        7,
        &spec_ready("Start from main.\n\naw:redo"),
        "2026-02-01T00:00:00Z",
    );
    let c = claimed(&w, "claude");
    assert_eq!((c.branch.as_str(), c.pr), ("issue/7-r2", None));
    assert!(!w.workspace.calls().iter().any(|c| c.starts_with("merge")));
    assert!(w.tracker.calls().contains(&"close_pr #51".to_string()));
    // The redo is done: the new claim does not ask for another.
    assert_eq!(w.tracker.claims(7)[0].branch, "issue/7-r2");
}

#[test]
fn a_redo_on_a_redo_branch_gives_r3() {
    let w = World::new();
    tdd_with_pr(&w, "issue/7-r2");
    w.workspace.remote.borrow_mut().push("issue/7".into());
    w.tracker
        .add_comment(7, &spec_ready("aw:redo"), "2026-02-01T00:00:00Z");
    assert_eq!(claimed(&w, "claude").branch, "issue/7-r3");
}

#[test]
fn a_redo_skips_branches_that_already_exist() {
    let w = World::new();
    tdd_with_pr(&w, "issue/7");
    w.workspace.remote.borrow_mut().push("issue/7-r2".into());
    w.tracker
        .add_comment(7, &spec_ready("aw:redo"), "2026-02-01T00:00:00Z");
    assert_eq!(claimed(&w, "claude").branch, "issue/7-r3");
}

/// A claim comment of `agent` that is `age` seconds old.
fn old_mark(agent: &str, stage: Stage, age: i64) -> String {
    ClaimMark {
        agent: agent.into(),
        stage,
        branch: "main".into(),
        pr: None,
        claimed_at: NOW - age,
    }
    .render()
}

/// `NOW` less `RECENT_SECONDS` (two stale periods, 6 hours).
const RECENT_START: &str = "comments_since 2027-01-15T02:00:00Z";
/// A time before `RECENT_START`.
const LONG_AGO: &str = "2027-01-14T20:00:00Z";

#[test]
fn a_claim_reads_one_list_of_recent_comments_and_the_issue_it_takes() {
    let w = World::new();
    w.tracker
        .issue(1, &["agent:claude", "stage:spec", "p0", "blocked"]);
    w.tracker.issue(2, &["agent:claude", "stage:done"]);
    w.tracker.issue(3, &["agent:claude", "stage:tdd", "p0"]);
    w.tracker.issue(4, &["agent:claude", "stage:spec", "p1"]);
    w.tracker.issue(5, &["agent:claude", "stage:spec", "p2"]);
    w.tracker.issue(6, &["agent:claude"]);
    assert_eq!(claimed_in(&w, "claude", Stage::Spec).issue, 4);
    // Not one request for each of the six open issues.
    assert_eq!(*w.tracker.reads.borrow(), [RECENT_START, "comments #4"]);

    // The claim is in the recent comments: the next claim reads no issue.
    w.tracker.reads.borrow_mut().clear();
    let why = match claim(w.services(), "claude", Some(Stage::Spec), &[], &[]).unwrap() {
        ClaimOutcome::Nothing(why) => why,
        ClaimOutcome::Claimed(c) => panic!("expected nothing, claimed #{}", c.issue),
    };
    assert!(why.contains("issue #4 (spec) is still claimed"));
    assert_eq!(*w.tracker.reads.borrow(), [RECENT_START]);
}

#[test]
fn a_claim_passes_over_an_issue_that_has_its_report() {
    let w = World::new();
    w.tracker.issue(1, &["agent:claude", "stage:spec", "p0"]);
    w.tracker.issue(2, &["agent:claude", "stage:spec", "p1"]);
    w.tracker
        .add_comment(1, &signal(Stage::Spec, "claude", Outcome::Ready), LONG_AGO);
    assert_eq!(claimed(&w, "claude").issue, 2);
    assert_eq!(
        *w.tracker.reads.borrow(),
        [RECENT_START, "comments #1", "comments #2"]
    );
}

#[test]
fn a_claim_older_than_the_recent_comments_is_replaced_on_its_issue() {
    let w = World::new();
    w.tracker.issue(1, &["agent:claude", "stage:spec"]);
    w.tracker
        .add_comment(1, &old_mark("claude", Stage::Spec, 12 * 60 * 60), LONG_AGO);
    // Another agent's old claim stays.
    w.tracker
        .add_comment(1, &old_mark("codex", Stage::Spec, 12 * 60 * 60), LONG_AGO);
    let c = claimed(&w, "claude");
    assert_eq!((c.issue, c.claimed_at), (1, NOW));
    let ages: Vec<(String, i64)> = w
        .tracker
        .claims(1)
        .into_iter()
        .map(|m| (m.agent, m.claimed_at))
        .collect();
    assert_eq!(
        ages,
        [
            ("codex".to_string(), NOW - 12 * 60 * 60),
            ("claude".to_string(), NOW)
        ]
    );
}
