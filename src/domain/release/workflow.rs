//! Text readers for a GitHub workflow file. The release contract reads the
//! workflow as text, as the defer script does, so a changed indent or a lost
//! line is a finding. Each reader fails with a message, never with a guess.

use regex::Regex;

/// The reason a workflow does not match its contract.
pub type Reason = String;

fn re(pattern: &str) -> Regex {
    Regex::new(pattern).expect("a fixed pattern is valid")
}

fn indent(line: &str) -> usize {
    line.len() - line.trim_start().len()
}

/// The lines of the top-level key, up to the next top-level line.
pub fn top_block(text: &str, key: &str) -> Result<String, Reason> {
    let lines: Vec<&str> = text.lines().collect();
    let marker = format!("{key}:");
    let Some(start) = lines.iter().position(|l| *l == marker) else {
        return Err(format!("top-level key '{key}' is absent"));
    };
    let end = lines[start + 1..]
        .iter()
        .position(|l| !l.is_empty() && !l.starts_with(' ') && !l.starts_with('#'))
        .map_or(lines.len(), |i| start + 1 + i);
    Ok(lines[start..end].join("\n"))
}

/// The trigger names under `on:`, in file order.
pub fn triggers(text: &str) -> Result<Vec<String>, Reason> {
    let block = top_block(text, "on")?;
    Ok(re(r"(?m)^  ([a-z_]+):$")
        .captures_iter(&block)
        .map(|c| c[1].to_string())
        .collect())
}

/// The input names under `on:`.
pub fn inputs(text: &str) -> Result<Vec<String>, Reason> {
    let block = top_block(text, "on")?;
    let mut names: Vec<String> = re(r"(?m)^      ([a-z][a-z0-9_]*):$")
        .captures_iter(&block)
        .map(|c| c[1].to_string())
        .collect();
    names.sort();
    names.dedup();
    Ok(names)
}

/// The job ids under `jobs:`.
pub fn job_ids(text: &str) -> Result<Vec<String>, Reason> {
    let block = top_block(text, "jobs")?;
    let mut ids: Vec<String> = re(r"(?m)^  ([a-z][a-z0-9-]*):$")
        .captures_iter(&block)
        .map(|c| c[1].to_string())
        .collect();
    ids.sort();
    ids.dedup();
    Ok(ids)
}

/// The lines of one job.
pub fn job_block(text: &str, job: &str) -> Result<String, Reason> {
    let jobs = top_block(text, "jobs")?;
    let lines: Vec<&str> = jobs.lines().collect();
    let marker = format!("  {job}:");
    let Some(start) = lines.iter().position(|l| *l == marker) else {
        return Err(format!("job {job} is absent"));
    };
    let next = re(r"^  [a-z][a-z0-9-]*:$");
    let end = lines[start + 1..]
        .iter()
        .position(|l| next.is_match(l))
        .map_or(lines.len(), |i| start + 1 + i);
    Ok(lines[start..end].join("\n"))
}

/// The `run: |` bodies of the file and its one-line `run:` commands. A step
/// that starts with its `run:` key (`- run: ...`) counts; the Python script
/// misses it, so a build there passed.
pub fn run_scripts(text: &str) -> Vec<String> {
    let lines: Vec<&str> = text.lines().collect();
    let mut scripts = Vec::new();
    for (index, line) in lines.iter().enumerate() {
        let stripped = line.trim();
        let stripped = stripped.strip_prefix("- ").unwrap_or(stripped).trim_start();
        if stripped == "run: |" || stripped == "run: |-" {
            let base = indent(line);
            let body: Vec<&str> = lines[index + 1..]
                .iter()
                .take_while(|l| l.trim().is_empty() || indent(l) > base)
                .copied()
                .collect();
            scripts.push(body.join("\n"));
        } else if let Some(command) = stripped.strip_prefix("run: ") {
            scripts.push(command.to_string());
        }
    }
    scripts
}

/// The values after `uses:`.
pub fn actions(text: &str) -> Vec<String> {
    re(r"(?m)^\s*(?:-\s+)?uses:\s*(\S+)")
        .captures_iter(text)
        .map(|c| c[1].to_string())
        .collect()
}

