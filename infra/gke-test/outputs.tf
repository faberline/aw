output "cluster_name" {
  value = google_container_cluster.test.name
}

output "wif_provider" {
  description = "Hard-coded in .github/workflows/gke-test.yml and gke-test-reaper.yml."
  value       = google_iam_workload_identity_pool_provider.github.name
}

output "deployer_service_accounts" {
  description = "Repository name to the service account its gke-test workflow signs in as."
  value       = { for repo, sa in google_service_account.deployer : repo => sa.email }
}

output "reaper_service_account" {
  value = google_service_account.reaper.email
}

output "pools" {
  description = "Repository name to its node pool, node label, and taint."
  value = {
    for repo, pool in google_container_node_pool.repo : repo => {
      name          = pool.name
      node_selector = { (local.pool_label_key) = repo }
      toleration    = { key = local.pool_label_key, operator = "Equal", value = repo, effect = "NoSchedule" }
      max_nodes     = local.pools[repo].max_nodes
    }
  }
}
