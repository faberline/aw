//! Line-based Markdown helpers. They keep line numbers stable: a blanked
//! line stays in place.

use std::sync::LazyLock;

use regex::Regex;

fn re(pattern: &str) -> Regex {
    Regex::new(pattern).expect("fixed pattern")
}

static LINK: LazyLock<Regex> = LazyLock::new(|| re(r"\[[^\]]*\]\(\s*([^\s)]+)"));
static SCHEME: LazyLock<Regex> = LazyLock::new(|| re(r"^[A-Za-z][A-Za-z0-9+.-]*:"));
static SEPARATOR_CELL: LazyLock<Regex> = LazyLock::new(|| re(r"^:?-{3,}:?$"));
static NESTED_ITEM: LazyLock<Regex> = LazyLock::new(|| re(r"^\s{2,}-\s+"));
static INDENTED: LazyLock<Regex> = LazyLock::new(|| re(r"^\s{4,}\S"));

/// The first three characters after the leading white space.
fn marker(line: &str) -> String {
    line.trim_start().chars().take(3).collect()
}

fn is_fence(marker: &str) -> bool {
    marker == "```" || marker == "~~~"
}

/// Blank fenced examples; line numbers do not move.
pub fn without_fenced_content(lines: &[&str]) -> Vec<String> {
    let mut visible = Vec::with_capacity(lines.len());
    let mut fence = String::new();
    for line in lines {
        let m = marker(line);
        if is_fence(&m) {
            if fence.is_empty() {
                fence = m;
            } else if m == fence {
                fence.clear();
            }
            visible.push(String::new());
            continue;
        }
        visible.push(if fence.is_empty() {
            (*line).to_string()
        } else {
            String::new()
        });
    }
    visible
}

/// The index of the next heading of this level, or the end of the lines.
pub fn section_end(lines: &[String], start: usize, level: usize) -> usize {
    let prefix = format!("{} ", "#".repeat(level));
    (start + 1..lines.len())
        .find(|&i| lines[i].starts_with(&prefix))
        .unwrap_or(lines.len())
}

/// The headings of one level: line index and trimmed title.
pub fn headings(lines: &[String], level: usize) -> Vec<(usize, String)> {
    let prefix = format!("{} ", "#".repeat(level));
    lines
        .iter()
        .enumerate()
        .filter(|(_, l)| l.starts_with(&prefix))
        .map(|(i, l)| (i, l[prefix.len()..].trim().to_string()))
        .collect()
}

pub fn parse_table_row(line: &str) -> Vec<String> {
    line.trim()
        .trim_matches('|')
        .split('|')
        .map(|c| c.trim().to_string())
        .collect()
}

pub fn is_separator_row(cells: &[String]) -> bool {
    !cells.is_empty() && cells.iter().all(|c| SEPARATOR_CELL.is_match(c))
}

/// The link targets of one line, in order.
pub fn link_targets(line: &str) -> Vec<String> {
    LINK.captures_iter(line).map(|c| c[1].to_string()).collect()
}

pub fn has_link(text: &str) -> bool {
    LINK.is_match(text)
}

pub fn has_scheme(target: &str) -> bool {
    SCHEME.is_match(target)
}

/// Replace every code span with spaces of the same length.
pub fn blank_code_spans(line: &str) -> String {
    let ranges = code_span_ranges(line);
    let mut out = String::new();
    for (i, c) in line.chars().enumerate() {
        out.push(if ranges.iter().any(|&(s, l)| i >= s && i < s + l) {
            ' '
        } else {
            c
        });
    }
    out
}

/// The code spans of one line as `(start, length)` in characters.
pub fn code_span_ranges(line: &str) -> Vec<(usize, usize)> {
    let chars: Vec<char> = line.chars().collect();
    let mut out = Vec::new();
    let mut i = 0;
    while i < chars.len() {
        if chars[i] != '`' {
            i += 1;
            continue;
        }
        let mut run = 0;
        while i + run < chars.len() && chars[i + run] == '`' {
            run += 1;
        }
        // The opening is the longest run that has a closing run of the
        // same length; a shorter opening leaves a backtick in the content.
        let span = (1..=run).rev().find_map(|open| {
            let from = i + open;
            (from..(chars.len() + 1).saturating_sub(open))
                .find(|&j| chars[j..j + open].iter().all(|&c| c == '`'))
                .map(|j| j + open - i)
        });
        match span {
            Some(len) => {
                out.push((i, len));
                i += len;
            }
            None => i += 1,
        }
    }
    out
}

