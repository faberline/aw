//! Adapter for `aw release-receipt`: the evidence files on this machine.

use std::path::Path;

use crate::domain::release::receipt::ReceiptFiles;

/// The local file system.
pub struct LocalFiles;

impl ReceiptFiles for LocalFiles {
    fn regular(&self, path: &Path) -> Result<Vec<u8>, String> {
        if path.is_symlink() || !path.is_file() {
            return Err(format!("not a regular file: {}", path.display()));
        }
        std::fs::read(path).map_err(|e| format!("could not read {}: {e}", path.display()))
    }

    fn real_dir(&self, path: &Path) -> bool {
        !path.is_symlink() && path.is_dir()
    }

    fn is_symlink(&self, path: &Path) -> bool {
        path.is_symlink()
    }

    fn write(&self, path: &Path, bytes: &[u8]) -> Result<(), String> {
        if let Some(parent) = path.parent().filter(|p| !p.as_os_str().is_empty()) {
            std::fs::create_dir_all(parent)
                .map_err(|e| format!("could not make {}: {e}", parent.display()))?;
        }
        std::fs::write(path, bytes).map_err(|e| format!("could not write {}: {e}", path.display()))
    }
}
