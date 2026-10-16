# Shared GKE test cluster for every faberline repository.
#
# aw owns the cluster, every node pool, and who may deploy. Each repository
# owns only its own namespace: a kustomize overlay plus a workflow that calls
# .github/workflows/gke-test.yml. See docs/standards/test-cluster.md.
#
# Cost model: no node runs while no test runs.
# - pool-<pool>: autoscaled 0..max_nodes, tainted, so it holds only the pods
#   that select it and the autoscaler takes it back to 0 on its own. A
#   repository can have more than one, for example <repo> and <repo>-client.
# - system-pool: kube-dns and the other kube-system singletons. The autoscaler
#   never removes the last node that holds them, so this pool has no
#   autoscaling at all: scripts/gke-test/wake.sh sets it to 1 node and
#   scripts/gke-test/park.sh sets it back to 0.
# - No other pool may be untainted AND autoscaled. Such a pool would take the
#   Pending kube-system pods after a park and start a node again.

locals {
  # Keyed by pool name. Every row names its pool `<repo>-<role>`.
  pools = {
    for p in yamldecode(file("${path.module}/pools.yaml")).pools :
    p.pool => p
  }

  pool_label_key = "faberline.dev/pool"

  # These series take only Hyperdisk, for boot disks and for PVCs. Keep the
  # same list in HYPERDISK_ONLY_RE of scripts/gke-test/run.sh.
  hyperdisk_only_re = "^(n4|n4a|n4d|c4|c4a|c4d)-"

  # Every identity that signs in from GitHub Actions: one per repository in
  # pools.yaml, plus the reaper. Keys are GitHub repository names under
  # var.github_owner.
  deployers = toset([for p in values(local.pools) : p.repo])
}

# --- Cluster ------------------------------------------------------------------

resource "google_service_account" "nodes" {
  project      = var.project_id
  account_id   = "${var.cluster_name}-node"
  display_name = "Nodes of the ${var.cluster_name} GKE cluster"
}

resource "google_project_iam_member" "node_baseline" {
  project = var.project_id
  role    = "roles/container.defaultNodeServiceAccount"
  member  = "serviceAccount:${google_service_account.nodes.email}"
}

resource "google_container_cluster" "test" {
  project                    = var.project_id
  name                       = var.cluster_name
  location                   = var.zone
  deletion_protection        = true
  networking_mode            = "VPC_NATIVE"
  datapath_provider          = "ADVANCED_DATAPATH"
  enable_fqdn_network_policy = true

  # GKE needs a default pool to create the cluster. Terraform deletes it right
  # after; the real pools are below.
  remove_default_node_pool = true
  initial_node_count       = 1
  node_config {
    machine_type    = "e2-medium"
    spot            = true
    service_account = google_service_account.nodes.email
    oauth_scopes    = ["https://www.googleapis.com/auth/cloud-platform"]
  }

  release_channel {
    channel = "REGULAR"
  }
  workload_identity_config {
    workload_pool = "${var.project_id}.svc.id.goog"
  }
  ip_allocation_policy {}
  # Remove an idle node sooner than the default profile does.
  cluster_autoscaling {
    autoscaling_profile = "OPTIMIZE_UTILIZATION"
  }
  resource_labels = { "faberline-owner" = "aw-gke-test" }

  lifecycle {
    # The free tier covers one zonal cluster. A plan that replaces it is a bug.
    prevent_destroy = true
    ignore_changes  = [initial_node_count, node_config]
  }

  depends_on = [google_project_iam_member.node_baseline]
}

# --- Pools --------------------------------------------------------------------

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
    disk_type       = can(regex(local.hyperdisk_only_re, each.value.machine_type)) ? "hyperdisk-balanced" : "pd-balanced"
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
    # The pool name shows which repository owns the pool, and run.sh lets a
    # repository select only pools that pools.yaml gives to it.
    precondition {
      condition     = can(regex("^${each.value.repo}-[a-z0-9]+(-[a-z0-9]+)*$", each.key))
      error_message = "pools.yaml: pool \"${each.key}\" must be \"${each.value.repo}-<role>\", for example \"${each.value.repo}-server\"."
    }
    # A shared-core node leaves too little CPU after GKE's DaemonSets: a test
    # Job that requested 500m never got an e2-medium node.
    precondition {
      condition     = !can(regex("^(e2-(micro|small|medium)|f1-micro|g1-small)$", each.value.machine_type))
      error_message = "pools.yaml: pool \"${each.key}\" uses shared-core ${each.value.machine_type}; use e2-standard-2 or bigger."
    }
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

# Step 3 of scripts/gke-test/reap.sh: find and delete orphan PVC disks.
resource "google_project_iam_custom_role" "disk_reaper" {
  project     = var.project_id
  role_id     = "faberlineGkeTestDiskReaper"
  title       = "faberline GKE test disk reaper"
  description = "List and delete persistent disks that no VM and no PersistentVolume uses."
  permissions = [
    "compute.disks.delete",
    "compute.disks.get",
    "compute.disks.list",
    "compute.zoneOperations.get",
  ]
}

resource "google_project_iam_member" "reaper_disks" {
  project = var.project_id
  role    = google_project_iam_custom_role.disk_reaper.id
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

# The reaper deletes test namespaces, and the ClusterRoles and
# ClusterRoleBindings that an operator made for a namespace that is gone. It
# only reads everything else: nodes for park.sh, PersistentVolumes so it never
# deletes a disk that one still names, and pods to report the ones outside test
# namespaces. Deleting RBAC objects needs no `escalate`.
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
    resources  = ["nodes", "persistentvolumes", "pods"]
    verbs      = ["get", "list"]
  }
  rule {
    api_groups = ["rbac.authorization.k8s.io"]
    resources  = ["clusterroles", "clusterrolebindings"]
    verbs      = ["get", "list", "delete"]
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

# --- Budget -------------------------------------------------------------------

# The last net under the reaper: an email when the project spends more than
# expected in a month. It reaches the billing account's admins and users,
# because default IAM recipients stay on. The billing account comes from the
# project, so its ID is not in this public repository.
data "google_project" "this" {
  project_id = var.project_id
}

resource "google_project_service" "billingbudgets" {
  project            = var.project_id
  service            = "billingbudgets.googleapis.com"
  disable_on_destroy = false
}

resource "google_billing_budget" "test" {
  provider = google.billing

  billing_account = data.google_project.this.billing_account
  display_name    = "${var.cluster_name} monthly"

  budget_filter {
    projects = ["projects/${data.google_project.this.number}"]
  }

  # In the billing account's currency.
  amount {
    specified_amount {
      units = tostring(var.monthly_budget)
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
  # Warn early when the month is on track to pass the budget.
  threshold_rules {
    threshold_percent = 1.0
    spend_basis       = "FORECASTED_SPEND"
  }

  depends_on = [google_project_service.billingbudgets]
}
