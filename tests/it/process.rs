use aw::infrastructure::work::process::{run, succeeds};

#[test]
fn run_returns_stdout_and_passes_stdin() {
    assert_eq!(run("sh", &["-c", "printf hello"], None).unwrap(), "hello");
    assert_eq!(run("cat", &[], Some("from stdin")).unwrap(), "from stdin");
}

#[test]
fn a_non_zero_exit_is_an_error_with_stderr() {
    let err = run("sh", &["-c", "echo broken >&2; exit 3"], None).unwrap_err();
    let text = err.to_string();
    assert!(text.contains("`sh -c` exited with"), "{text}");
    assert!(text.contains("broken"), "{text}");
}

#[test]
fn a_program_that_does_not_exist_is_an_error() {
    let err = run("aw-no-such-program", &[], None).unwrap_err();
    assert!(err.to_string().contains("could not start"));
    let err = succeeds("aw-no-such-program", &[]).unwrap_err();
    assert!(err.to_string().contains("could not start"));
}

#[test]
fn succeeds_reports_the_exit_status() {
    assert!(succeeds("sh", &["-c", "exit 0"]).unwrap());
    assert!(!succeeds("sh", &["-c", "exit 1"]).unwrap());
}
