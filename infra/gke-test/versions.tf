terraform {
  required_version = ">= 1.9.0"

  # The bucket comes from ./bootstrap, which is applied once by hand.
  backend "gcs" {
    bucket = "axiom-502607-faberline-tfstate"
    prefix = "gke-test"
  }

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 2.30"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

data "google_client_config" "current" {}

# The API server runs on the GKE control plane, so this provider works while
# every node pool is at zero nodes.
provider "kubernetes" {
  host                   = "https://${google_container_cluster.test.endpoint}"
  token                  = data.google_client_config.current.access_token
  cluster_ca_certificate = base64decode(google_container_cluster.test.master_auth[0].cluster_ca_certificate)
}
