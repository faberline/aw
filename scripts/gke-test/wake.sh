#!/usr/bin/env bash
set -euo pipefail

# Wake the shared cluster: give system-pool one Ready node and wait for
# kube-dns. Repository pools need no wake; the autoscaler adds their nodes
# when their pods are Pending.
#
# Call it AFTER the test namespace exists with its faberline.dev/test-run
# label. park.sh refuses to park while such a namespace exists, so this order
# keeps the reaper from parking a run that just woke the cluster.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"
: "${KUBECONFIG:?KUBECONFIG must point at a task-local file}"

if (( "$(ready_nodes "$GKE_SYSTEM_POOL")" == 0 )); then
  log "waking $GKE_SYSTEM_POOL (resize to 1)"
  resize_pool "$GKE_SYSTEM_POOL" 1
fi

deadline=$((SECONDS + 600))
until (( "$(ready_nodes "$GKE_SYSTEM_POOL")" >= 1 )); do
  if (( SECONDS >= deadline )); then
    log "timed out waiting for a Ready node in $GKE_SYSTEM_POOL"
    kubectl get nodes -o wide >&2 || true
    exit 1
  fi
  sleep 10
done

kubectl -n kube-system rollout status deployment/kube-dns --timeout=300s >&2
log "awake: $GKE_SYSTEM_POOL has a Ready node and kube-dns is available"
