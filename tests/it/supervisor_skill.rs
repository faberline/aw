//! The shape of the `aw-supervisor` skill: the rule groups that moved to
//! separate files are all there, and each rule number is defined once.

use std::collections::BTreeMap;
use std::path::PathBuf;

fn dir() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("plugins/faberline/skills/aw-supervisor")
}

fn read(name: &str) -> String {
    std::fs::read_to_string(dir().join(name)).unwrap_or_else(|e| panic!("{name}: {e}"))
}

fn special_files() -> Vec<String> {
    let mut names: Vec<String> = std::fs::read_dir(dir())
        .unwrap()
        .filter_map(|e| e.ok())
        .map(|e| e.file_name().to_string_lossy().to_string())
        .filter(|n| n.starts_with("special-") && n.ends_with(".md"))
        .collect();
    names.sort();
    names
}

#[test]
fn every_separate_file_is_named_in_the_main_file_and_exists() {
    let main = read("SKILL.md");
    let files = special_files();
    assert_eq!(files.len(), 4, "{files:?}");
    for name in &files {
        assert!(main.contains(&format!("`{name}`")), "{name} is not named");
    }
    // Each name in the main file is a file that exists.
    for word in main.split('`') {
        if word.starts_with("special-") && word.ends_with(".md") {
            assert!(files.iter().any(|f| f == word), "{word} does not exist");
        }
    }
}

#[test]
fn each_rule_number_is_defined_once() {
    let mut found: BTreeMap<u32, Vec<String>> = BTreeMap::new();
    let mut names = vec!["SKILL.md".to_string()];
    names.extend(special_files());
    for name in &names {
        for line in read(name).lines() {
            let Some(rest) = line.strip_prefix('#') else {
                continue;
            };
            let heading = rest.trim_start_matches('#').trim();
            let Some(tail) = heading.strip_suffix(')') else {
                continue;
            };
            let Some((_, id)) = tail.rsplit_once("(G") else {
                continue;
            };
            if let Ok(n) = id.parse::<u32>() {
                found.entry(n).or_default().push(name.clone());
            }
        }
    }
    for (n, at) in &found {
        assert_eq!(at.len(), 1, "G{n} is defined in {at:?}");
    }
    let all: Vec<u32> = found.keys().copied().collect();
    assert_eq!(all, (1..=64).collect::<Vec<u32>>());
}

#[test]
fn the_quick_exit_comes_first_and_names_the_command() {
    let main = read("SKILL.md");
    let quick = main.find("## Quick exit").expect("a Quick exit section");
    assert!(quick < main.find("## Write authorization").unwrap());
    assert!(quick < main.find("### 1. Check access").unwrap());
    assert!(main.contains("aw supervise --check --repo"));
    assert!(main.contains("<!-- aw:supervise fp="));
}
