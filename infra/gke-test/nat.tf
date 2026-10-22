# Outbound internet for the private nodes in main.tf.
#
# The nodes have no external IP, so they pull images from ghcr.io through this
# Cloud NAT. The cluster's control plane endpoint uses Private Service Connect,
# and the default subnet has Private Google Access, so the nodes reach GKE and
# Google APIs without it.
#
# Cost: one auto-allocated external IP while the gateway exists, a small
# charge per node-hour while nodes run, and a charge per GB that goes through.

data "google_compute_subnetwork" "default" {
  project = var.project_id
  region  = var.region
  name    = "default"
}

resource "google_compute_router" "nat" {
  project = var.project_id
  region  = var.region
  name    = "${var.cluster_name}-nat"
  network = data.google_compute_subnetwork.default.network
}

resource "google_compute_router_nat" "nat" {
  project = var.project_id
  region  = var.region
  name    = "${var.cluster_name}-nat"
  router  = google_compute_router.nat.name

  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "LIST_OF_SUBNETWORKS"
  subnetwork {
    name                    = data.google_compute_subnetwork.default.id
    source_ip_ranges_to_nat = ["ALL_IP_RANGES"]
  }

  # One NAT IP has about 64,000 ports. 1,024 per node leaves room for parallel
  # image pulls and still covers more nodes than the region's CPU quota allows.
  min_ports_per_vm = 1024

  log_config {
    enable = true
    filter = "ERRORS_ONLY"
  }
}
