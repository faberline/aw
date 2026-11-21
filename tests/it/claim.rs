use aw::application::work::{ClaimOutcome, claim, claim_with, status};
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
    w.tracker.issue(1, &["agent:other", "stage:spec", "p2"]);
    w.tracker.issue(2, &["agent:other", "stage:spec", "p1"]);
    w.tracker.issue(3, &["agent:other", "stage:spec", "p1"]);
    w.tracker.issue(4, &["agent:claude", "stage:spec", "p0"]);
    let c = claimed(&w, "other");
    assert_eq!(c.issue, 2);
    assert_eq!(c.stage, Stage::Spec);
    assert_eq!(c.branch, "main");
    assert_eq!(status(w.services(), "other", None).unwrap(), Some(c));
}

#[test]
fn takes_bar_work_after_p0_and_before_the_other_work() {
    let w = World::new();
    w.tracker.issue(1, &["agent:claude", "stage:spec", "p1"]);
    w.tracker
        .issue(2, &["agent:claude", "stage:spec", "p3", "bar"]);
    w.tracker.issue(3, &["agent:other", "stage:spec", "p0"]);
    w.tracker.issue(4, &["agent:other", "stage:spec", "bar"]);
    assert_eq!(claimed(&w, "claude").issue, 2);
    assert_eq!(claimed(&w, "other").issue, 3);
}

#[test]
fn a_ci_fix_review_is_claimed_before_bar_reviews_and_after_p0() {
    // relay, 2026-11-12: the review of the repair of a red `lint` on main
    // (p1, no `bar`) waited behind `bar` reviews that could not pass.
    for (p0, expected) in [(false, 155), (true, 24)] {
        let w = World::new();
        w.tracker
            .issue(16, &["agent:claude", "stage:review", "p1", "bar"]);
        w.tracker
            .issue(51, &["agent:claude", "stage:review", "p1", "bar"]);
        w.tracker
            .issue(155, &["agent:claude", "stage:review", "p1", "ci-fix"]);
        if p0 {
            w.tracker
                .issue(24, &["agent:claude", "stage:review", "p0", "bar"]);
            w.tracker.pr(25, "claude/issue-24", "Closes #24");
        }
        w.tracker.pr(17, "claude/issue-16", "Closes #16");
        w.tracker.pr(52, "claude/issue-51", "Closes #51");
        w.tracker.pr(156, "claude/issue-155", "Closes #155");
        let c = claimed(&w, "claude");
        assert_eq!((c.issue, c.stage), (expected, Stage::Review));
    }
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
    w.tracker.pr(5, "bot/issue-7", "Closes #7");
    claimed(&w, "claude");
    let marks = w.tracker.claims(7);
    assert_eq!(marks.len(), 1);
    assert_eq!(marks[0].agent, "claude");
    assert_eq!(marks[0].stage, Stage::Review);
    assert_eq!(marks[0].branch, "bot/issue-7");
    assert_eq!(marks[0].pr, Some(5));
    assert_eq!(marks[0].claimed_at, NOW);
    assert_eq!(w.tracker.calls(), ["comment #7"]);
    // A claim is not a signal: the issue still waits on this agent.
    assert!(w.tracker.signals(7).is_empty());
}

#[test]
fn skips_blocked_done_and_stageless_issues() {
    let w = World::new();
    w.tracker.issue(1, &["agent:other", "stage:tdd", "blocked"]);
    w.tracker.issue(2, &["agent:other", "stage:done"]);
    w.tracker.issue(3, &["agent:other"]);
    nothing(&w, "other");
}

#[test]
fn skips_an_issue_this_agent_already_reported_for_this_stage() {
    let w = World::new();
    w.tracker.issue(1, &["agent:other", "stage:spec"]);
    w.tracker.add_comment(
        1,
        &signal(Stage::Spec, "other", Outcome::Ready),
        "2026-02-01T00:00:00Z",
    );
    nothing(&w, "other");
}

