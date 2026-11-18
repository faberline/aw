# --- Billing export dataset ---------------------------------------------------
#
# The dataset that receives the Cloud Billing export to BigQuery. This file only
# makes the dataset. The owner turns the export on by hand in the console
# (Billing > Billing export > BigQuery export > Standard usage cost), and Google
# then adds its own export service account to the dataset. Terraform and the
# API cannot turn the export on, and no IAM member is added here.
#
# Location: asia-east1 is on the list of regions that Cloud Billing export
# supports (docs: "Set up Cloud Billing data export to BigQuery"). It is the
# region of the cluster. The location cannot change after the dataset exists.
#
# No table or partition expires: the bill history stays.

resource "google_project_service" "bigquery" {
  project            = var.project_id
  service            = "bigquery.googleapis.com"
  disable_on_destroy = false
}

resource "google_bigquery_dataset" "billing_export" {
  project     = var.project_id
  dataset_id  = "billing_export"
  location    = var.region
  description = "Cloud Billing export to BigQuery (standard usage cost) for the shared test cluster. Used to read the cost of each node pool in TWD. See docs/standards/cost-control.md in faberline/aw."

  # Bill history stays: no default expiration, and a destroy never deletes data.
  delete_contents_on_destroy = false

  lifecycle {
    prevent_destroy = true
  }

  depends_on = [google_project_service.bigquery]
}
