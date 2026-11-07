use serde::Serialize;

use super::record::{Client, Event, RunRecord};

/// One run: every ledger record of one client session.
#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct RunSummary {
    pub client: Client,
    pub session: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub clone: Option<String>,
    /// `ended` when the last record is an end, else `open`. An old `open`
    /// run crashed, hung, or is still working.
    pub status: &'static str,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub started: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub ended: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub prompt: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub reason: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub last_message: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub transcript: Option<String>,
}

impl RunSummary {
    fn new(record: &RunRecord) -> Self {
        RunSummary {
            client: record.client,
            session: record.session.clone(),
            clone: None,
            status: "open",
            started: None,
            ended: None,
            prompt: None,
            reason: None,
            error: None,
            last_message: None,
            transcript: None,
        }
    }

    fn add(&mut self, record: &RunRecord) {
        if self.clone.is_none() {
            self.clone.clone_from(&record.clone);
        }
        if self.transcript.is_none() {
            self.transcript.clone_from(&record.transcript);
        }
        match record.event {
            Event::Start => {
                if self.started.is_none() {
                    self.started = Some(record.at.clone());
                    self.prompt.clone_from(&record.prompt);
                }
                self.status = "open";
            }
            Event::End => {
                self.ended = Some(record.at.clone());
                self.reason.clone_from(&record.reason);
                self.error.clone_from(&record.error);
                self.last_message.clone_from(&record.last_message);
                self.status = "ended";
            }
        }
    }
}

/// Group records by client session, in the order each run first appears,
/// and keep the last `limit` runs.
pub fn summarize(records: &[RunRecord], limit: usize) -> Vec<RunSummary> {
    let mut runs: Vec<RunSummary> = Vec::new();
    for record in records {
        let index = runs
            .iter()
            .position(|r| r.client == record.client && r.session == record.session)
            .unwrap_or_else(|| {
                runs.push(RunSummary::new(record));
                runs.len() - 1
            });
        runs[index].add(record);
    }
    let skip = runs.len().saturating_sub(limit);
    runs.split_off(skip)
}
