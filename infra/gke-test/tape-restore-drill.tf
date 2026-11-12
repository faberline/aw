# TEMPORARY. One GCS bucket for the one-time backup restore drill of
# faberline/tape#76 (approved by the user on 2026-11-12, same conditions as
# faberline/sift#84). Remove this file, then apply, when the drill reports.
# See "Temporary resources" in docs/standards/test-cluster.md.
#
# Only pods that run as the Kubernetes service account `tape-restore-drill`
# in namespace `tape` can use the bucket. Workload Identity Federation for
# GKE names that account as the IAM principal directly, so no Google service
# account and no pod annotation is needed. No project-level role changes.

locals {
  tape_restore_drill_bucket = "tape-restore-drill-20261112"
}

resource "google_storage_bucket" "tape_restore_drill" {
  project  = var.project_id
  name     = local.tape_restore_drill_bucket
  location = var.region

  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  # One apply removes the bucket with its objects.
  force_destroy = true

  lifecycle_rule {
    condition {
      age = 7
    }
    action {
      type = "Delete"
    }
  }

  labels = {
    "faberline-owner" = "aw-gke-test"
    "purpose"         = "tape-76-restore-drill"
  }
}

resource "google_storage_bucket_iam_member" "tape_restore_drill" {
  bucket = google_storage_bucket.tape_restore_drill.name
  role   = "roles/storage.objectAdmin"
  member = "principal://iam.googleapis.com/projects/${data.google_project.this.number}/locations/global/workloadIdentityPools/${var.project_id}.svc.id.goog/subject/ns/tape/sa/tape-restore-drill"
}
