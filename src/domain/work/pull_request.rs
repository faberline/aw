#[derive(Clone, Debug, PartialEq, Eq)]
pub struct PullRequest {
    pub number: u64,
    pub head: String,
    pub body: String,
    pub url: String,
}

/// The branch an agent uses for the tdd stage of an issue.
pub fn work_branch(agent: &str, issue: u64) -> String {
    format!("{agent}/issue-{issue}")
}

fn closes_line(issue: u64) -> String {
    format!("Closes #{issue}")
}

impl PullRequest {
    /// The first non-empty line of the body is `Closes #<issue>`.
    pub fn closes(&self, issue: u64) -> bool {
        self.body
            .lines()
            .find(|l| !l.trim().is_empty())
            .is_some_and(|l| l.trim().eq_ignore_ascii_case(&closes_line(issue)))
    }

    pub fn is_for(&self, issue: u64) -> bool {
        self.head.ends_with(&format!("/issue-{issue}")) || self.closes(issue)
    }
}

pub fn find_for_issue(prs: &[PullRequest], issue: u64) -> Option<&PullRequest> {
    prs.iter().find(|p| p.is_for(issue))
}

/// A line that only links an issue, such as `Closes #7` or `fixes #7`.
/// CI accepts exactly one `Closes #<issue>` line and no other closing
/// keyword, so `aw` writes that line itself and drops these.
fn is_closing_keyword_line(line: &str) -> bool {
    let Some((word, number)) = line.trim().split_once(char::is_whitespace) else {
        return false;
    };
    let word = word.trim_end_matches(':').to_ascii_lowercase();
    let keyword = matches!(
        word.as_str(),
        "close"
            | "closes"
            | "closed"
            | "fix"
            | "fixes"
            | "fixed"
            | "resolve"
            | "resolves"
            | "resolved"
    );
    let number = number.trim();
    keyword
        && number
            .strip_prefix('#')
            .is_some_and(|n| !n.is_empty() && n.bytes().all(|b| b.is_ascii_digit()))
}

/// A pull request body whose first line is `Closes #<issue>`, and the only
/// line that links an issue.
pub fn body_closing(issue: u64, text: &str) -> String {
    let line = closes_line(issue);
    let kept: Vec<&str> = text
        .lines()
        .filter(|l| !is_closing_keyword_line(l))
        .collect();
    let rest = kept.join("\n");
    let rest = rest.trim();
    if rest.is_empty() {
        line
    } else {
        format!("{line}\n\n{rest}")
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn pr(head: &str, body: &str) -> PullRequest {
        PullRequest {
            number: 1,
            head: head.into(),
            body: body.into(),
            url: String::new(),
        }
    }

    #[test]
    fn matches_by_branch_or_closes_line() {
        assert!(pr("codex/issue-7", "").is_for(7));
        assert!(pr("feature", "\nCloses #7\n\nmore").is_for(7));
        assert!(!pr("codex/issue-17", "Closes #17").is_for(7));
        assert!(!pr("feature", "text\nCloses #7").is_for(7));
    }

    #[test]
    fn body_always_starts_with_closes() {
        assert_eq!(body_closing(3, ""), "Closes #3");
        assert_eq!(body_closing(3, "## Summary"), "Closes #3\n\n## Summary");
        assert_eq!(
            body_closing(3, "Closes #3\n\n## Summary"),
            "Closes #3\n\n## Summary"
        );
    }

    #[test]
    fn body_keeps_one_closing_line() {
        // A model may repeat the line at the end; CI then finds no issue.
        assert_eq!(
            body_closing(99, "Closes #99\n\n## Summary\n\ntext\n\nCloses #99\n"),
            "Closes #99\n\n## Summary\n\ntext"
        );
        assert_eq!(
            body_closing(3, "## Summary\nFixes: #3\nresolves #4\nthis fixes #3 too"),
            "Closes #3\n\n## Summary\nthis fixes #3 too"
        );
        assert!(!is_closing_keyword_line("Closes #"));
        assert!(!is_closing_keyword_line("Closes #3 and more"));
    }
}
