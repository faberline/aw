//! The patrol report: sections of checklist items, and the text forms.

use serde::Serialize;

/// One thing the desk must look at.
#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct Item {
    pub check: String,
    pub what: String,
    pub why: String,
}

/// The counts line of a repository section.
#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize)]
pub struct Counts {
    pub open: usize,
    pub spec: usize,
    pub tdd: usize,
    pub review: usize,
    pub blocked: usize,
    pub blocked_reasons: BlockedReasons,
    pub open_prs: usize,
    pub last_merge: Option<String>,
}

/// How the blocked issues of a repository say what they wait for.
#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize)]
pub struct BlockedReasons {
    /// A `Waits for:` line, or a comment that names the blockers.
    pub waits: usize,
    /// A comment that names a decision, an owner or a wait.
    pub comment: usize,
    /// A hold label (`decision`, paid, owner).
    pub label: usize,
    pub none: usize,
}

/// One repository (or the aw section).
#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct Section {
    pub name: String,
    pub counts: Option<Counts>,
    /// Set when the repository could not be read: the failed call.
    pub unknown: Option<String>,
    pub items: Vec<Item>,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct Report {
    pub sections: Vec<Section>,
    pub summary: String,
}

impl Report {
    pub fn new(sections: Vec<Section>) -> Self {
        let summary = summarize(&sections);
        Report { sections, summary }
    }

    /// The report with only the items of the named checks.
    pub fn only(mut self, checks: &[String]) -> Self {
        if checks.is_empty() {
            return self;
        }
        for s in &mut self.sections {
            s.items.retain(|i| checks.contains(&i.check));
        }
        Report::new(self.sections)
    }

    /// True when a repository could not be read.
    pub fn has_unknown(&self) -> bool {
        self.sections.iter().any(|s| s.unknown.is_some())
    }

    pub fn markdown(&self) -> String {
        let mut out = format!("{}\n\n", self.summary);
        for s in &self.sections {
            out.push_str(&format!("## {}\n\n", s.name));
            if let Some(c) = &s.counts {
                let merge = c.last_merge.as_deref().unwrap_or("none");
                let why = if c.blocked > 0 {
                    let r = &c.blocked_reasons;
                    format!(
                        " (waits-for {}, comment {}, label {}, no reason {})",
                        r.waits, r.comment, r.label, r.none
                    )
                } else {
                    String::new()
                };
                out.push_str(&format!(
                    "open {}, spec {}, tdd {}, review {}, blocked {}{}, open PRs {}, last merge {}\n\n",
                    c.open, c.spec, c.tdd, c.review, c.blocked, why, c.open_prs, merge
                ));
            }
            if let Some(call) = &s.unknown {
                out.push_str(&format!("- [ ] unknown: cannot read {call}\n"));
            }
            for i in &s.items {
                out.push_str(&format!("- [ ] {}: {} ({})\n", i.what, i.why, i.check));
            }
            if s.unknown.is_none() && s.items.is_empty() {
                out.push_str("nothing to look at\n");
            }
            out.push('\n');
        }
        out
    }

    pub fn json(&self) -> String {
        serde_json::to_string_pretty(self).expect("a report is valid JSON")
    }
}

fn summarize(sections: &[Section]) -> String {
    let mut counts: Vec<(String, usize)> = Vec::new();
    let mut unknown = 0;
    for s in sections {
        if s.unknown.is_some() {
            unknown += 1;
        }
        for i in &s.items {
            match counts.iter_mut().find(|(c, _)| *c == i.check) {
                Some((_, n)) => *n += 1,
                None => counts.push((i.check.clone(), 1)),
            }
        }
    }
    let mut parts: Vec<String> = counts.iter().map(|(c, n)| format!("{c} {n}")).collect();
    if unknown > 0 {
        parts.push(format!("unknown {unknown}"));
    }
    if parts.is_empty() {
        "nothing to look at".to_string()
    } else {
        format!("To look at: {}", parts.join(", "))
    }
}
