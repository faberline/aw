# shellcheck shell=bash disable=SC2034
# Shared settings for scripts/gke-test/*.sh. Source it; do not run it.
#
# Every value has a default for the one shared cluster in infra/gke-test/.
# KUBECONFIG must point at a task-local file so a run never rewrites the
# caller's real one, and parallel CI jobs never race on ~/.kube/config.

GKE_PROJECT="${GKE_PROJECT:-axiom-502607}"
GKE_ZONE="${GKE_ZONE:-asia-east1-a}"
GKE_CLUSTER="${GKE_CLUSTER:-axiom-operator-acceptance}"
GKE_SYSTEM_POOL="${GKE_SYSTEM_POOL:-system-pool}"

# Namespace labels. test-run marks a namespace as a live test; expires is the
# Unix time after which the reaper may delete it.
LABEL_RUN="faberline.dev/test-run"
LABEL_EXPIRES="faberline.dev/expires"
LABEL_POOL="faberline.dev/pool"

log() { printf '[gke-test] %s\n' "$*" >&2; }

# Resize a node pool. GKE refuses a second cluster operation while one is in
# progress (two repositories waking at once, or a park racing a wake), so
# retry instead of failing the run.
resize_pool() {
  local pool="$1" nodes="$2" attempt
  for attempt in 1 2 3 4 5 6 7 8 9 10; do
    if gcloud container clusters resize "$GKE_CLUSTER" \
      --project="$GKE_PROJECT" --zone="$GKE_ZONE" \
      --node-pool="$pool" --num-nodes="$nodes" --quiet >&2; then
      return 0
    fi
    log "resize $pool to $nodes failed (attempt $attempt); retrying in 30s"
    sleep 30
  done
  log "resize $pool to $nodes failed 10 times"
  return 1
}

# Number of Ready nodes in a pool.
ready_nodes() {
  kubectl get nodes -l "cloud.google.com/gke-nodepool=$1" -o json \
    | jq '[.items[] | select(any(.status.conditions[]; .type == "Ready" and .status == "True"))] | length'
}

# Names of namespaces that carry a live test-run label.
test_namespaces() {
  kubectl get namespaces -l "$LABEL_RUN" -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}'
}
