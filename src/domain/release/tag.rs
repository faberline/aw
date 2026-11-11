//! Release tag names, tag patterns of a workflow and `Closes #n` lines.

use super::require;
use crate::domain::work::ports::PortResult;

/// `MAJOR.MINOR.PATCH`, each a number with no leading zero.
pub fn is_version(text: &str) -> bool {
    let parts: Vec<&str> = text.split('.').collect();
    parts.len() == 3 && parts.iter().all(|p| is_number(p, true))
}

fn is_number(text: &str, zero: bool) -> bool {
    !text.is_empty()
        && text.bytes().all(|b| b.is_ascii_digit())
        && ((text == "0" && zero) || !text.starts_with('0'))
}

/// The `N` of `<final>-rc.N`, when `name` is a candidate of `final`.
pub fn rc_number(final_tag: &str, name: &str) -> Option<u64> {
    let n = name.strip_prefix(final_tag)?.strip_prefix("-rc.")?;
    if is_number(n, false) {
        n.parse().ok()
    } else {
        None
    }
}

/// The version of `vX.Y.Z` or `vX.Y.Z-rc.N`; `None` for any other name.
pub fn release_version(tag: &str) -> Option<&str> {
    let rest = tag.strip_prefix('v')?;
    let version = rest.split("-rc.").next()?;
    let final_tag = &tag[..version.len() + 1];
    (is_version(version) && (rest == version || rc_number(final_tag, tag).is_some()))
        .then_some(version)
}

/// Percent-encode everything except unreserved characters.
pub fn encode(text: &str) -> String {
    text.bytes()
        .map(|b| match b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'.' | b'_' | b'~' => {
                (b as char).to_string()
            }
            other => format!("%{other:02X}"),
        })
        .collect()
}

/// The issue numbers of the `Closes #n`, `Fixes #n` and `Resolves #n`
/// lines of a pull request body. The keyword is the first word of its line,
/// `#n` is the next word and the last word of its line. As in the old
/// tool, `#n` can be on a later line.
pub fn closes(body: &str) -> Vec<u64> {
    // Each word, with: is it the first, is it the last word of its line.
    let mut words: Vec<(&str, bool, bool)> = Vec::new();
    for line in body.split('\n') {
        let line: Vec<&str> = line.split_whitespace().collect();
        let last = line.len().saturating_sub(1);
        words.extend(
            line.iter()
                .enumerate()
                .map(|(i, w)| (*w, i == 0, i == last)),
        );
    }
    words
        .windows(2)
        .filter_map(|pair| {
            let (keyword, first, _) = pair[0];
            let (reference, _, last) = pair[1];
            let number = reference.strip_prefix('#')?;
            let keyword = keyword.to_ascii_lowercase();
            (first
                && last
                && matches!(keyword.as_str(), "closes" | "fixes" | "resolves")
                && is_number(number, false))
            .then(|| number.parse().ok())
            .flatten()
        })
        .collect()
}

/// The `on.push` trigger of a workflow file.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct PushTrigger {
    /// Every key of `on.push`.
    pub keys: Vec<String>,
    /// `on.push.tags`, when it is a list of text patterns.
    pub tags: Option<Vec<String>>,
}

/// The keys of `on.push` that do not stop a tag run.
const PUSH_KEYS: [&str; 3] = ["tags", "branches", "branches-ignore"];

/// Stop when a push of `tag` does not surely start the workflow: no tag
/// trigger, a path or tag-ignore filter, an unknown key, a `!` pattern, or
/// no pattern that accepts the tag.
pub fn accepts_tag(trigger: Option<&PushTrigger>, tag: &str) -> PortResult<()> {
    let tags = trigger.and_then(|t| t.tags.as_deref()).unwrap_or_default();
    require(
        !tags.is_empty()
            && trigger.is_some_and(|t| t.keys.iter().all(|k| PUSH_KEYS.contains(&k.as_str()))),
        "full tests need an unfiltered tag trigger",
    )?;
    require(
        tags.iter().all(|p| !p.starts_with('!')) && tags.iter().any(|p| accepts(p, tag)),
        "tag is not accepted by full tests",
    )
}

/// Does the tag filter `pattern` of a workflow accept `tag`? `*` and `**`
/// match any text, `[...]` matches one character. A pattern with `?`, `+`
/// or `\` is never accepted: the tool does not guess their meaning.
pub fn accepts(pattern: &str, tag: &str) -> bool {
    if pattern.contains(['?', '+', '\\']) {
        return false;
    }
    let pattern: Vec<char> = pattern.chars().collect();
    let tag: Vec<char> = tag.chars().collect();
    glob(&pattern, &tag)
}

fn glob(pattern: &[char], text: &[char]) -> bool {
    match pattern.first() {
        None => text.is_empty(),
        Some('*') => (0..=text.len()).any(|skip| glob(&pattern[1..], &text[skip..])),
        Some('[') => match pattern.iter().position(|c| *c == ']') {
            Some(end) if end > 1 => {
                !text.is_empty()
                    && class(&pattern[1..end], text[0])
                    && glob(&pattern[end + 1..], &text[1..])
            }
            _ => false,
        },
        Some(c) => text.first() == Some(c) && glob(&pattern[1..], &text[1..]),
    }
}

