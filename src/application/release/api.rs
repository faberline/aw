//! Guarded reads of one repository through the `Provider` port.

use serde_json::Value;

use crate::domain::release::digest::{blob_sha, decode_base64, is_sha, strict_json};
use crate::domain::release::ports::Provider;
use crate::domain::release::tag::encode;
use crate::domain::release::{fail, require};
use crate::domain::work::ports::{PortError, PortResult};

const PAGE: usize = 100;
/// More pages than this is an error: the tool never decides on a cut list.
const MAX_PAGES: usize = 1000;
const MAX_BLOB: u64 = 1_048_576;

/// The REST API of one repository. Every path starts with `repos/<repo>`.
pub struct Api<'a> {
    provider: &'a dyn Provider,
    root: String,
}

fn name(text: &str) -> bool {
    !text.is_empty()
        && text != "."
        && text != ".."
        && text
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || matches!(c, '_' | '.' | '-'))
}

impl<'a> Api<'a> {
    pub fn new(provider: &'a dyn Provider) -> PortResult<Self> {
        let repo = provider.repo();
        require(
            repo.split_once('/')
                .is_some_and(|(owner, repo)| name(owner) && name(repo)),
            "invalid repository",
        )?;
        Ok(Api {
            provider,
            root: format!("repos/{repo}"),
        })
    }

    /// `owner/name`.
    pub fn repo(&self) -> &str {
        self.provider.repo()
    }

    /// `repos/<repo><tail>`. `tail` is empty or starts with `/`. A `.` or
    /// `..` path segment or a URL is refused.
    fn path(&self, tail: &str) -> PortResult<String> {
        let route = tail.split('?').next().unwrap_or_default();
        require(
            (tail.is_empty() || tail.starts_with('/'))
                && !tail.contains("://")
                && route.split('/').all(|part| part != "." && part != ".."),
            "invalid API path",
        )?;
        Ok(format!("{}{tail}", self.root))
    }

    /// `GET`. `None` when the code host answers "not found".
    pub fn optional(&self, tail: &str) -> PortResult<Option<Value>> {
        self.provider.get(&self.path(tail)?)
    }

    /// `GET`. "Not found" is an error.
    pub fn get(&self, tail: &str) -> PortResult<Value> {
        let path = self.path(tail)?;
        self.provider
            .get(&path)?
            .ok_or_else(|| PortError(format!("release: not found: GET {path}")))
    }

    /// Every row of a paginated list. `key` names the list in the response;
    /// without it the response is the list.
    pub fn pages(&self, tail: &str, key: Option<&str>) -> PortResult<Vec<Value>> {
        let join = if tail.contains('?') { '&' } else { '?' };
        let mut rows = Vec::new();
        for page in 1..=MAX_PAGES {
            let data = self.get(&format!("{tail}{join}per_page={PAGE}&page={page}"))?;
            let batch = match key {
                Some(key) => data.get(key),
                None => Some(&data),
            };
            let Some(batch) = batch.and_then(Value::as_array) else {
                return Err(fail("incomplete paginated inventory"));
            };
            rows.extend(batch.iter().cloned());
            if batch.len() < PAGE {
                return Ok(rows);
            }
        }
        Err(fail("release pagination exceeded; no truncated decisions"))
    }

    /// The bytes and the git blob id of the file `path` at the exact commit
    /// `revision`. The size and the blob id must agree with the bytes.
    pub fn blob(&self, path: &str, revision: &str) -> PortResult<(Vec<u8>, String)> {
        require(is_sha(revision), "immutable source required")?;
        let data = self.get(&format!("/contents/{path}?ref={revision}"))?;
        let size = data["size"].as_u64().unwrap_or(0);
        require(
            data["type"] == "file"
                && data["encoding"] == "base64"
                && (1..=MAX_BLOB).contains(&size),
            "invalid source blob",
        )?;
        let Some(raw) = data["content"].as_str().and_then(decode_base64) else {
            return Err(fail("invalid source blob"));
        };
        let sha = data["sha"].as_str().unwrap_or_default();
        require(
            raw.len() as u64 == size && sha == blob_sha(&raw),
            "source blob identity differs",
        )?;
        Ok((raw, sha.to_string()))
    }

    /// The text and the git blob id of a file at an exact commit.
    pub fn text(&self, path: &str, revision: &str) -> PortResult<(String, String)> {
        let (raw, sha) = self.blob(path, revision)?;
        let text = String::from_utf8(raw).map_err(|_| fail("source blob is not UTF-8 text"))?;
        Ok((text, sha))
    }

    /// The strict JSON and the git blob id of a file at an exact commit.
    pub fn json(&self, path: &str, revision: &str) -> PortResult<(Value, String)> {
        let (text, sha) = self.text(path, revision)?;
        Ok((strict_json(&text)?, sha))
    }

    /// The exact commit of the default branch, now.
    pub fn default_head(&self) -> PortResult<String> {
        let meta = self.get("")?;
        let Some(branch) = meta["default_branch"].as_str() else {
            return Err(fail("repository has no default branch"));
        };
        let commit = self.get(&format!("/commits/{}", encode(branch)))?;
        let sha = commit["sha"].as_str().unwrap_or_default();
        require(is_sha(sha), "default branch has no exact commit")?;
        Ok(sha.to_string())
    }

    /// The one write: create the tag. It fails when the tag exists.
    pub fn create_tag(&self, tag: &str, sha: &str) -> PortResult<Value> {
        self.provider.create_tag(tag, sha)
    }
}
