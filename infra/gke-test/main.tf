# Shared GKE test cluster for every faberline repository.
#
# aw owns the cluster, every node pool, and who may deploy. Each repository
# owns only its own namespace: a kustomize overlay plus a workflow that calls
# .github/workflows/gke-test.yml. See docs/standards/test-cluster.md.
#
# Cost model: no node runs while no test runs.
# - pool-<repo>: autoscaled 0..max_nodes, tainted, so it holds only that
#   repository's pods and the autoscaler takes it back to 0 on its own.
# - system-pool: kube-dns and the other kube-system singletons. The autoscaler
#   never removes the last node that holds them, so this pool has no
#   autoscaling at all: scripts/gke-test/wake.sh sets it to 1 node and
#   scripts/gke-test/park.sh sets it back to 0.
# - No other pool may be untainted AND autoscaled. Such a pool would take the
#   Pending kube-system pods after a park and start a node again.

locals {
  pools = { for p in yamldecode(file("${path.module}/pools.yaml")).pools : p.repo => p }

  pool_label_key = "faberline.dev/pool"
  node_sa_email  = "${var.node_service_account_id}@${var.project_id}.iam.gserviceaccount.com"

  # Every identity that signs in from GitHub Actions: one per repository row,
  # plus the reaper. Keys are GitHub repository names under var.github_owner.
  deployers = toset(keys(local.pools))
}

# --- Existing resources -------------------------------------------------------
# workspace/acceptance/gcp/cluster made these. Its state lived in /tmp and the
# GCS bootstrap there was never applied, so nothing else owns them now.

import {
  to = google_container_cluster.test
  id = "projects/${var.project_id}/locations/${var.zone}/clusters/${var.cluster_name}"
}

import {
  to = google_service_account.nodes
  id = "projects/${var.project_id}/serviceAccounts/${local.node_sa_email}"
}

import {
  to = google_project_iam_member.node_baseline
  id = "${var.project_id} roles/container.defaultNodeServiceAccount serviceAccount:${local.node_sa_email}"
}

import {
  to = google_project_iam_member.node_image_pull
  id = "${var.project_id} roles/artifactregistry.reader serviceAccount:${local.node_sa_email}"
}

import {
  to = google_container_node_pool.acceptance
  id = "projects/${var.project_id}/locations/${var.zone}/clusters/${var.cluster_name}/nodePools/acceptance-pool"
}

import {
  to = google_container_node_pool.data_plane
  id = "projects/${var.project_id}/locations/${var.zone}/clusters/${var.cluster_name}/nodePools/data-plane-pool"
}

resource "google_service_account" "nodes" {
  project      = var.project_id
  account_id   = var.node_service_account_id
  display_name = "Axiom persistent operator acceptance GKE nodes"
}

resource "google_project_iam_member" "node_baseline" {
  project = var.project_id
  role    = "roles/container.defaultNodeServiceAccount"
  member  = "serviceAccount:${google_service_account.nodes.email}"
}

resource "google_project_iam_member" "node_image_pull" {
  project = var.project_id
  role    = "roles/artifactregistry.reader"
  member  = "serviceAccount:${google_service_account.nodes.email}"
}

resource "google_container_cluster" "test" {
  project                    = var.project_id
  name                       = var.cluster_name
  location                   = var.zone
  remove_default_node_pool   = true
  initial_node_count         = 1
  deletion_protection        = true
  networking_mode            = "VPC_NATIVE"
  datapath_provider          = "ADVANCED_DATAPATH"
  enable_fqdn_network_policy = true

  release_channel {
    channel = "REGULAR"
  }
  workload_identity_config {
    workload_pool = "${var.project_id}.svc.id.goog"
  }
  ip_allocation_policy {}
  resource_labels = { "axiom-owner" = "gcp-operator-acceptance" }

  lifecycle {
    # Only the free-tier zonal cluster exists. A plan that replaces it is a bug.
    prevent_destroy = true
    # Create-time only; the import cannot read them back.
    ignore_changes = [initial_node_count, remove_default_node_pool, node_config]
  }
}

# --- Pools kept until lumen, sift, and tape move to their own pools ----------

