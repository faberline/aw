# The parker: park system-pool on time.
#
# GitHub runs the reaper's `*/30` schedule every 2.5 to 5 hours, so the
# reaper alone keeps system-pool up for hours after the last test. Cloud
# Scheduler runs on time. Every 10 minutes it starts the workflow park.yaml,
# which does what scripts/gke-test/park.sh does. The reaper stays as the
# second net and does its other steps.
#
# Both run in GCP outside the cluster, so they still run when the cluster has
# no node. Cost: about 20,000 Workflows steps a month, about US$0.2 after
# the free 5,000; one Cloud Scheduler job, inside the free 3 per billing
# account.

resource "google_project_service" "workflows" {
  project            = var.project_id
  service            = "workflows.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "cloudscheduler" {
  project            = var.project_id
  service            = "cloudscheduler.googleapis.com"
  disable_on_destroy = false
}

resource "google_service_account" "parker" {
  project      = var.project_id
  account_id   = "gke-test-parker"
  display_name = "GKE test parker (Cloud Scheduler and Workflows)"
}

# Cluster credentials and the system-pool resize.
resource "google_project_iam_member" "parker" {
  project = var.project_id
  role    = google_project_iam_custom_role.test_runner.id
  member  = "serviceAccount:${google_service_account.parker.email}"
}

# Cloud Scheduler signs in as the parker to start an execution.
resource "google_project_iam_member" "parker_invoker" {
  project = var.project_id
  role    = "roles/workflows.invoker"
  member  = "serviceAccount:${google_service_account.parker.email}"
}

# Read only: the nodes of system-pool and the test namespaces.
resource "kubernetes_cluster_role_v1" "parker" {
  metadata {
    name = "faberline-gke-test-parker"
  }
  rule {
    api_groups = [""]
    resources  = ["namespaces", "nodes"]
    verbs      = ["get", "list"]
  }
}

resource "kubernetes_cluster_role_binding_v1" "parker" {
  metadata {
    name = "faberline-gke-test-parker"
  }
  role_ref {
    api_group = "rbac.authorization.k8s.io"
    kind      = "ClusterRole"
    name      = kubernetes_cluster_role_v1.parker.metadata[0].name
  }
  subject {
    api_group = "rbac.authorization.k8s.io"
    kind      = "User"
    name      = google_service_account.parker.email
  }
}

resource "google_workflows_workflow" "park" {
  project         = var.project_id
  name            = "gke-test-park"
  region          = var.region
  description     = "Park system-pool of ${var.cluster_name} when no test runs."
  service_account = google_service_account.parker.email
  source_contents = file("${path.module}/park.yaml")

  # Environment variables, not templatefile(): park.yaml uses ${} itself.
  user_env_vars = {
    GKE_PROJECT     = var.project_id
    GKE_ZONE        = var.zone
    GKE_CLUSTER     = google_container_cluster.test.name
    GKE_SYSTEM_POOL = google_container_node_pool.system.name
    GRACE_SECONDS   = "300"
  }

  depends_on = [
    google_project_service.workflows,
    google_project_iam_member.parker,
    kubernetes_cluster_role_binding_v1.parker,
  ]
}

resource "google_cloud_scheduler_job" "park" {
  project   = var.project_id
  name      = "gke-test-park"
  region    = var.region
  schedule  = "*/10 * * * *"
  time_zone = "Etc/UTC"
  # The call only starts an execution, so it returns fast. No retry: the next
  # tick is the retry.
  attempt_deadline = "60s"

  retry_config {
    retry_count = 0
  }

  http_target {
    http_method = "POST"
    uri         = "https://workflowexecutions.googleapis.com/v1/${google_workflows_workflow.park.id}/executions"
    body        = base64encode("{}")
    headers = {
      "Content-Type" = "application/json"
    }
    oauth_token {
      service_account_email = google_service_account.parker.email
      scope                 = "https://www.googleapis.com/auth/cloud-platform"
    }
  }

  depends_on = [
    google_project_service.cloudscheduler,
    google_project_iam_member.parker_invoker,
  ]
}
