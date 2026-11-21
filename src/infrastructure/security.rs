//! Adapter for the security check: the tracked files of a git checkout and
//! the `[standards.security]` table of its `aw.toml`.

use std::time::{SystemTime, UNIX_EPOCH};

use toml::Table;

use crate::domain::security::ports::{Config, Exception, SecurityFiles};
use crate::domain::work::ports::{PortError, PortResult};
use crate::infrastructure::repo::TrackedFiles;
use crate::infrastructure::work::process::run;

fn field(item: &toml::Value, name: &str) -> Option<String> {
    item.get(name)
        .and_then(toml::Value::as_str)
        .filter(|s| !s.trim().is_empty())
        .map(str::to_string)
}

fn is_date(text: &str) -> bool {
    let b = text.as_bytes();
    b.len() == 10
        && b[4] == b'-'
        && b[7] == b'-'
        && b.iter()
            .enumerate()
            .all(|(i, c)| i == 4 || i == 7 || c.is_ascii_digit())
}

/// The `[standards.security]` table of an `aw.toml` text.
pub fn parse_config(text: &str) -> PortResult<Config> {
    let table = text
        .parse::<Table>()
        .map_err(|e| PortError(format!("could not parse aw.toml: {e}")))?;
    let mut config = Config::default();
    let Some(security) = table
        .get("standards")
        .and_then(|s| s.get("security"))
        .and_then(toml::Value::as_table)
    else {
        return Ok(config);
    };
    if let Some(value) = security.get("built_images") {
        config.built_images = value
            .as_array()
            .and_then(|a| {
                a.iter()
                    .map(|v| v.as_str().map(str::to_string))
                    .collect::<Option<Vec<_>>>()
            })
            .ok_or_else(|| {
                PortError(
                    "aw.toml: standards.security.built_images must be an array of strings".into(),
                )
            })?;
    }
    if let Some(value) = security.get("exception") {
        let items = value.as_array().ok_or_else(|| {
            PortError("aw.toml: standards.security.exception must be an array of tables".into())
        })?;
        for item in items {
            let rule = field(item, "rule");
            let reason = field(item, "reason");
            let expires = field(item, "expires").filter(|d| is_date(d));
            match (rule, reason, expires) {
                (Some(rule), Some(reason), Some(expires)) => config.exceptions.push(Exception {
                    rule,
                    path: field(item, "path"),
                    reason,
                    expires,
                }),
                _ => {
                    return Err(PortError(
                        "aw.toml: each standards.security.exception needs a `rule`, a `reason` and an `expires` date (YYYY-MM-DD)".into(),
                    ));
                }
            }
        }
    }
    Ok(config)
}

/// The UTC date of `seconds` since the Unix epoch, `YYYY-MM-DD`.
pub fn date_of(seconds: u64) -> String {
    let days = i64::try_from(seconds / 86_400).unwrap_or(0);
    // Civil from days (Howard Hinnant).
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = yoe + era * 400 + i64::from(m <= 2);
    format!("{y:04}-{m:02}-{d:02}")
}

impl SecurityFiles for TrackedFiles {
    fn config(&self) -> PortResult<Config> {
        match std::fs::read_to_string(self.root().join("aw.toml")) {
            Ok(text) => parse_config(&text),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(Config::default()),
            Err(e) => Err(PortError(format!("could not read aw.toml: {e}"))),
        }
    }

    fn paths(&self) -> PortResult<Vec<String>> {
        Ok(crate::domain::repo::ports::RepoFiles::entries(self)?
            .into_iter()
            .map(|e| e.path)
            .collect())
    }

    fn text(&self, path: &str) -> PortResult<Option<String>> {
        crate::domain::repo::ports::RepoFiles::text(self, path)
    }

    fn repo_name(&self) -> String {
        let remote = run(
            "git",
            &[
                "-C",
                &self.root().to_string_lossy(),
                "remote",
                "get-url",
                "origin",
            ],
            None,
        )
        .unwrap_or_default();
        let from_remote = remote
            .trim()
            .trim_end_matches(".git")
            .rsplit(['/', ':'])
            .next()
            .unwrap_or("")
            .to_string();
        if from_remote.is_empty() {
            self.root()
                .file_name()
                .map(|n| n.to_string_lossy().to_string())
                .unwrap_or_default()
        } else {
            from_remote
        }
    }

    fn today(&self) -> String {
        let seconds = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_or(0, |d| d.as_secs());
        date_of(seconds)
    }
}
