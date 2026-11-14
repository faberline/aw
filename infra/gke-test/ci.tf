# ci-on-gke phase 1 (aw#390): the pool, the sccache bucket and the identity
# that CI Jobs use. The pool is not a row of pools.yaml on purpose: a row names
# a repository, and every repository in pools.yaml gets a deployer service
# account with cluster-admin (see local.deployers in main.tf). The CI pool
# belongs to no single repository and needs no new deployer.
#
# Cost: Spot n4-standard-2, 0 to 8 nodes (16 vCPU at most), 0 nodes while no
# CI Job runs. The
# pool, its VMs, and the bucket carry the label faberline-ci=aw-ci, so the
# budget below counts only them.

locals {
  ci_pool_name = "aw-ci"
  ci_label     = { "faberline-ci" = "aw-ci" }
  ci_namespace = "aw-ci"
  ci_ksa       = "ci-job"
}

resource "google_container_node_pool" "ci" {
  project            = var.project_id
  name               = "pool-${local.ci_pool_name}"
  location           = var.zone
  cluster            = google_container_cluster.test.name
  initial_node_count = 0

  autoscaling {
    min_node_count  = 0
    max_node_count  = 8
    location_policy = "ANY"
  }
  management {
    auto_repair  = true
    auto_upgrade = true
  }
  network_config {
    enable_private_nodes = true
  }
  node_config {
    machine_type    = "n4-standard-2"
    spot            = true
    disk_size_gb    = 50
    disk_type       = "hyperdisk-balanced"
    service_account = google_service_account.nodes.email
    oauth_scopes    = ["https://www.googleapis.com/auth/cloud-platform"]
    metadata        = { disable-legacy-endpoints = "true" }
    labels          = { (local.pool_label_key) = local.ci_pool_name }
    resource_labels = local.ci_label
    taint {
      key    = local.pool_label_key
      value  = local.ci_pool_name
      effect = "NO_SCHEDULE"
    }
    workload_metadata_config {
      mode = "GKE_METADATA"
    }
  }

  lifecycle {
    ignore_changes = [node_count, initial_node_count]
  }
}

# --- sccache bucket -----------------------------------------------------------

resource "google_storage_bucket" "sccache" {
  project                     = var.project_id
  name                        = "${var.project_id}-aw-ci-sccache"
  location                    = var.region
  storage_class               = "STANDARD"
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  labels                      = local.ci_label

  lifecycle_rule {
    condition {
      age = 30
    }
    action {
      type = "Delete"
    }
  }
}

# --- Identity of CI Jobs ------------------------------------------------------

# The Google service account can read and write this one bucket and nothing
# else. A Job pod uses the Kubernetes service account below through Workload
# Identity. The Kubernetes service account has no RBAC role.
resource "google_service_account" "ci_job" {
  project      = var.project_id
  account_id   = "gke-ci-job"
  display_name = "CI Jobs of ci-on-gke (sccache bucket only)"
}

resource "google_storage_bucket_iam_member" "ci_job_sccache" {
  bucket = google_storage_bucket.sccache.name
  role   = "roles/storage.objectUser"
  member = "serviceAccount:${google_service_account.ci_job.email}"
}

# Jobs of a run use their own namespace `ci-<repo>-<run_id>-<attempt>-<stage>`,
# so no fixed namespace/KSA pair fits. Grant this one bucket to every
# workload of this cluster's Workload Identity pool (a Kubernetes service
# account of any namespace, direct access without a Google service account).
# Bucket level only: no project role.
resource "google_storage_bucket_iam_member" "ci_cluster_sccache" {
  bucket = google_storage_bucket.sccache.name
  role   = "roles/storage.objectUser"
  member = "principalSet://iam.googleapis.com/projects/${data.google_project.this.number}/locations/global/workloadIdentityPools/${var.project_id}.svc.id.goog/*"
}

resource "kubernetes_namespace_v1" "ci" {
  metadata {
    name = local.ci_namespace
  }
}

resource "kubernetes_service_account_v1" "ci_job" {
  metadata {
    name      = local.ci_ksa
    namespace = kubernetes_namespace_v1.ci.metadata[0].name
    annotations = {
      "iam.gke.io/gcp-service-account" = google_service_account.ci_job.email
    }
  }
}

resource "google_service_account_iam_member" "ci_job_wi" {
  service_account_id = google_service_account.ci_job.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "serviceAccount:${var.project_id}.svc.id.goog[${local.ci_namespace}/${local.ci_ksa}]"
}

# --- Budget of the CI pool ----------------------------------------------------

resource "google_billing_budget" "ci" {
  provider = google.billing

  billing_account = data.google_project.this.billing_account
  display_name    = "${var.cluster_name} ci-on-gke monthly"

  budget_filter {
    projects = ["projects/${data.google_project.this.number}"]
    labels   = { "faberline-ci" = "aw-ci" }
  }

  amount {
    specified_amount {
      units = tostring(var.ci_monthly_budget)
    }
  }

  threshold_rules {
    threshold_percent = 0.5
  }
  threshold_rules {
    threshold_percent = 0.9
  }
  threshold_rules {
    threshold_percent = 1.0
  }
  threshold_rules {
    threshold_percent = 1.0
    spend_basis       = "FORECASTED_SPEND"
  }

  depends_on = [google_project_service.billingbudgets]
}
