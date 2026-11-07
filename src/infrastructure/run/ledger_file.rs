use std::fs::{self, OpenOptions};
use std::io::{ErrorKind, Write};
use std::path::{Path, PathBuf};

use crate::domain::run::ports::{RunError, RunLedger, RunResult};
use crate::domain::run::record::RunRecord;

/// The run ledger as JSON lines at `<workers dir>/runs.jsonl`. Each record
/// is one write in append mode, so hooks that run at the same time do not
/// mix their lines.
pub struct LedgerFile {
    path: PathBuf,
}

impl LedgerFile {
    pub fn in_dir(dir: &Path) -> Self {
        LedgerFile {
            path: dir.join("runs.jsonl"),
        }
    }

    pub fn path(&self) -> &Path {
        &self.path
    }
}

fn io(what: &str, path: &Path, err: std::io::Error) -> RunError {
    RunError(format!("could not {what} {}: {err}", path.display()))
}

impl RunLedger for LedgerFile {
    fn append(&self, record: &RunRecord) -> RunResult<()> {
        if let Some(dir) = self.path.parent() {
            fs::create_dir_all(dir).map_err(|e| io("create", dir, e))?;
        }
        let mut line = serde_json::to_string(record).map_err(|e| RunError(e.to_string()))?;
        line.push('\n');
        OpenOptions::new()
            .create(true)
            .append(true)
            .open(&self.path)
            .and_then(|mut file| file.write_all(line.as_bytes()))
            .map_err(|e| io("append to", &self.path, e))
    }

    fn read_all(&self) -> RunResult<Vec<RunRecord>> {
        match fs::read_to_string(&self.path) {
            Ok(text) => Ok(text
                .lines()
                .filter_map(|line| serde_json::from_str(line).ok())
                .collect()),
            Err(e) if e.kind() == ErrorKind::NotFound => Ok(Vec::new()),
            Err(e) => Err(io("read", &self.path, e)),
        }
    }
}
