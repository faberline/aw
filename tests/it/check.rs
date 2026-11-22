//! `aw claim --check`: the same answer as a real claim, and no write.

use aw::application::work::{ClaimOutcome, NoWork, Verdict, check, claim_with};
use aw::domain::work::signal::{Outcome, Signal};
use aw::domain::work::stage::Stage;

use crate::support::World;

fn signal(stage: Stage, outcome: Outcome) -> String {
    Signal {
        stage,
        agent: "other".into(),
        outcome,
    }
    .render("", None)
}

fn verdict(w: &World, stage: Option<Stage>) -> Verdict {
    check(w.services(), "other", stage, &[], &[])
        .unwrap()
        .verdict
}

#[test]
fn an_empty_queue_has_no_work() {
    let w = World::new();
    for stage in [Stage::Spec, Stage::Tdd, Stage::Review] {
        assert_eq!(
            verdict(&w, Some(stage)),
            Verdict::NoWork(NoWork::EmptyQueue)
        );
    }
}

#[test]
fn the_line_names_work_and_reason() {
    let w = World::new();
    w.tracker.issue(7, &["agent:other", "stage:review"]);
    w.tracker.pr(70, "aw/other-7", "Closes #7");
    let c = check(w.services(), "other", Some(Stage::Review), &[], &[]).unwrap();
    assert_eq!(
        c.to_string(),
        "work stage=review reason=claim issue=7 review_waiting=1 tdd_waiting=0"
    );
    let c = check(w.services(), "other", Some(Stage::Tdd), &[], &[]).unwrap();
    assert_eq!(
        c.to_string(),
        "no-work stage=tdd reason=empty-queue review_waiting=1 tdd_waiting=0"
    );
}

#[test]
fn a_check_writes_nothing() {
    let w = World::new();
    w.tracker.issue(7, &["agent:other", "stage:spec"]);
    w.tracker.issue(8, &["agent:other", "stage:review"]); // no pull request
    w.tracker.issue(9, &["agent:other", "stage:tdd"]);
    for stage in [Stage::Spec, Stage::Tdd, Stage::Review] {
        verdict(&w, Some(stage));
    }
    assert_eq!(w.tracker.calls(), Vec::<String>::new());
    assert!(w.tracker.claims(7).is_empty());
}

#[test]
fn it_agrees_with_a_real_claim() {
    // Same board, same pick: the check and the claim share `select`.
    let build = || {
        let w = World::new();
        w.tracker.issue(1, &["agent:other", "stage:spec", "p2"]);
        w.tracker
            .issue(2, &["agent:other", "stage:spec", "p1", "blocked"]);
        w.tracker.issue(3, &["agent:other", "stage:spec", "p1"]);
        w
    };
    let picked = match claim_with(
        build().services(),
        "other",
        Some(Stage::Spec),
        &[],
        &[],
        false,
    )
    .unwrap()
    {
        ClaimOutcome::Claimed(c) => c.issue,
        ClaimOutcome::Nothing(why) => panic!("{why}"),
    };
    assert_eq!(
        verdict(&build(), Some(Stage::Spec)),
        Verdict::Claim {
            issue: picked,
            stage: Stage::Spec
        }
    );
    assert_eq!(picked, 3);
}

#[test]
fn a_waiting_issue_is_passed_over_like_a_claim_does() {
    let w = World::new();
    w.tracker.issue(5, &["agent:other", "stage:spec"]);
    w.tracker
        .bodies
        .borrow_mut()
        .insert(5, "Waits for: #9".into());
    w.tracker.issue(9, &["agent:other", "stage:review"]);
    assert_eq!(
        verdict(&w, Some(Stage::Spec)),
        Verdict::NoWork(NoWork::PassedOver)
    );
}

#[test]
fn only_blocked_issues_are_all_blocked() {
    let w = World::new();
    w.tracker.issue(5, &["agent:other", "stage:tdd", "blocked"]);
    assert_eq!(
        verdict(&w, Some(Stage::Tdd)),
        Verdict::NoWork(NoWork::AllBlocked)
    );
}

#[test]
fn review_backpressure_holds_new_tdd_but_not_returned_work() {
    let w = World::new();
    for n in 1..=3 {
        w.tracker.issue(n, &["agent:other", "stage:review"]);
    }
    w.tracker.issue(10, &["agent:other", "stage:tdd"]);
    assert_eq!(
        verdict(&w, Some(Stage::Tdd)),
        Verdict::NoWork(NoWork::Backpressure)
    );
    w.tracker.issue(11, &["agent:other", "stage:tdd"]);
    w.tracker.add_comment(
        11,
        &signal(Stage::Review, Outcome::Failed),
        "2026-01-01T00:00:00Z",
    );
    assert_eq!(
        verdict(&w, Some(Stage::Tdd)),
        Verdict::Claim {
            issue: 11,
            stage: Stage::Tdd
        }
    );
}