fn class(set: &[char], c: char) -> bool {
    let mut i = 0;
    while i < set.len() {
        if i + 2 < set.len() && set[i + 1] == '-' {
            if set[i] <= c && c <= set[i + 2] {
                return true;
            }
            i += 3;
        } else {
            if set[i] == c {
                return true;
            }
            i += 1;
        }
    }
    false
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn versions_and_tags() {
        assert!(is_version("0.5.0"));
        assert!(is_version("10.0.12"));
        for bad in ["0.5", "0.5.0.1", "01.5.0", "0.5.x", "", "0..1", "v0.5.0"] {
            assert!(!is_version(bad), "{bad}");
        }
        assert_eq!(rc_number("v0.5.0", "v0.5.0-rc.12"), Some(12));
        for bad in [
            "v0.5.0",
            "v0.5.0-rc.0",
            "v0.5.0-rc.01",
            "v0.5.0-rc.x",
            "v0.5.1-rc.1",
        ] {
            assert_eq!(rc_number("v0.5.0", bad), None, "{bad}");
        }
        assert_eq!(release_version("v0.5.0"), Some("0.5.0"));
        assert_eq!(release_version("v0.5.0-rc.3"), Some("0.5.0"));
        for bad in [
            "0.5.0",
            "v0.5",
            "v0.5.0-rc.0",
            "v0.5.0-beta",
            "v0.5.0-rc.1-rc.2",
            "v",
        ] {
            assert_eq!(release_version(bad), None, "{bad}");
        }
    }

    #[test]
    fn encode_keeps_only_unreserved() {
        assert_eq!(encode("v0.5.0-rc.1"), "v0.5.0-rc.1");
        assert_eq!(encode("a/b c"), "a%2Fb%20c");
    }

    #[test]
    fn closes_reads_whole_lines() {
        let body = "Text\nCloses #12\n  fixes   #7  \nRESOLVES #3\nCloses #0\nCloses #4 and more\nsee closes #9\nCloses 5\nCloses #05";
        assert_eq!(closes(body), [12, 7, 3]);
        assert_eq!(
            closes("Closes\n\n  #8\nFixes\n#9 now\nx Resolves\n#10"),
            [8]
        );
        assert!(closes("").is_empty());
    }

    #[test]
    fn a_tag_trigger_must_be_unfiltered() {
        let trigger = |keys: &[&str], tags: Option<&[&str]>| PushTrigger {
            keys: keys.iter().map(|k| k.to_string()).collect(),
            tags: tags.map(|t| t.iter().map(|p| p.to_string()).collect()),
        };
        let check = |t: &PushTrigger| accepts_tag(Some(t), "v0.5.0-rc.1").map_err(|e| e.0);
        assert!(check(&trigger(&["tags", "branches"], Some(&["v*"]))).is_ok());
        let none = accepts_tag(None, "v0.5.0").unwrap_err().0;
        assert!(none.contains("unfiltered tag trigger"));
        assert!(
            check(&trigger(&["branches"], None))
                .unwrap_err()
                .contains("unfiltered")
        );
        assert!(
            check(&trigger(&["tags"], Some(&[])))
                .unwrap_err()
                .contains("unfiltered")
        );
        for key in ["paths", "paths-ignore", "tags-ignore", "<<"] {
            let refused = check(&trigger(&["tags", key], Some(&["v*"]))).unwrap_err();
            assert!(refused.contains("unfiltered"), "{key}");
        }
        let negative = check(&trigger(&["tags"], Some(&["v*", "!v0*"]))).unwrap_err();
        assert!(negative.contains("not accepted"));
        let other = check(&trigger(&["tags"], Some(&["v1.*"]))).unwrap_err();
        assert!(other.contains("not accepted"));
    }

    #[test]
    fn tag_patterns() {
        assert!(accepts("v*", "v0.5.0-rc.1"));
        assert!(accepts("v*.*.*", "v0.5.0"));
        assert!(accepts("**", "v0.5.0"));
        assert!(accepts("v[0-9]*.[0-9]*.[0-9]*", "v0.5.0-rc.1"));
        assert!(accepts("v0.5.0", "v0.5.0"));
        assert!(accepts("v[01x].5.0", "v1.5.0"));
        assert!(!accepts("v[0-9].[0-9].[0-9]", "v0.5.0-rc.1"));
        assert!(!accepts("release-*", "v0.5.0"));
        assert!(!accepts("v0.5.0", "v0.5.0-rc.1"));
        assert!(!accepts("v[0-9", "v0"));
        assert!(!accepts("v[]", "v0"));
        assert!(!accepts("v[2-9]*", "v0.5.0"));
        for unknown in ["v?.5.0", "v0+", "v\\*"] {
            assert!(!accepts(unknown, "v0.5.0"), "{unknown}");
        }
    }
}
