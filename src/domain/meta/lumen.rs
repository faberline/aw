//! P7: exact positive claims that would reverse the Lumen contract. The list
//! is narrow on purpose. A correct negative statement must not match.

use std::sync::LazyLock;

use regex::Regex;

use super::markdown::{strip_inline, without_fenced_content};
use super::report::Finding;

const PATTERNS: [&str; 13] = [
    r"\bFleet (?:is|equals|enables|provides) (?:HA|autoscaling)\b",
    r"\b(?:one|1) Pod (?:is|provides) HA\b",
    r"\b2 voters? (?:is|are|provides?) production HA\b",
    r"\bZonal acceptance proves regional HA\b",
    r"\bPDB (?:limits|gates|protects) StatefulSet rolling updates?\b",
    r"\bGCE machine types? belong(?:s)? in the Kubernetes-native CRD\b",
    r"\bOperator creates? (?:the )?(?:Kubernetes cluster|namespace|KSA|client Deployment)\b",
    r"\bBinding a KSA automatically adds? (?:an )?Authorization header\b",
    r"\bGenerated clients support token rotation and safe retry today\b",
    r"\bClient-side validation replaces TokenReview\b",
    r"\bThe current runtime Kustomize template is Managed\b",
    r"\bLumen (?:executes|runs|generates) embedding models?\b",
    r"\bGenerated SDK packages? (?:are|is) published\b",
];

static FORBIDDEN: LazyLock<Vec<Regex>> = LazyLock::new(|| {
    PATTERNS
        .iter()
        .map(|p| Regex::new(&format!("(?i){p}")).expect("fixed pattern"))
        .collect()
});

/// Whether the project folder is `apps/lumen`.
pub fn is_lumen(dir: &str) -> bool {
    dir == "apps/lumen" || dir.ends_with("/apps/lumen")
}

/// The forbidden claims in one document. `name` is the file name.
pub fn check(name: &str, text: &str) -> Vec<Finding> {
    let lines: Vec<&str> = text.lines().collect();
    let prose = without_fenced_content(&lines);
    let mut findings = Vec::new();
    for (index, line) in prose.iter().enumerate() {
        let plain = strip_inline(line);
        for pattern in FORBIDDEN.iter() {
            if pattern.is_match(&plain) {
                findings.push(Finding::new(
                    "P7",
                    index + 1,
                    format!(
                        "forbidden Lumen contract assertion in {name}: {}",
                        line.trim()
                    ),
                ));
            }
        }
    }
    findings
}
