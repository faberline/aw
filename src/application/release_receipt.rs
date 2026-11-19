//! Use case: make the GKE release receipt of one app of a checkout.

use std::path::Path;

use crate::domain::release::app_check::ContractRepo;
use crate::domain::release::receipt::{self, Inputs, ReceiptFiles};

pub use crate::domain::release::receipt::Receipt;

/// The ports the use case needs. The composition root builds them.
#[derive(Clone, Copy)]
pub struct ReceiptServices<'a> {
    pub repo: &'a dyn ContractRepo,
    pub files: &'a dyn ReceiptFiles,
}

/// What the caller gives, besides the services.
#[derive(Clone, Debug)]
pub struct ReceiptRequest<'a> {
    pub app: &'a str,
    pub backend: &'a str,
    pub candidate_manifest: &'a Path,
    pub output: &'a Path,
    pub gh_run: Option<&'a Path>,
    pub evidence_dir: Option<&'a Path>,
}

/// Make the receipt and write it with its `.sha256` sidecar. The `Err` is
/// the full message.
pub fn make(s: ReceiptServices<'_>, request: &ReceiptRequest<'_>) -> Result<Receipt, String> {
    let apps = s.repo.apps().map_err(|e| e.to_string())?;
    let Some(app) = apps.iter().find(|a| a.name == request.app) else {
        let known: Vec<&str> = apps.iter().map(|a| a.name.as_str()).collect();
        return Err(format!(
            "GKE receipt refused: unknown shared-script app: {} (known: {})",
            request.app,
            known.join(" ")
        ));
    };
    let made = receipt::make(
        app,
        &Inputs {
            backend: request.backend,
            candidate_manifest: request.candidate_manifest,
            output: request.output,
            gh_run: request.gh_run,
            evidence_dir: request.evidence_dir,
        },
        s.files,
    )
    .map_err(|r| r.to_string())?;
    s.files.write(request.output, &made.data)?;
    s.files.write(
        &request.output.with_file_name(&made.sidecar_name),
        made.sidecar.as_bytes(),
    )?;
    Ok(made)
}