#[test]
fn a_blocked_review_issue_does_not_count_toward_backpressure() {
    let w = World::new();
    w.tracker.issue(1, &["agent:other", "stage:review"]);
    w.tracker.issue(2, &["agent:other", "stage:review"]);
    w.tracker
        .issue(3, &["agent:other", "stage:review", "blocked"]);
    w.tracker.issue(10, &["agent:other", "stage:tdd"]);
    assert_eq!(
        verdict(&w, Some(Stage::Tdd)),
        Verdict::Claim {
            issue: 10,
            stage: Stage::Tdd
        }
    );
}

#[test]
fn tdd_backpressure_holds_new_spec() {
    let w = World::new();
    for n in 1..=5 {
        w.tracker.issue(n, &["agent:other", "stage:tdd"]);
    }
    w.tracker.issue(20, &["agent:other", "stage:spec"]);
    assert_eq!(
        verdict(&w, Some(Stage::Spec)),
        Verdict::NoWork(NoWork::Backpressure)
    );
    // Review has no backpressure rule.
    assert_eq!(
        verdict(&w, Some(Stage::Review)),
        Verdict::NoWork(NoWork::EmptyQueue)
    );
}

#[test]
fn expedite_passes_backpressure() {
    let w = World::new();
    for n in 1..=3 {
        w.tracker.issue(n, &["agent:other", "stage:review"]);
    }
    w.tracker
        .issue(10, &["agent:other", "stage:tdd", "expedite"]);
    assert_eq!(
        verdict(&w, Some(Stage::Tdd)),
        Verdict::Claim {
            issue: 10,
            stage: Stage::Tdd
        }
    );
}

#[test]
fn a_live_claim_is_work_to_resume() {
    let w = World::new();
    w.tracker.issue(7, &["agent:other", "stage:spec"]);
    claim_with(w.services(), "other", Some(Stage::Spec), &[], &[], false).unwrap();
    assert_eq!(
        verdict(&w, Some(Stage::Spec)),
        Verdict::Held {
            issue: 7,
            stage: Stage::Spec
        }
    );
}

#[test]
fn the_spec_run_has_plan_work() {
    let w = World::new();
    // Untriaged issue, with no agent label.
    w.tracker.issue(4, &["bug"]);
    assert_eq!(
        verdict(&w, Some(Stage::Spec)),
        Verdict::Plan {
            issue: 4,
            why: "untriaged"
        }
    );
    // The tdd and review runs do not plan.
    assert_eq!(
        verdict(&w, Some(Stage::Tdd)),
        Verdict::NoWork(NoWork::EmptyQueue)
    );
}

#[test]
fn a_label_move_the_plan_must_finish_is_work() {
    let w = World::new();
    w.tracker.issue(4, &["agent:other", "stage:tdd"]);
    w.tracker.add_comment(
        4,
        &signal(Stage::Tdd, Outcome::Ready),
        "2026-01-01T00:00:00Z",
    );
    assert_eq!(
        verdict(&w, Some(Stage::Spec)),
        Verdict::Plan {
            issue: 4,
            why: "label-move"
        }
    );
}

#[test]
fn a_blocked_issue_waiting_for_a_person_is_not_plan_work() {
    let w = World::new();
    w.tracker
        .issue(4, &["agent:other", "stage:spec", "blocked"]);
    w.tracker.add_comment(
        4,
        &signal(Stage::Spec, Outcome::Blocked),
        "2026-01-01T00:00:00Z",
    );
    assert_eq!(
        verdict(&w, Some(Stage::Spec)),
        Verdict::NoWork(NoWork::AllBlocked)
    );
    // A person answers: the plan may act.
    w.tracker
        .add_comment(4, "Use option A.", "2026-01-02T00:00:00Z");
    assert_eq!(
        verdict(&w, Some(Stage::Spec)),
        Verdict::Plan {
            issue: 4,
            why: "blocked-answerable"
        }
    );
}

#[test]
fn backpressure_skips_the_plan_of_the_spec_run() {
    let w = World::new();
    for n in 1..=3 {
        w.tracker.issue(n, &["agent:other", "stage:review"]);
    }
    w.tracker.issue(4, &["bug"]);
    assert_eq!(
        verdict(&w, Some(Stage::Spec)),
        Verdict::NoWork(NoWork::Backpressure)
    );
}

#[test]
fn the_skill_limits_match_the_code() {
    let skill = std::fs::read_to_string(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/plugins/faberline/skills/aw-worker/SKILL.md"
    ))
    .unwrap();
    assert!(skill.contains(&format!(
        "| `BACKPRESSURE_REVIEW_LIMIT` | {} |",
        aw::domain::work::issue::BACKPRESSURE_REVIEW_LIMIT
    )));
    assert!(skill.contains(&format!(
        "| `BACKPRESSURE_TDD_LIMIT` | {} |",
        aw::domain::work::issue::BACKPRESSURE_TDD_LIMIT
    )));
}

#[test]
fn desk_issues_are_not_waiting_for_triage() {
    let w = World::new();
    w.tracker.issue(1, &["status"]);
    w.tracker.issue(2, &["decision"]);
    w.tracker.issue(3, &["pipeline"]);
    assert_eq!(
        verdict(&w, Some(Stage::Spec)),
        Verdict::NoWork(NoWork::EmptyQueue)
    );
}
