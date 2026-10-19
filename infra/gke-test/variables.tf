variable "project_id" {
  description = "GCP project that owns the shared test cluster. The ID keeps its old axiom name; a project ID cannot change."
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
  description = "Name of the shared test cluster. The node service account is <cluster_name>-node."
  type        = string
  default     = "faberline-test"
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

variable "monthly_budget" {
  description = "Monthly budget of the project, in the billing account's currency (TWD). 2000 TWD is about US$62: normal test use is about 1500 to 2000 TWD a month, so an alert means more spend than usual."
  type        = number
  default     = 2000
}
