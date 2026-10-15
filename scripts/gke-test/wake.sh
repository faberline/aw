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

# Not `rollout status`: kube-dns sat Pending while the pool had 0 nodes, so
# its progress deadline has often passed already and `rollout status` fails at
# once. The Available condition only asks for a ready replica.
kubectl -n kube-system wait --for=condition=Available deployment/kube-dns --timeout=300s >&2
log "awake: $GKE_SYSTEM_POOL has a Ready node and kube-dns is available"
