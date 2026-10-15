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
  description = "Pool name to its repository, node pool, node label, and taint."
  value = {
    for name, pool in google_container_node_pool.repo : name => {
      repo          = local.pools[name].repo
      node_pool     = pool.name
      node_selector = { (local.pool_label_key) = name }
      toleration    = { key = local.pool_label_key, operator = "Equal", value = name, effect = "NoSchedule" }
      max_nodes     = local.pools[name].max_nodes
    }
  }
}
