//! What the release gate needs from the code host.

use serde_json::Value;

use super::tag::PushTrigger;
use crate::domain::work::ports::PortResult;

/// The REST API of one repository. Reads are free; the only write is the
/// creation of a tag.
pub trait Provider {
    /// `owner/name`.
    fn repo(&self) -> &str;
    /// `GET <path>`. `None` when the code host answers "not found".
    fn get(&self, path: &str) -> PortResult<Option<Value>>;
    /// Create `refs/tags/<tag>` at the commit `sha`. It must fail when the
    /// tag exists. Returns the response of the code host.
    fn create_tag(&self, tag: &str, sha: &str) -> PortResult<Value>;
}

/// Reads the trigger of a workflow file.
pub trait WorkflowParser {
    /// The `on.push` mapping of the workflow `text`. `None` when the
    /// workflow has no `on.push` mapping. An error when the text is not one
    /// YAML document.
    fn push_trigger(&self, text: &str) -> PortResult<Option<PushTrigger>>;
}