/// The text of each code span with its backticks removed.
pub fn code_spans(line: &str) -> Vec<String> {
    let chars: Vec<char> = line.chars().collect();
    code_span_ranges(line)
        .into_iter()
        .map(|(s, l)| {
            let span: String = chars[s..s + l].iter().collect();
            span.trim_matches('`').to_string()
        })
        .collect()
}

/// The text of a link or a code span without its markup.
pub fn strip_inline(text: &str) -> String {
    static LINK_FORM: LazyLock<Regex> = LazyLock::new(|| re(r"\[([^\]]+)\]\([^)]+\)"));
    static CODE: LazyLock<Regex> = LazyLock::new(|| re(r"`([^`]+)`"));
    let plain = LINK_FORM.replace_all(text, "$1");
    CODE.replace_all(&plain, "$1").into_owned()
}

/// The GitHub anchor of a heading.
pub fn github_slug(text: &str) -> String {
    static DROP: LazyLock<Regex> = LazyLock::new(|| re(r"[^a-z0-9 _-]"));
    static SEPARATORS: LazyLock<Regex> = LazyLock::new(|| re(r"[ _]+"));
    static DASHES: LazyLock<Regex> = LazyLock::new(|| re(r"-+"));
    let lower = strip_inline(text).to_lowercase();
    let kept = DROP.replace_all(lower.trim(), "");
    let joined = SEPARATORS.replace_all(&kept, "-");
    DASHES
        .replace_all(&joined, "-")
        .trim_matches('-')
        .to_string()
}

/// The link targets of the prose lines: no fenced block, no code span.
/// Each is paired with its line index.
pub fn markdown_links(lines: &[&str]) -> Vec<(usize, String)> {
    let mut links = Vec::new();
    let mut fence = String::new();
    for (index, line) in lines.iter().enumerate() {
        let m = marker(line);
        if is_fence(&m) {
            if fence.is_empty() {
                fence = m;
            } else if m == fence {
                fence.clear();
            }
            continue;
        }
        if !fence.is_empty() {
            continue;
        }
        links.extend(
            link_targets(&blank_code_spans(line))
                .into_iter()
                .map(|t| (index, t)),
        );
    }
    links
}

/// Whether the lines leave a fenced block open at the end.
pub fn fence_left_open(lines: &[&str]) -> bool {
    let mut fence = String::new();
    for line in lines {
        let m = marker(line);
        if is_fence(&m) {
            if fence.is_empty() {
                fence = m;
            } else if m == fence {
                fence.clear();
            }
        }
    }
    !fence.is_empty()
}

/// The nested list items of a range, each as its first line index and its
/// text joined with continuation lines.
pub fn source_blocks(lines: &[String], start: usize, end: usize) -> Vec<(usize, String)> {
    let mut blocks: Vec<(usize, Vec<String>)> = Vec::new();
    let mut current: Option<(usize, Vec<String>)> = None;
    for (index, line) in lines.iter().enumerate().take(end).skip(start) {
        if NESTED_ITEM.is_match(line) {
            if let Some(block) = current.take() {
                blocks.push(block);
            }
            current = Some((index, vec![line.trim().to_string()]));
            continue;
        }
        if let Some((_, parts)) = current.as_mut()
            && (line.trim().is_empty() || INDENTED.is_match(line))
        {
            if !line.trim().is_empty() {
                parts.push(line.trim().to_string());
            }
            continue;
        }
        if let Some(block) = current.take() {
            blocks.push(block);
        }
    }
    if let Some(block) = current {
        blocks.push(block);
    }
    blocks
        .into_iter()
        .map(|(i, parts)| (i, parts.join(" ")))
        .collect()
}
