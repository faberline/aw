use std::io::Write;
use std::process::{Command, Stdio};

use crate::domain::work::ports::{PortError, PortResult};

/// Run a program and return its stdout. A non-zero exit becomes an error
/// that carries stderr.
pub fn run(program: &str, args: &[&str], stdin: Option<&str>) -> PortResult<String> {
    let mut command = Command::new(program);
    command
        .args(args)
        .stdin(if stdin.is_some() {
            Stdio::piped()
        } else {
            Stdio::null()
        })
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    let mut child = command
        .spawn()
        .map_err(|e| PortError(format!("could not start `{program}`: {e}")))?;
    if let Some(input) = stdin
        && let Some(mut pipe) = child.stdin.take()
    {
        pipe.write_all(input.as_bytes())
            .map_err(|e| PortError(format!("could not write to `{program}`: {e}")))?;
    }
    let output = child
        .wait_with_output()
        .map_err(|e| PortError(format!("`{program}` failed: {e}")))?;
    if output.status.success() {
        Ok(String::from_utf8_lossy(&output.stdout).into_owned())
    } else {
        let stderr = String::from_utf8_lossy(&output.stderr);
        let shown = args.first().map_or(String::new(), |a| format!(" {a}"));
        Err(PortError(format!(
            "`{program}{shown}` exited with {}: {}",
            output.status,
            stderr.trim()
        )))
    }
}

/// Like [`run`], but only reports whether the program exited with 0.
pub fn succeeds(program: &str, args: &[&str]) -> PortResult<bool> {
    let status = Command::new(program)
        .args(args)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .map_err(|e| PortError(format!("could not start `{program}`: {e}")))?;
    Ok(status.success())
}
