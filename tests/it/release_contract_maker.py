"""A receipt maker with only the tables that `aw release-contract` reads."""

REPOSITORY = "faberline/defer"
IMAGE_OWNER = "ghcr.io/faberline"
ACCEPTANCE_JOB = "deploy + verify on GKE"
VERIFY_IMAGE_STEP = "Verify prebuilt image input"
HARNESS_STEP = "Run acceptance harness"
PARK_STEP = "Park node pool (belt and suspenders)"
TWO = ("x86_64-unknown-linux-musl", "aarch64-unknown-linux-musl")
APPS = {"defer": {"backend": "gke-acceptance", "targets": TWO, "functional": ("readyz", "round_trip", "durability")}}


def candidate_schema(app):
    return f"cclab.{app}.candidate-manifest.v1"


def receipt_schema(app):
    return f"{app}.gke-release-receipt/v1"


def final_jobs(app):
    return {j: "success" for j in ("identity", "build", f"{app}-release-gates", "ghcr-image-and-attest", "manifest", "verify-candidate", "result")}
