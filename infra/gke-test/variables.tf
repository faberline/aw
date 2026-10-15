variable "project_id" {
  description = "GCP project that owns the shared test cluster."
  type        = string
  default     = "axiom-502607"
}

variable "region" {
  description = "Region of the project's regional resources."
  type        = string
  default     = "asia-east1"
}

variable "zone" {
  description = "Zone of the cluster. The free tier covers one zonal cluster per billing account."
  type        = string
  default     = "asia-east1-a"
}

variable "cluster_name" {
  description = "Name of the existing cluster. It was made by workspace/acceptance/gcp/cluster and is imported here."
  type        = string
  default     = "axiom-operator-acceptance"
}

variable "node_service_account_id" {
  description = "Account ID of the existing node service account."
  type        = string
  default     = "axiom-operator-acceptance-node"
}

variable "github_owner" {
  description = "GitHub organization whose repositories may sign in through Workload Identity Federation."
  type        = string
  default     = "faberline"
}

variable "reaper_repository" {
  description = "Repository whose scheduled workflow deletes expired test namespaces and parks the system pool."
  type        = string
  default     = "aw"
}
