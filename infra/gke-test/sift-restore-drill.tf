# TEMPORARY. One GCS bucket for the one-time backup restore drill of
# faberline/sift#84 (approved by the user on 2026-11-12, decision
# faberline/aw#304). Remove this file, then apply, when the drill is done.
# See "Temporary resources" in docs/standards/test-cluster.md.
#
# Only pods that run as the Kubernetes service account `sift-restore-drill`
# in namespace `sift` can use the bucket. Workload Identity Federation for
# GKE names that account as the IAM principal directly, so no Google service
# account and no pod annotation is needed. No project-level role changes.

locals {
  sift_restore_drill_bucket = "sift-restore-drill-20261111"
}

resource "google_storage_bucket" "sift_restore_drill" {
  project  = var.project_id
  name     = local.sift_restore_drill_bucket
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
    "purpose"         = "sift-84-restore-drill"
  }
}

resource "google_storage_bucket_iam_member" "sift_restore_drill" {
  bucket = google_storage_bucket.sift_restore_drill.name
  role   = "roles/storage.objectAdmin"
  member = "principal://iam.googleapis.com/projects/${data.google_project.this.number}/locations/global/workloadIdentityPools/${var.project_id}.svc.id.goog/subject/ns/sift/sa/sift-restore-drill"
}