#[test]
fn a_new_stage_or_agent_makes_the_issue_eligible_again() {
    let w = World::new();
    w.tracker.issue(1, &["agent:other", "stage:tdd"]);
    w.tracker.add_comment(
        1,
        &signal(Stage::Spec, "other", Outcome::Ready),
        "2026-02-01T00:00:00Z",
    );
    assert_eq!(claimed(&w, "other").issue, 1);

    let w = World::new();
    w.tracker.issue(1, &["agent:claude", "stage:spec"]);
    w.tracker.add_comment(
        1,
        &signal(Stage::Spec, "other", Outcome::Blocked),
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
    w.tracker.pr(5, "bot/issue-7", "Closes #7");
    assert_eq!(claimed(&w, "claude").issue, 1);
    w.tracker.issues.borrow_mut().retain(|i| i.number == 7);
    w.tracker.comments.borrow_mut().clear();
    assert_eq!(claimed(&w, "claude").branch, "bot/issue-7");
    assert!(w.workspace.calls().is_empty(), "{:?}", w.workspace.calls());
}

#[test]
fn tdd_creates_the_work_branch_from_the_default_branch() {
    let w = World::new();
    w.tracker.issue(7, &["agent:other", "stage:tdd"]);
    let c = claimed(&w, "other");
    assert_eq!(c.branch, "other/issue-7");
    assert_eq!(
        w.workspace.calls(),
        ["status", "fetch", "create other/issue-7 from origin/main"]
    );
}

#[test]
fn tdd_continues_a_pushed_work_branch() {
    let w = World::new();
    w.workspace.remote.borrow_mut().push("other/issue-7".into());
    w.tracker.issue(7, &["agent:other", "stage:tdd"]);
    claimed(&w, "other");
    assert_eq!(
        w.workspace.calls(),
        [
            "status",
            "fetch",
            "create other/issue-7 from origin/other/issue-7"
        ]
    );
}

#[test]
fn tdd_continues_the_open_pull_request_branch() {
    let w = World::new();
    w.workspace.remote.borrow_mut().push("issue/7".into());
    w.tracker.pr(51, "issue/7", "Closes #7\n\nfirst try");
    w.tracker.issue(7, &["agent:other", "stage:tdd"]);
    let c = claimed(&w, "other");
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
    w.workspace.local.borrow_mut().push("other/issue-7".into());
    w.workspace.remote.borrow_mut().push("other/issue-7".into());
    w.tracker.issue(7, &["agent:other", "stage:tdd"]);
    claimed(&w, "other");
    assert_eq!(
        w.workspace.calls(),
        [
            "status",
            "fetch",
            "switch other/issue-7",
            "ff origin/other/issue-7"
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
    w.tracker.issue(1, &["agent:other", "stage:tdd"]);
    assert!(claim(w.services(), "other", None, &[], &[]).is_err());
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
    w.tracker.issue(1, &["agent:other", "stage:spec"]);
    w.tracker.issue(2, &["agent:other", "stage:spec"]);
    let first = claimed(&w, "other");
    let why = nothing(&w, "other");
    assert!(why.contains("#1"), "{why}");
    assert_eq!(status(w.services(), "other", None).unwrap(), Some(first));
    assert!(w.tracker.claims(2).is_empty());
}

#[test]
fn another_agents_claim_does_not_count() {
    let w = World::new();
    w.tracker
        .issue(1, &["agent:other", "agent:claude", "stage:spec"]);
    claimed(&w, "other");
    assert_eq!(status(w.services(), "claude", None).unwrap(), None);
}

#[test]
fn a_stale_claim_is_removed_and_replaced() {
    let w = World::new();
    w.tracker.issue(1, &["agent:other", "stage:spec"]);
    claimed(&w, "other");
    w.clock.0.set(NOW + STALE_AFTER_SECONDS);
    w.tracker.clear_calls();
    let again = claimed(&w, "other");
    assert_eq!(again.claimed_at, NOW + STALE_AFTER_SECONDS);
    assert_eq!(w.tracker.calls(), ["delete_comment #1", "comment #1"]);
    assert_eq!(w.tracker.claims(1).len(), 1);
}

#[test]
fn a_bad_agent_id_is_an_error() {
    let w = World::new();
    assert!(claim(w.services(), "Other", None, &[], &[]).is_err());
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
        .add_comment(1, &old_mark("other", Stage::Spec, 12 * 60 * 60), LONG_AGO);
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
            ("other".to_string(), NOW - 12 * 60 * 60),
            ("claude".to_string(), NOW)
        ]
    );
}

/// The list of recent comments can miss a claim that is not stale (a
/// comment deleted between two pages). The comments of the issue still
/// show it, so the run does not take the issue a second time.
#[test]
fn a_live_claim_that_the_recent_list_misses_still_holds_its_issue() {
    let w = World::new();
    w.tracker.issue(1, &["agent:claude", "stage:spec"]);
    // The fake list filters by the time of the comment, so this comment is
    // not in it. The mark itself is one minute old.
    w.tracker
        .add_comment(1, &old_mark("claude", Stage::Spec, 60), LONG_AGO);
    let why = nothing(&w, "claude");
    assert!(why.contains("issue #1 (spec) is still claimed"), "{why}");
    assert_eq!(w.tracker.claims(1).len(), 1);
    assert!(w.tracker.calls().is_empty());
}

#[test]
fn waits_for_closed_prerequisites_is_claimed() {
    let w = World::new();
    w.tracker.issue(1, &["agent:claude", "stage:spec"]);
    w.tracker
        .bodies
        .borrow_mut()
        .insert(1, "Waits for: #9".into());
    // #9 is not an open issue, so it is closed.
    assert_eq!(claimed(&w, "claude").issue, 1);
}

#[test]
fn an_open_prerequisite_skips_the_issue_and_takes_the_next() {
    let w = World::new();
    w.tracker.issue(1, &["agent:claude", "stage:spec", "p0"]);
    w.tracker.issue(2, &["agent:claude", "stage:spec"]);
    w.tracker.issue(9, &[]);
    w.tracker
        .bodies
        .borrow_mut()
        .insert(1, "Waits for: #9".into());
    w.tracker.clear_calls();
    assert_eq!(claimed(&w, "claude").issue, 2);
    assert!(
        w.tracker.claims(1).is_empty(),
        "no claim on the waiting issue"
    );
    assert!(!w.tracker.calls().iter().any(|c| c.contains("#1")));
}

#[test]
fn a_cross_repo_prerequisite_is_read_in_its_repository() {
    for (open, expected) in [(true, 2), (false, 1)] {
        let w = World::new();
        w.tracker.issue(1, &["agent:claude", "stage:spec", "p0"]);
        w.tracker.issue(2, &["agent:claude", "stage:spec"]);
        w.tracker
            .bodies
            .borrow_mut()
            .insert(1, "Waits for: faberline/core#7".into());
        if open {
            w.tracker
                .foreign_open
                .borrow_mut()
                .push("faberline/core#7".into());
        }
        assert_eq!(claimed(&w, "claude").issue, expected);
    }
}

#[test]
fn a_waits_line_with_no_number_warns_and_is_claimed() {
    let w = World::new();
    w.tracker.issue(1, &["agent:claude", "stage:spec"]);
    w.tracker
        .bodies
        .borrow_mut()
        .insert(1, "Waits for: the port adapter".into());
    assert_eq!(claimed(&w, "claude").issue, 1);
}

fn claimed_tdd(world: &World) -> Claim {
    match claim(world.services(), "other", Some(Stage::Tdd), &[], &[]).unwrap() {
        ClaimOutcome::Claimed(c) => c,
        ClaimOutcome::Nothing(why) => panic!("expected a claim, got nothing: {why}"),
    }
}

fn returned_world() -> World {
    let w = World::new();
    // #3 is new work. #9 came back from review: its pull request is open.
    w.tracker.issue(3, &["agent:other", "stage:tdd", "p1"]);
    w.tracker.issue(9, &["agent:other", "stage:tdd", "p1"]);
    w.tracker.pr(51, "issue/9", "Closes #9");
    w
}

#[test]
fn tdd_takes_an_issue_returned_from_review_before_older_new_work_of_the_same_priority() {
    // lumen, 2026-11-16: two reviewed pull requests waited behind about 22
    // newer issues, so review judged a stale head again and again.
    let w = returned_world();
    assert_eq!(claimed_tdd(&w).issue, 9);
}

#[test]
fn tdd_takes_higher_priority_new_work_before_a_returned_issue() {
    let w = returned_world();
    w.tracker.issue(12, &["agent:other", "stage:tdd", "p0"]);
    assert_eq!(claimed_tdd(&w).issue, 12);
}

#[test]
fn tdd_takes_new_work_of_a_higher_priority_before_a_returned_issue_of_a_lower_one() {
    let w = World::new();
    w.tracker.issue(3, &["agent:other", "stage:tdd", "p1"]);
    w.tracker.issue(9, &["agent:other", "stage:tdd", "p2"]);
    w.tracker.pr(51, "issue/9", "Closes #9");
    assert_eq!(claimed_tdd(&w).issue, 3);
}

#[test]
fn a_returned_issue_that_waits_on_an_open_issue_is_still_passed_over() {
    let w = World::new();
    w.tracker.issue(3, &["agent:other", "stage:tdd"]);
    w.tracker.issue(9, &["agent:other", "stage:tdd", "blocked"]);
    w.tracker.pr(51, "issue/9", "Closes #9");
    assert_eq!(claimed_tdd(&w).issue, 3);
}

#[test]
fn without_a_stage_a_returned_tdd_issue_gets_no_boost_over_review() {
    let w = World::new();
    w.tracker.issue(3, &["agent:other", "stage:review"]);
    w.tracker.pr(50, "issue/3", "Closes #3");
    w.tracker.issue(9, &["agent:other", "stage:tdd"]);
    w.tracker.pr(51, "issue/9", "Closes #9");
    assert_eq!(claimed(&w, "other").issue, 3);
}

fn rework(w: &World, stage: Stage) -> Result<ClaimOutcome, anyhow::Error> {
    claim_with(w.services(), "other", Some(stage), &[], &[], true)
}

#[test]
fn rework_only_tdd_takes_an_issue_with_an_open_pull_request() {
    let w = returned_world();
    match rework(&w, Stage::Tdd).unwrap() {
        ClaimOutcome::Claimed(c) => assert_eq!(c.issue, 9),
        ClaimOutcome::Nothing(why) => panic!("expected a claim: {why}"),
    }
}

#[test]
fn rework_only_tdd_takes_an_issue_whose_newest_signal_is_a_review_failed() {
    let w = World::new();
    w.tracker.issue(3, &["agent:other", "stage:tdd"]);
    w.tracker.issue(4, &["agent:other", "stage:tdd"]);
    w.tracker.add_comment(
        4,
        &signal(Stage::Review, "other", Outcome::Failed),
        "2026-01-01T00:00:00Z",
    );
    match rework(&w, Stage::Tdd).unwrap() {
        ClaimOutcome::Claimed(c) => assert_eq!(c.issue, 4),
        ClaimOutcome::Nothing(why) => panic!("expected a claim: {why}"),
    }
}

#[test]
fn rework_only_tdd_does_not_take_new_work() {
    let w = World::new();
    w.tracker.issue(3, &["agent:other", "stage:tdd"]);
    match rework(&w, Stage::Tdd).unwrap() {
        ClaimOutcome::Nothing(why) => assert!(why.contains("no returned tdd issue"), "{why}"),
        ClaimOutcome::Claimed(c) => panic!("claimed new work #{}", c.issue),
    }
    assert!(w.tracker.claims(3).is_empty());
}

#[test]
fn rework_only_spec_takes_an_issue_that_tdd_sent_back() {
    let w = World::new();
    w.tracker.issue(1, &["agent:other", "stage:spec"]);
    w.tracker.issue(2, &["agent:other", "stage:spec"]);
    w.tracker.add_comment(
        2,
        &signal(Stage::Tdd, "other", Outcome::Respec),
        "2026-01-01T00:00:00Z",
    );
    match rework(&w, Stage::Spec).unwrap() {
        ClaimOutcome::Claimed(c) => assert_eq!(c.issue, 2),
        ClaimOutcome::Nothing(why) => panic!("expected a claim: {why}"),
    }
}

#[test]
fn rework_only_spec_does_not_take_an_issue_that_never_had_a_spec() {
    let w = World::new();
    w.tracker.issue(1, &["agent:other", "stage:spec"]);
    assert!(matches!(
        rework(&w, Stage::Spec).unwrap(),
        ClaimOutcome::Nothing(_)
    ));
}

#[test]
fn rework_only_needs_a_spec_or_tdd_stage() {
    let w = World::new();
    assert!(rework(&w, Stage::Review).is_err());
    assert!(claim_with(w.services(), "other", None, &[], &[], true).is_err());
}

fn in_milestone(w: &World, number: u64, title: &str) {
    for i in w.tracker.issues.borrow_mut().iter_mut() {
        if i.number == number {
            i.milestone = Some(title.into());
        }
    }
}

#[test]
fn rework_only_tdd_takes_the_returned_issue_that_has_waited_longest() {
    // lumen, 2026-11-17 (aw#536): two pull requests that review sent back on
    // 2026-11-15 waited two days. Returned issues of lower milestones came
    // back from review again and again and went first every time.
    let w = World::new();
    w.tracker.issue(5, &["agent:other", "stage:tdd", "p1"]);
    w.tracker.pr(51, "issue/5", "Closes #5");
    in_milestone(&w, 5, "lib@0.13.0");
    w.tracker.add_comment(
        5,
        &signal(Stage::Review, "other", Outcome::Failed),
        "2026-01-16T06:00:00Z",
    );
    w.tracker.issue(12, &["agent:other", "stage:tdd", "p1"]);
    w.tracker.pr(52, "issue/12", "Closes #12");
    in_milestone(&w, 12, "lib@0.26.0");
    w.tracker.add_comment(
        12,
        &signal(Stage::Review, "other", Outcome::Failed),
        "2026-01-15T17:05:00Z",
    );
    // Notes of a person after the signal do not change who waited longest.
    w.tracker
        .add_comment(12, "Desk: priority raised.", "2026-01-15T17:25:00Z");
    match rework(&w, Stage::Tdd).unwrap() {
        ClaimOutcome::Claimed(c) => assert_eq!(c.issue, 12),
        ClaimOutcome::Nothing(why) => panic!("expected a claim: {why}"),
    }
}

#[test]
fn rework_only_tdd_keeps_the_priority_before_the_wait() {
    let w = World::new();
    w.tracker.issue(5, &["agent:other", "stage:tdd", "p1"]);
    w.tracker.pr(51, "issue/5", "Closes #5");
    w.tracker.add_comment(
        5,
        &signal(Stage::Review, "other", Outcome::Failed),
        "2026-01-16T06:00:00Z",
    );
    w.tracker.issue(12, &["agent:other", "stage:tdd", "p2"]);
    w.tracker.pr(52, "issue/12", "Closes #12");
    w.tracker.add_comment(
        12,
        &signal(Stage::Review, "other", Outcome::Failed),
        "2026-01-15T17:05:00Z",
    );
    match rework(&w, Stage::Tdd).unwrap() {
        ClaimOutcome::Claimed(c) => assert_eq!(c.issue, 5),
        ClaimOutcome::Nothing(why) => panic!("expected a claim: {why}"),
    }
}

// faberline/aw#542: the first merge error is kept, and one issue that cannot
// be prepared does not stop the run.

fn two_returned(w: &World) {
    for (n, pr) in [(7, 51), (8, 52)] {
        w.workspace.remote.borrow_mut().push(format!("issue/{n}"));
        w.tracker
            .pr(pr, &format!("issue/{n}"), &format!("Closes #{n}"));
        w.tracker.issue(n, &["agent:claude", "stage:tdd"]);
    }
    w.workspace.behind.set(true);
}

#[test]
fn a_merge_error_without_a_conflict_is_reported_with_its_own_text() {
    let w = World::new();
    tdd_with_pr(&w, "issue/7");
    w.workspace.behind.set(true);
    *w.workspace.merge_error.borrow_mut() = Some("untracked files would be overwritten".into());
    let why = nothing(&w, "claude");
    assert!(why.contains("no open"), "{why}");
    let comments = aw::domain::work::ports::Tracker::comments(&w.tracker, 7).unwrap();
    let text = &comments.last().unwrap().body;
    assert!(
        text.contains("untracked files would be overwritten"),
        "{text}"
    );
    assert!(text.contains("git merge origin/main failed"), "{text}");
    // A merge that fails is not a conflict: no new branch, no closed PR.
    let calls = w.workspace.calls();
    assert!(!calls.iter().any(|c| c.contains("issue/7-r2")), "{calls:?}");
    assert!(!w.tracker.calls().iter().any(|c| c.starts_with("close_pr")));
    assert!(w.tracker.claims(7).is_empty());
}

#[test]
fn one_issue_that_cannot_be_prepared_is_skipped_and_the_next_is_taken() {
    let w = World::new();
    two_returned(&w);
    *w.workspace.merge_error.borrow_mut() = Some("fatal: refusing to merge".into());
    let c = claimed(&w, "claude");
    assert_eq!(c.issue, 8);
    assert!(w.tracker.claims(7).is_empty());
    assert!(w.tracker.calls().contains(&"comment #7".to_string()));
    // The checkout was prepared once for the run.
    let fetches = w.workspace.calls().iter().filter(|c| *c == "fetch").count();
    assert_eq!(fetches, 1);
}

#[test]
fn rework_only_skips_a_bad_returned_issue_and_takes_the_next() {
    let w = World::new();
    w.tracker.issue(7, &["agent:other", "stage:tdd"]);
    w.tracker.issue(8, &["agent:other", "stage:tdd"]);
    for (n, pr) in [(7, 51), (8, 52)] {
        w.workspace.remote.borrow_mut().push(format!("issue/{n}"));
        w.tracker
            .pr(pr, &format!("issue/{n}"), &format!("Closes #{n}"));
    }
    w.workspace.behind.set(true);
    *w.workspace.merge_error.borrow_mut() = Some("boom".into());
    match rework(&w, Stage::Tdd).unwrap() {
        ClaimOutcome::Claimed(c) => assert_eq!(c.issue, 8),
        ClaimOutcome::Nothing(why) => panic!("expected a claim: {why}"),
    }
}

// faberline/aw#538: `expedite` is new work that backpressure lets through.

#[test]
fn rework_only_takes_a_new_expedite_issue_in_spec_and_tdd() {
    for (stage, label) in [(Stage::Spec, "stage:spec"), (Stage::Tdd, "stage:tdd")] {
        let w = World::new();
        w.tracker.issue(1, &["agent:other", label, "p1"]);
        w.tracker
            .issue(2, &["agent:other", label, "p1", "expedite"]);
        match rework(&w, stage).unwrap() {
            ClaimOutcome::Claimed(c) => assert_eq!((c.issue, c.stage), (2, stage)),
            ClaimOutcome::Nothing(why) => panic!("expected a claim: {why}"),
        }
    }
}

#[test]
fn rework_only_still_refuses_new_work_without_the_expedite_label() {
    let w = World::new();
    w.tracker.issue(1, &["agent:other", "stage:spec", "p0"]);
    w.tracker.issue(2, &["agent:other", "stage:spec", "ci-fix"]);
    assert!(matches!(
        rework(&w, Stage::Spec).unwrap(),
        ClaimOutcome::Nothing(_)
    ));
}

#[test]
fn an_expedite_issue_goes_before_other_work_but_after_p0() {
    let w = World::new();
    w.tracker
        .issue(1, &["agent:claude", "stage:spec", "p1", "ci-fix"]);
    w.tracker
        .issue(2, &["agent:claude", "stage:spec", "p1", "expedite"]);
    assert_eq!(claimed(&w, "claude").issue, 2);
    let w = World::new();
    w.tracker.issue(1, &["agent:claude", "stage:spec", "p0"]);
    w.tracker
        .issue(2, &["agent:claude", "stage:spec", "p1", "expedite"]);
    assert_eq!(claimed(&w, "claude").issue, 1);
}

#[test]
fn an_idle_review_claim_updates_the_next_behind_pr_in_order() {
    let w = World::new();
    // Other agents' issues: `claude` has nothing to claim.
    w.tracker.issue(8, &["agent:other", "stage:review", "p2"]);
    w.tracker.pr(6, "other/issue-8", "Closes #8");
    w.tracker
        .issue(9, &["agent:other", "stage:review", "p3", "expedite"]);
    w.tracker.pr(4, "other/issue-9", "Closes #9");
    w.tracker.behind.set(2);
    let why = nothing(&w, "claude");
    assert!(
        why.contains("updated the branch of pull request #4"),
        "{why}"
    );
    assert_eq!(w.tracker.calls(), ["update_branch #4"]);
    // The updated pull request is now in flight: no second update.
    w.tracker
        .behind_heads
        .borrow_mut()
        .insert("other/issue-9".into(), 0);
    w.tracker.calls.borrow_mut().clear();
    let why = nothing(&w, "claude");
    assert!(!why.contains("updated the branch"), "{why}");
    assert!(w.tracker.calls().is_empty());
}

#[test]
fn a_queued_ci_run_without_check_runs_is_an_update_in_flight() {
    // aw#570: after `aw` updates a branch, its CI run is queued and has no
    // job, so the required check `gate` does not exist yet. That must count
    // as in flight, or each later claim updates one more pull request.
    let w = World::new();
    w.tracker.issue(8, &["agent:other", "stage:review", "p2"]);
    w.tracker.pr(6, "other/issue-8", "Closes #8");
    w.tracker
        .issue(9, &["agent:other", "stage:review", "p3", "expedite"]);
    w.tracker.pr(4, "other/issue-9", "Closes #9");
    *w.tracker.required.borrow_mut() = vec!["gate".into()];
    w.tracker.behind.set(2);
    let why = nothing(&w, "claude");
    assert!(why.contains("pull request #4"), "{why}");
    // #4 is updated; its run is queued and has no check run.
    w.tracker
        .behind_heads
        .borrow_mut()
        .insert("other/issue-9".into(), 0);
    w.tracker
        .unfinished_heads
        .borrow_mut()
        .push("other/issue-9".into());
    w.tracker.calls.borrow_mut().clear();
    // A second pass in the same run, and a later run: no update.
    for _ in 0..2 {
        let why = nothing(&w, "claude");
        assert!(!why.contains("updated the branch"), "{why}");
        assert!(w.tracker.calls().is_empty(), "{:?}", w.tracker.calls());
    }
    // Once CI finished without the required check, it cannot merge next:
    // the next pull request is updated (the 0.7.7 rule stays).
    w.tracker.unfinished_heads.borrow_mut().clear();
    let why = nothing(&w, "claude");
    assert!(why.contains("pull request #6"), "{why}");
}

#[test]
fn an_idle_claim_updates_nothing_for_a_stage_that_is_not_review() {
    let w = World::new();
    w.tracker.issue(8, &["agent:other", "stage:review"]);
    w.tracker.pr(6, "other/issue-8", "Closes #8");
    w.tracker.behind.set(2);
    let outcome = claim(w.services(), "claude", Some(Stage::Spec), &[], &[]).unwrap();
    assert!(matches!(outcome, ClaimOutcome::Nothing(_)));
    assert!(w.tracker.calls().is_empty());
}

#[test]
fn an_idle_claim_that_cannot_update_still_says_nothing_to_claim() {
    let w = World::new();
    w.tracker.issue(8, &["agent:other", "stage:review"]);
    w.tracker.pr(6, "other/issue-8", "Closes #8");
    w.tracker.behind.set(2);
    w.tracker.fail_update_branch.set(true);
    let why = nothing(&w, "claude");
    assert!(!why.contains("updated the branch"), "{why}");
}

#[test]
fn an_unreadable_dependency_waits_one_issue_and_the_run_goes_on() {
    let w = World::new();
    w.tracker.issue(1, &["agent:claude", "stage:spec", "p0"]);
    w.tracker.issue(2, &["agent:claude", "stage:spec"]);
    w.tracker
        .bodies
        .borrow_mut()
        .insert(1, "Waits for: faberline/som#249".into());
    w.tracker
        .foreign_unreadable
        .borrow_mut()
        .push("faberline/som#249".into());
    assert_eq!(claimed(&w, "claude").issue, 2);
    assert!(w.tracker.claims(1).is_empty());
}

fn desk_world(stage_label: &str, signal_stage: Stage) -> World {
    let w = World::new();
    w.tracker.issue(4, &["agent:other", stage_label]);
    w.tracker.add_comment(
        4,
        &signal(signal_stage, "other", Outcome::Ready),
        "2026-01-01T00:00:00Z",
    );
    w
}

#[test]
fn a_desk_comment_after_a_ready_signal_sends_a_tdd_issue_back() {
    let w = desk_world("stage:tdd", Stage::Tdd);
    w.tracker.pr(51, "issue/4", "Closes #4");
    // Without the desk comment the issue is not claimed (faberline/sift#204).
    assert!(matches!(
        rework(&w, Stage::Tdd).unwrap(),
        ClaimOutcome::Nothing(_)
    ));
    w.tracker
        .add_comment(4, "desk: new target, redo", "2026-01-02T00:00:00Z");
    match rework(&w, Stage::Tdd).unwrap() {
        ClaimOutcome::Claimed(c) => assert_eq!(c.issue, 4),
        ClaimOutcome::Nothing(why) => panic!("expected a claim: {why}"),
    }
}

#[test]
fn a_desk_comment_after_a_ready_signal_sends_a_spec_issue_back() {
    let w = desk_world("stage:spec", Stage::Spec);
    w.tracker
        .add_comment(4, "Desk, please redo the spec", "2026-01-02T00:00:00Z");
    match rework(&w, Stage::Spec).unwrap() {
        ClaimOutcome::Claimed(c) => assert_eq!(c.issue, 4),
        ClaimOutcome::Nothing(why) => panic!("expected a claim: {why}"),
    }
}

#[test]
fn other_comments_and_older_desk_comments_do_not_send_an_issue_back() {
    let w = desk_world("stage:spec", Stage::Spec);
    w.tracker
        .add_comment(4, "desk: before the signal", "2025-12-31T00:00:00Z");
    w.tracker
        .add_comment(4, "looks fine to me", "2026-01-02T00:00:00Z");
    assert!(matches!(
        rework(&w, Stage::Spec).unwrap(),
        ClaimOutcome::Nothing(_)
    ));
}

#[test]
fn a_waits_text_after_a_sentence_holds_the_issue_back() {
    let w = World::new();
    w.tracker.issue(1, &["agent:claude", "stage:spec", "p0"]);
    w.tracker.issue(2, &["agent:claude", "stage:spec"]);
    w.tracker.issue(9, &["agent:claude", "stage:spec"]);
    w.tracker
        .bodies
        .borrow_mut()
        .insert(1, "Part of #7. Waits for: #9".into());
    assert_eq!(claimed(&w, "claude").issue, 2);
}

/// A tdd issue whose old branch is on origin and has no open pull request
/// (faberline/aw#614: its pull request was closed, not merged).
fn tdd_with_old_branch_only(w: &World, head: &str) {
    w.workspace.remote.borrow_mut().push(head.into());
    w.tracker.issue(7, &["agent:claude", "stage:tdd"]);
}

#[test]
fn a_redo_starts_a_new_branch_when_the_old_branch_has_no_open_pull_request() {
    let w = World::new();
    tdd_with_old_branch_only(&w, "claude/issue-7");
    w.tracker
        .add_comment(7, &spec_ready("aw:redo"), "2026-02-01T00:00:00Z");
    let c = claimed(&w, "claude");
    assert_eq!((c.branch.as_str(), c.pr), ("claude/issue-7-r2", None));
    // Nothing is closed, merged or deleted.
    assert!(!w.workspace.calls().iter().any(|c| c.starts_with("merge")));
    assert!(!w.tracker.calls().iter().any(|c| c.starts_with("close_pr")));
}

#[test]
fn a_redo_with_no_old_branch_uses_the_plain_branch() {
    let w = World::new();
    w.tracker.issue(7, &["agent:claude", "stage:tdd"]);
    w.tracker
        .add_comment(7, &spec_ready("aw:redo"), "2026-02-01T00:00:00Z");
    assert_eq!(claimed(&w, "claude").branch, "claude/issue-7");
}

#[test]
fn an_old_branch_with_no_open_pull_request_and_no_redo_is_reused() {
    let w = World::new();
    tdd_with_old_branch_only(&w, "claude/issue-7");
    assert_eq!(claimed(&w, "claude").branch, "claude/issue-7");
}

#[test]
fn a_blocked_signal_and_an_old_branch_claim_do_not_use_up_a_redo() {
    // faberline/aw#620: spec `aw:redo`, then tdd `blocked`, then a claim
    // that kept the old branch. The next claim still owes the redo.
    let w = World::new();
    tdd_with_old_branch_only(&w, "claude/issue-7");
    let mark = "<!-- aw:claim stage=tdd agent=claude branch=claude/issue-7 pr=- at=1000 -->";
    w.tracker.add_comment(7, mark, "2026-02-01T00:00:00Z");
    w.tracker
        .add_comment(7, &spec_ready("aw:redo"), "2026-02-02T00:00:00Z");
    w.tracker.add_comment(7, mark, "2026-02-02T01:00:00Z");
    w.tracker.add_comment(
        7,
        &signal(Stage::Tdd, "claude", Outcome::Blocked),
        "2026-02-02T02:00:00Z",
    );
    w.tracker
        .add_comment(7, "desk: use the redo", "2026-02-02T03:00:00Z");
    assert_eq!(claimed(&w, "claude").branch, "claude/issue-7-r2");
}

#[test]
fn tdd_rework_starts_from_the_remote_head_even_when_fetch_does_not_bring_it() {
    // A single-branch clone: `git fetch` brings main only (faberline/sift#258).
    let w = World::new();
    w.tracker.pr(51, "claude/issue-7", "Closes #7");
    w.tracker.issue(7, &["agent:claude", "stage:tdd"]);
    w.workspace
        .hidden
        .borrow_mut()
        .push("claude/issue-7".into());
    let c = claimed(&w, "claude");
    assert_eq!((c.branch.as_str(), c.pr), ("claude/issue-7", Some(51)));
    let calls = w.workspace.calls();
    assert!(
        calls.contains(&"create claude/issue-7 from origin/claude/issue-7".to_string()),
        "{calls:?}"
    );
    assert!(
        !calls.iter().any(|c| c.contains("from origin/main")),
        "{calls:?}"
    );
}

#[test]
fn an_issue_that_names_itself_in_waits_for_is_still_claimed() {
    // faberline/aw#664: som#291 had `Waits for: #290, #291`.
    let w = World::new();
    w.tracker.issue(1, &["agent:claude", "stage:tdd"]);
    w.tracker
        .bodies
        .borrow_mut()
        .insert(1, "Text.\nWaits for: #1".into());
    assert_eq!(claimed(&w, "claude").issue, 1);
}

fn respecs(w: &World, issue: u64, hours: &[&str]) {
    for h in hours {
        w.tracker.add_comment(
            issue,
            &signal(Stage::Tdd, "claude", Outcome::Respec),
            &format!("2026-02-01T{h}:00:00Z"),
        );
    }
}

#[test]
fn three_respec_signals_stop_the_spec_loop_and_ask_the_desk() {
    // faberline/aw#659: lumen#47 had four respec signals and four specs.
    let w = World::new();
    w.tracker.issue(1, &["agent:claude", "stage:spec", "p0"]);
    w.tracker.issue(2, &["agent:claude", "stage:spec"]);
    respecs(&w, 1, &["01", "02", "03"]);
    // The loop issue is skipped, the next issue is claimed.
    assert_eq!(claimed(&w, "claude").issue, 2);
    let labels = w.tracker.issues.borrow()[0].labels.clone();
    assert!(labels.contains(&"blocked".to_string()), "{labels:?}");
    let text = w.tracker.last_comment(1);
    assert!(text.contains("outcome=blocked"), "{text}");
    assert!(
        text.contains("Decision: the spec and tdd runs of #1"),
        "{text}"
    );
    assert!(
        w.tracker
            .calls()
            .contains(&"add_labels #1 blocked".to_string()),
        "{:?}",
        w.tracker.calls()
    );
}

#[test]
fn two_respec_signals_still_get_a_new_spec() {
    let w = World::new();
    w.tracker.issue(1, &["agent:claude", "stage:spec"]);
    respecs(&w, 1, &["01", "02"]);
    assert_eq!(claimed(&w, "claude").issue, 1);
}

#[test]
fn a_comment_of_a_person_starts_the_respec_count_again() {
    let w = World::new();
    w.tracker.issue(1, &["agent:claude", "stage:spec"]);
    respecs(&w, 1, &["01", "02", "03"]);
    w.tracker
        .add_comment(1, "desk: cover the live spec too.", "2026-02-01T04:00:00Z");
    respecs(&w, 1, &["05"]);
    assert_eq!(claimed(&w, "claude").issue, 1);
}

#[test]
fn a_spec_claim_says_what_the_branch_and_the_pull_request_hold() {
    let w = World::new();
    w.tracker.issue(1, &["agent:claude", "stage:spec"]);
    w.tracker
        .branches
        .borrow_mut()
        .insert("claude/issue-1".into(), "33b73eea".into());
    w.tracker.pr(5, "claude/issue-1", "Closes #1");
    assert_eq!(claimed(&w, "claude").issue, 1);
    // A failing read is a warning, not an error.
    let w = World::new();
    w.tracker.issue(1, &["agent:claude", "stage:spec"]);
    w.tracker.fail_branch_head.set(true);
    assert_eq!(claimed(&w, "claude").issue, 1);
}

fn diverged_world(branch: &str) -> World {
    let w = World::new();
    w.tracker.issue(7, &["agent:other", "stage:tdd", "p1"]);
    w.tracker.pr(5, branch, "Closes #7");
    w.workspace.local.borrow_mut().push(branch.into());
    w.workspace.remote.borrow_mut().push(branch.into());
    w
}

#[test]
fn a_local_work_branch_that_only_lags_fast_forwards() {
    let w = diverged_world("other/issue-7");
    let c = claimed_tdd(&w);
    assert_eq!(c.branch, "other/issue-7");
    let calls = w.workspace.calls();
    assert!(
        calls.contains(&"ff origin/other/issue-7".to_string()),
        "{calls:?}"
    );
    assert!(!calls.iter().any(|c| c.starts_with("reset ")), "{calls:?}");
}

#[test]
fn a_local_work_branch_with_extra_commits_is_backed_up_and_reset_to_origin() {
    let w = diverged_world("other/issue-7");
    w.workspace
        .local_only
        .borrow_mut()
        .push("other/issue-7".into());
    let c = claimed_tdd(&w);
    assert_eq!(c.branch, "other/issue-7");
    let calls = w.workspace.calls();
    let at = |what: &str| calls.iter().position(|c| c.starts_with(what));
    let backup = at("backup refs/aw/backup/other/issue-7-").expect("a backup ref");
    let reset = at("reset origin/other/issue-7").expect("a reset");
    assert!(backup < reset, "{calls:?}");
    assert!(w.tracker.signals(7).is_empty(), "no blocked signal");
}

#[test]
fn a_diverged_branch_with_a_dirty_tree_is_a_blocked_signal_that_names_the_fix() {
    let w = diverged_world("other/issue-7");
    w.workspace
        .local_only
        .borrow_mut()
        .push("other/issue-7".into());
    w.workspace.dirty_after_fetch.set(true);
    // The dirty tree stops the run, and #7 says why.
    let err = claim(w.services(), "other", Some(Stage::Tdd), &[], &[]).unwrap_err();
    assert!(format!("{err:#}").contains("git branch -f"), "{err:#}");
    let signals = w.tracker.signals(7);
    assert_eq!(signals.len(), 1);
    assert_eq!(signals[0].outcome, Outcome::Blocked);
    let comments = w.tracker.last_comment(7);
    assert!(
        comments.contains("git branch -f other/issue-7 origin/other/issue-7"),
        "{comments}"
    );
    assert!(!w.workspace.calls().iter().any(|c| c.starts_with("reset ")));
}
