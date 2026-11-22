//! More `aw claim --check` cases: the text of every verdict and the plan.

use aw::application::work::{check, claim_with};
use aw::domain::work::issue::{Comment, plan_attention};
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

#[test]
fn every_verdict_has_one_line() {
    let w = World::new();
    w.tracker.issue(7, &["agent:other", "stage:spec"]);
    claim_with(w.services(), "other", Some(Stage::Spec), &[], &[], false).unwrap();
    let held = check(w.services(), "other", Some(Stage::Spec), &[], &[]).unwrap();
    assert_eq!(
        held.to_string(),
        "work stage=spec reason=held issue=7 review_waiting=0 tdd_waiting=0"
    );
    assert!(held.has_work());

    let w = World::new();
    w.tracker.issue(4, &["bug"]);
    let plan = check(w.services(), "other", Some(Stage::Spec), &[], &[]).unwrap();
    assert_eq!(
        plan.to_string(),
        "work stage=spec reason=plan issue=4 why=untriaged review_waiting=0 tdd_waiting=0"
    );

    let w = World::new();
    w.tracker.issue(5, &["agent:other", "stage:tdd", "blocked"]);
    let none = check(w.services(), "other", Some(Stage::Tdd), &[], &[]).unwrap();
    assert_eq!(
        none.to_string(),
        "no-work stage=tdd reason=all-blocked review_waiting=0 tdd_waiting=0"
    );
    assert!(!none.has_work());
}

#[test]
fn a_bad_agent_is_an_error() {
    let w = World::new();
    assert!(check(w.services(), "Bad Agent", None, &[], &[]).is_err());
}

#[test]
fn the_plan_reads_each_kind_of_issue() {
    let l = |names: &[&str]| names.iter().map(|n| n.to_string()).collect::<Vec<_>>();
    assert_eq!(
        plan_attention(&l(&["stage:done"]), &[]),
        Some("done-still-open")
    );
    assert_eq!(
        plan_attention(&l(&["stage:spec", "blocked"]), &[]),
        Some("blocked-answerable")
    );
    assert_eq!(plan_attention(&l(&["stage:spec", "stage:tdd"]), &[]), None);
    assert_eq!(plan_attention(&l(&["stage:spec"]), &[]), None);
    for (stage, outcome, label) in [
        (Stage::Tdd, Outcome::Respec, "stage:tdd"),
        (Stage::Review, Outcome::Passed, "stage:review"),
        (Stage::Review, Outcome::Failed, "stage:review"),
    ] {
        let c = Comment {
            id: 1,
            body: signal(stage, outcome),
            created_at: "2026-01-01T00:00:00Z".into(),
        };
        assert_eq!(plan_attention(&l(&[label]), &[c]), Some("label-move"));
    }
}
