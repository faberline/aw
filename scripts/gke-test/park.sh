#!/usr/bin/env bash
set -euo pipefail

# Park the shared cluster: set system-pool to 0 nodes, so the idle cluster
# costs only the free-tier management fee. Repository pools go to 0 on their
# own through the autoscaler.
#
# Parks only when no namespace carries faberline.dev/test-run. A namespace
# that is still Terminating keeps its labels, so a later run parks.
#
# The parker (infra/gke-test/park.yaml, every 10 minutes) does the same
# check in GCP and parks on time. This script is the reaper's second net.
# Keep both checks the same.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"
: "${KUBECONFIG:?KUBECONFIG must point at a task-local file}"

live="$(test_namespaces)"
if [[ -n "$live" ]]; then
  log "not parking; live test namespaces:"
  sed "s/^/  /" <<<"$live" >&2
  exit 0
fi

# A run that labels its namespace after the check above can lose its system
# node. When this resize is still running, that run's wake.sh waits for it and
# resizes to 1 again. When wake.sh already returned, its kube-dns wait or its
# test fails and the run reports it; the window is the few seconds between
# check and resize.
resize_pool "$GKE_SYSTEM_POOL" 0
log "parked: $GKE_SYSTEM_POOL at 0 nodes"