# Untainted. Autoscaling is OFF on purpose: with it on, this pool takes the
# Pending kube-system pods whenever system-pool is parked. Resize it by hand
# (workspace/acceptance/gke-harness/scripts/ensure-cluster.sh does) and back
# to 0 when done.
resource "google_container_node_pool" "acceptance" {
  project  = var.project_id
  name     = "acceptance-pool"
  location = var.zone
  cluster  = google_container_cluster.test.name

  management {
    auto_repair  = true
    auto_upgrade = true
  }
  node_config {
    machine_type    = "e2-standard-2"
    service_account = google_service_account.nodes.email
    oauth_scopes    = ["https://www.googleapis.com/auth/cloud-platform"]
    metadata        = { disable-legacy-endpoints = "true" }
    workload_metadata_config {
      mode = "GKE_METADATA"
    }
  }

  lifecycle {
    ignore_changes = [node_count, initial_node_count]
  }
}

# Tainted, so its autoscaling is safe: only lumen's placement leg tolerates it.
resource "google_container_node_pool" "data_plane" {
  project  = var.project_id
  name     = "data-plane-pool"
  location = var.zone
  cluster  = google_container_cluster.test.name

  autoscaling {
    min_node_count = 0
    max_node_count = 1
  }
  management {
    auto_repair  = true
    auto_upgrade = true
  }
  node_config {
    machine_type    = "e2-standard-2"
    service_account = google_service_account.nodes.email
    oauth_scopes    = ["https://www.googleapis.com/auth/cloud-platform"]
    metadata        = { disable-legacy-endpoints = "true" }
    labels          = { "axiom.dev/pool" = "data-plane" }
    taint {
      key    = "axiom.dev/dedicated"
      value  = "data-plane"
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

# --- New pools ----------------------------------------------------------------

# kube-system only. No autoscaling and no taint; wake.sh and park.sh own its
# size. e2-standard-2, not e2-medium: the DaemonSets plus kube-dns,
# konnectivity, and metrics-server request about 1.2 CPU.
resource "google_container_node_pool" "system" {
  project            = var.project_id
  name               = "system-pool"
  location           = var.zone
  cluster            = google_container_cluster.test.name
  initial_node_count = 0

  management {
    auto_repair  = true
    auto_upgrade = true
  }
  node_config {
    machine_type    = "e2-standard-2"
    spot            = true
    disk_size_gb    = 50
    disk_type       = "pd-balanced"
    service_account = google_service_account.nodes.email
    oauth_scopes    = ["https://www.googleapis.com/auth/cloud-platform"]
    metadata        = { disable-legacy-endpoints = "true" }
    labels          = { (local.pool_label_key) = "system" }
    workload_metadata_config {
      mode = "GKE_METADATA"
    }
  }

  lifecycle {
    ignore_changes = [node_count, initial_node_count]
  }
}

resource "google_container_node_pool" "repo" {
  for_each = local.pools

  project            = var.project_id
  name               = "pool-${each.key}"
  location           = var.zone
  cluster            = google_container_cluster.test.name
  initial_node_count = 0

  autoscaling {
    min_node_count  = 0
    max_node_count  = each.value.max_nodes
    location_policy = "ANY"
  }
  management {
    auto_repair  = true
    auto_upgrade = true
  }
  node_config {
    machine_type    = each.value.machine_type
    spot            = each.value.spot
    disk_size_gb    = 50
    disk_type       = "pd-balanced"
    service_account = google_service_account.nodes.email
    oauth_scopes    = ["https://www.googleapis.com/auth/cloud-platform"]
    metadata        = { disable-legacy-endpoints = "true" }
    # GKE keeps the label and taint on the pool template, so the autoscaler
    # can scale this pool up from zero for a matching Pending pod.
    labels = { (local.pool_label_key) = each.key }
    taint {
      key    = local.pool_label_key
      value  = each.key
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

# --- GitHub Actions sign-in ---------------------------------------------------

resource "google_project_service" "sts" {
  project            = var.project_id
  service            = "sts.googleapis.com"
  disable_on_destroy = false
}

resource "google_iam_workload_identity_pool" "github" {
  project                   = var.project_id
  workload_identity_pool_id = "faberline-github"
  display_name              = "faberline GitHub Actions"
  description               = "OIDC trust for ${var.github_owner} workflows"
}

# The attribute condition is the outer boundary: only tokens minted for a
# repository of this organization exchange at all. The per-repository
# workloadIdentityUser bindings below are the inner boundary.
resource "google_iam_workload_identity_pool_provider" "github" {
  project                            = var.project_id
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github-oidc"
  display_name                       = "GitHub OIDC"
  attribute_condition                = "assertion.repository_owner == \"${var.github_owner}\""

  attribute_mapping = {
    "google.subject"             = "assertion.sub"
    "attribute.repository"       = "assertion.repository"
    "attribute.repository_owner" = "assertion.repository_owner"
  }

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }

  depends_on = [google_project_service.sts]
}

# Enough to fetch credentials and to resize system-pool. Everything inside
# the cluster is decided by Kubernetes RBAC below, not by IAM.
resource "google_project_iam_custom_role" "test_runner" {
  project     = var.project_id
  role_id     = "faberlineGkeTest"
  title       = "faberline GKE test runner"
  description = "Get cluster credentials and resize node pools of the shared test cluster."
  permissions = [
    "container.clusters.get",
    "container.clusters.update",
    "container.operations.get",
  ]
}

# One service account per repository, so audit logs show which repository did
# what.
resource "google_service_account" "deployer" {
  for_each = local.deployers

  project      = var.project_id
  account_id   = "gke-test-${each.key}"
  display_name = "GKE test deployer for ${var.github_owner}/${each.key}"
}

resource "google_project_iam_member" "deployer" {
  for_each = local.deployers

  project = var.project_id
  role    = google_project_iam_custom_role.test_runner.id
  member  = "serviceAccount:${google_service_account.deployer[each.key].email}"
}

resource "google_service_account_iam_member" "deployer_wif" {
  for_each = local.deployers

  service_account_id = google_service_account.deployer[each.key].name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository/${var.github_owner}/${each.key}"
}

resource "google_service_account" "reaper" {
  project      = var.project_id
  account_id   = "gke-test-reaper"
  display_name = "GKE test reaper for ${var.github_owner}/${var.reaper_repository}"
}

resource "google_project_iam_member" "reaper" {
  project = var.project_id
  role    = google_project_iam_custom_role.test_runner.id
  member  = "serviceAccount:${google_service_account.reaper.email}"
}

resource "google_service_account_iam_member" "reaper_wif" {
  service_account_id = google_service_account.reaper.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository/${var.github_owner}/${var.reaper_repository}"
}

# --- Kubernetes RBAC ----------------------------------------------------------

# Deployers get cluster-admin. Their manifests carry CRDs, ClusterRoles, and
# ClusterRoleBindings, and RBAC lets a user grant only the permissions it
# already holds, so any narrower role would still have to include `bind` and
# `escalate`, which is cluster-admin under another name. This is a test
# cluster with no production data, and every repository belongs to one owner.
resource "kubernetes_cluster_role_binding_v1" "deployer" {
  for_each = local.deployers

  metadata {
    name = "faberline-gke-test-${each.key}"
  }
  role_ref {
    api_group = "rbac.authorization.k8s.io"
    kind      = "ClusterRole"
    name      = "cluster-admin"
  }
  subject {
    api_group = "rbac.authorization.k8s.io"
    kind      = "User"
    name      = google_service_account.deployer[each.key].email
  }
}

# The reaper only reads nodes and deletes test namespaces.
resource "kubernetes_cluster_role_v1" "reaper" {
  metadata {
    name = "faberline-gke-test-reaper"
  }
  rule {
    api_groups = [""]
    resources  = ["namespaces"]
    verbs      = ["get", "list", "watch", "delete"]
  }
  rule {
    api_groups = [""]
    resources  = ["nodes"]
    verbs      = ["get", "list"]
  }
}

resource "kubernetes_cluster_role_binding_v1" "reaper" {
  metadata {
    name = "faberline-gke-test-reaper"
  }
  role_ref {
    api_group = "rbac.authorization.k8s.io"
    kind      = "ClusterRole"
    name      = kubernetes_cluster_role_v1.reaper.metadata[0].name
  }
  subject {
    api_group = "rbac.authorization.k8s.io"
    kind      = "User"
    name      = google_service_account.reaper.email
  }
}
