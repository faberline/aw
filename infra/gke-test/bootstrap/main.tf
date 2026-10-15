# One-time bootstrap: the GCS bucket that holds the state of ../ (the shared
# test cluster). Apply it once by hand with local state:
#
#   terraform -chdir=infra/gke-test/bootstrap init
#   terraform -chdir=infra/gke-test/bootstrap apply
#
# The local state of this folder is not worth keeping. If it is lost, import
# the bucket again with `terraform import google_storage_bucket.tfstate <name>`.
terraform {
  required_version = ">= 1.9.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }
}

variable "project_id" {
  type    = string
  default = "axiom-502607"
}

variable "region" {
  type    = string
  default = "asia-east1"
}

provider "google" {
  project = var.project_id
  region  = var.region
}

# The name is also hard-coded in ../versions.tf; a backend block cannot read
# variables.
resource "google_storage_bucket" "tfstate" {
  project                     = var.project_id
  name                        = "${var.project_id}-faberline-tfstate"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  versioning {
    enabled = true
  }

  lifecycle {
    prevent_destroy = true
  }

  labels = { "faberline-owner" = "aw-gke-test" }
}

output "state_bucket" {
  value = google_storage_bucket.tfstate.name
}
