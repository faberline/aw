#!/usr/bin/env bash
set -euo pipefail

# Park the shared cluster: set system-pool to 0 nodes, so the idle cluster
# costs only the free-tier management fee. Repository pools go to 0 on their
# own through the autoscaler.
#
# Parks only when no namespace carries faberline.dev/test-run. A namespace
# that is still Terminating keeps its labels, so the next reaper run parks.

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

# A run that labels its namespace after the check above loses its system
# node. wake.sh in that run already returned, so its kube-dns wait fails and
# the run reports it; the window is the few seconds between check and resize.
resize_pool "$GKE_SYSTEM_POOL" 0
log "parked: $GKE_SYSTEM_POOL at 0 nodes"