/// Every action is pinned to a 40-hex commit. A local action (`./`) is not.
pub fn require_pinned(label: &str, text: &str) -> Result<(), Reason> {
    let sha = re(r"^[0-9a-f]{40}$");
    for value in actions(text) {
        if value.starts_with("./") {
            continue;
        }
        let pinned = value
            .rsplit_once('@')
            .is_some_and(|(_, tail)| sha.is_match(tail));
        if !pinned {
            return Err(format!(
                "{label}: action not pinned to a 40-hex commit: {value}"
            ));
        }
    }
    Ok(())
}

/// The words of a script, comments dropped. A line that does not parse as
/// shell is split on white space.
pub fn shell_tokens(script: &str) -> Vec<String> {
    let mut tokens = Vec::new();
    for raw in script.lines() {
        let line = raw.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        match shlex::split(line) {
            Some(words) => tokens.extend(words),
            None => tokens.extend(line.split_whitespace().map(String::from)),
        }
    }
    tokens
}

/// The build commands that a promotion must not run.
const BUILDS: [&[&str]; 5] = [
    &["docker", "build"],
    &["docker", "buildx", "build"],
    &["cargo", "build"],
    &["cargo", "zigbuild"],
    &["cross", "build"],
];

/// A promotion publishes the candidate. It does not build.
pub fn require_no_rebuild(label: &str, text: &str) -> Result<(), Reason> {
    for script in run_scripts(text) {
        let tokens = shell_tokens(&script);
        for start in 0..tokens.len() {
            for pattern in BUILDS {
                let end = start + pattern.len();
                if end <= tokens.len() && tokens[start..end].iter().eq(pattern.iter()) {
                    return Err(format!(
                        "{label}: promotion must not rebuild: {}",
                        pattern.join(" ")
                    ));
                }
            }
        }
    }
    Ok(())
}

/// The bracketed `needs: [a, b]` list of a job.
pub fn needs(job: &str) -> Result<Vec<String>, Reason> {
    let Some(found) = re(r"(?m)^\s*needs: \[([^\]]+)\]$").captures(job) else {
        return Err("result job must declare a bracketed needs list".into());
    };
    Ok(found[1].split(',').map(|s| s.trim().to_string()).collect())
}

/// The lines of the one step with this name inside a job block.
pub fn step_block(job: &str, step: &str) -> Result<String, Reason> {
    let lines: Vec<&str> = job.lines().collect();
    let marker = format!("- name: {step}");
    let starts: Vec<usize> = lines
        .iter()
        .enumerate()
        .filter(|(_, l)| l.trim() == marker)
        .map(|(i, _)| i)
        .collect();
    if starts.len() != 1 {
        return Err(format!(
            "step {step:?} must appear exactly once, found {}",
            starts.len()
        ));
    }
    let start = starts[0];
    let base = indent(lines[start]);
    let end = lines[start + 1..]
        .iter()
        .position(|l| !l.trim().is_empty() && indent(l) <= base && l.trim_start().starts_with("- "))
        .map_or(lines.len(), |i| start + 1 + i);
    Ok(lines[start..end].join("\n"))
}

/// The body of the one `run: |` block of a step.
pub fn run_script(step: &str) -> Result<String, Reason> {
    let lines: Vec<&str> = step.lines().collect();
    let starts: Vec<usize> = lines
        .iter()
        .enumerate()
        .filter(|(_, l)| matches!(l.trim(), "run: |" | "run: |-"))
        .map(|(i, _)| i)
        .collect();
    if starts.len() != 1 {
        return Err("step must carry exactly one block run script".into());
    }
    let base = indent(lines[starts[0]]);
    let body: Vec<&str> = lines[starts[0] + 1..]
        .iter()
        .take_while(|l| l.trim().is_empty() || indent(l) > base)
        .copied()
        .collect();
    Ok(body.join("\n"))
}

/// The non-comment lines of a script, with backslash continuations joined as
/// a shell reads them.
pub fn logical_lines(text: &str) -> Vec<String> {
    let mut out = Vec::new();
    let mut current = String::new();
    for raw in text.lines() {
        let trimmed = raw.trim();
        if trimmed.starts_with('#') {
            continue;
        }
        if let Some(head) = trimmed.strip_suffix('\\') {
            current.push_str(head.trim_end());
            current.push(' ');
            continue;
        }
        current.push_str(trimmed);
        if !current.trim().is_empty() {
            out.push(current.trim().to_string());
        }
        current.clear();
    }
    if !current.trim().is_empty() {
        out.push(current.trim().to_string());
    }
    out
}
