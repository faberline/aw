//! The `Waits for:` line of an issue body: the issues that must close first.

use regex::Regex;

/// One prerequisite: `#N` (the same repository) or `owner/repo#N`.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Prerequisite {
    /// `None` for the repository of the issue.
    pub repo: Option<String>,
    pub number: u64,
}

impl Prerequisite {
    pub fn show(&self) -> String {
        match &self.repo {
            Some(r) => format!("{r}#{}", self.number),
            None => format!("#{}", self.number),
        }
    }
}

/// What the `Waits for:` lines of a body say.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Waits {
    pub prerequisites: Vec<Prerequisite>,
    /// The `Waits for:` lines that name no issue number.
    pub without_number: Vec<String>,
}

/// Read every line that starts with `Waits for:` (case-insensitive,
/// leading spaces, list marker or quote mark allowed).
pub fn parse(body: &str) -> Waits {
    let reference = Regex::new(r"(?:([A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9._-]+))?#(\d+)")
        .expect("the reference pattern is valid");
    let mut waits = Waits::default();
    for line in body.lines() {
        let line = line.trim_start_matches(|c: char| c.is_whitespace() || "-*>".contains(c));
        let Some(head) = line.get(..10) else { continue };
        if !head.eq_ignore_ascii_case("waits for:") {
            continue;
        }
        let rest = line[10..].trim();
        let mut found = false;
        for cap in reference.captures_iter(rest) {
            let Ok(number) = cap[2].parse::<u64>() else {
                continue;
            };
            found = true;
            let p = Prerequisite {
                repo: cap.get(1).map(|m| m.as_str().to_string()),
                number,
            };
            if !waits.prerequisites.contains(&p) {
                waits.prerequisites.push(p);
            }
        }
        if !found {
            waits.without_number.push(rest.to_string());
        }
    }
    waits
}

#[cfg(test)]
mod tests {
    use super::*;

    fn p(repo: Option<&str>, number: u64) -> Prerequisite {
        Prerequisite {
            repo: repo.map(String::from),
            number,
        }
    }

    #[test]
    fn reads_local_and_cross_repo_numbers() {
        let w = parse("Text\nWaits for: #12, faberline/core#7 and #12\nMore");
        assert_eq!(w.prerequisites, [p(None, 12), p(Some("faberline/core"), 7)]);
        assert!(w.without_number.is_empty());
    }

    #[test]
    fn a_line_without_a_number_is_listed() {
        let w = parse("- waits for: the port adapter\nWaits for: #3");
        assert_eq!(w.prerequisites, [p(None, 3)]);
        assert_eq!(w.without_number, ["the port adapter"]);
    }

    #[test]
    fn other_text_is_not_a_waits_line() {
        let w = parse("The adapter waits for #5 to merge.\nWaits for the port.");
        assert_eq!(w, Waits::default());
    }
}
