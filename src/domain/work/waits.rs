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
        // `Part of #7. Waits for: #9` holds a waits text after a sentence end
        // (faberline/aw#601). It counts. Safer than a warning: an early claim
        // wastes a whole tdd run, a hold that is not meant shows on the board.
        let line = match after_sentence(line) {
            Some(at) => &line[at..],
            None => line,
        };
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

/// The same-repository issue numbers on the first line of a signal message,
/// when that line is `Waits for: #n[, #m]` and names nothing else. A line
/// with another repository or with text and no number gives `None`: that case
/// stays with the desk.
pub fn signal_numbers(message: &str) -> Option<Vec<u64>> {
    let line = message.lines().map(str::trim).find(|l| !l.is_empty())?;
    let head = line.get(..10)?;
    if !head.eq_ignore_ascii_case("waits for:") {
        return None;
    }
    let waits = parse(line);
    if waits.prerequisites.is_empty()
        || !waits.without_number.is_empty()
        || waits.prerequisites.iter().any(|p| p.repo.is_some())
    {
        return None;
    }
    Some(waits.prerequisites.iter().map(|p| p.number).collect())
}

/// The body with its `Waits for:` lines set to `numbers`. The first old line
/// becomes the new line, the other old lines go, and a reference to another
/// repository on an old line stays. A body with no such line gets one at the
/// end.
pub fn set_waits(body: &str, numbers: &[u64]) -> String {
    let mut line_numbers: Vec<String> = numbers.iter().map(|n| format!("#{n}")).collect();
    let mut out: Vec<String> = Vec::new();
    let mut at: Option<usize> = None;
    for line in body.lines().flat_map(super::split::own_line_waits) {
        let bare = line.trim_start_matches(|c: char| c.is_whitespace() || "-*>".contains(c));
        let is_waits = bare
            .get(..10)
            .is_some_and(|h| h.eq_ignore_ascii_case("waits for:"));
        if !is_waits {
            out.push(line);
            continue;
        }
        for p in parse(bare).prerequisites {
            if p.repo.is_some() && !line_numbers.contains(&p.show()) {
                line_numbers.push(p.show());
            }
        }
        if at.is_none() {
            at = Some(out.len());
            out.push(String::new());
        }
    }
    let new = format!("Waits for: {}", line_numbers.join(", "));
    match at {
        Some(i) => out[i] = new,
        None => {
            out.push(String::new());
            out.push(new);
        }
    }
    let mut text = out.join("\n");
    if body.ends_with('\n') {
        text.push('\n');
    }
    text
}

/// The byte index of a `Waits for:` that follows the end of a sentence in
/// the same line (`.`, `;`, `!` or `?`, then a space).
fn after_sentence(line: &str) -> Option<usize> {
    let lower = line.to_ascii_lowercase();
    let at = lower.find("waits for:")?;
    let before = line[..at].trim_end();
    (at > 0 && line[..at].ends_with(' ') && before.ends_with(['.', ';', '!', '?'])).then_some(at)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_waits_text_after_a_sentence_is_read() {
        let w = parse("Part of #161. Waits for: #182\nWaits for: #185");
        assert_eq!(w.prerequisites, [p(None, 182), p(None, 185)]);
    }

    #[test]
    fn a_waits_text_inside_a_sentence_is_not_read() {
        let w = parse("The port waits for: #5 to merge.\nIt is done;waits for: #6");
        assert_eq!(w, Waits::default());
    }

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

    #[test]
    fn a_signal_line_gives_same_repo_numbers_only() {
        assert_eq!(signal_numbers("Waits for: #300\nmore"), Some(vec![300]));
        assert_eq!(signal_numbers("\nwaits for: #3, #4."), Some(vec![3, 4]));
        assert_eq!(signal_numbers("Waits for: faberline/core#7"), None);
        assert_eq!(signal_numbers("Waits for: #3, faberline/core#7"), None);
        assert_eq!(signal_numbers("Waits for: the port"), None);
        assert_eq!(signal_numbers("Blocked.\nWaits for: #3"), None);
        assert_eq!(signal_numbers(""), None);
    }

    #[test]
    fn set_waits_replaces_adds_and_keeps_other_repos() {
        assert_eq!(
            set_waits("Text\nWaits for: #294\nEnd", &[300]),
            "Text\nWaits for: #300\nEnd"
        );
        assert_eq!(set_waits("Text\n", &[300]), "Text\n\nWaits for: #300\n");
        assert_eq!(set_waits("", &[3, 4]), "\nWaits for: #3, #4");
        assert_eq!(
            set_waits(
                "A\nWaits for: #1\nB\n- waits for: faberline/core#7, #2",
                &[9]
            ),
            "A\nWaits for: #9, faberline/core#7\nB"
        );
        assert_eq!(
            set_waits("Part of #7. Waits for: #9", &[9]),
            "Part of #7.\nWaits for: #9"
        );
    }
}
