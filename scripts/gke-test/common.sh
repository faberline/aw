# shellcheck shell=bash disable=SC2034
# Shared settings for scripts/gke-test/*.sh. Source it; do not run it.
#
# Every value has a default for the one shared cluster in infra/gke-test/.
# KUBECONFIG must point at a task-local file so a run never rewrites the
# caller's real one, and parallel CI jobs never race on ~/.kube/config.

GKE_PROJECT="${GKE_PROJECT:-axiom-502607}"
GKE_ZONE="${GKE_ZONE:-asia-east1-a}"
GKE_CLUSTER="${GKE_CLUSTER:-faberline-test}"
GKE_SYSTEM_POOL="${GKE_SYSTEM_POOL:-system-pool}"

# Namespace labels. test-run marks a namespace as a live test; expires is the
# Unix time after which the reaper may delete it.
LABEL_RUN="faberline.dev/test-run"
LABEL_EXPIRES="faberline.dev/expires"
LABEL_POOL="faberline.dev/pool"
# Put on a ClusterRole or ClusterRoleBinding that an operator creates at run
# time for one namespace. Teardown and the reaper delete it with that namespace.
LABEL_OWNER_NS="faberline.dev/owner-namespace"

log() { printf '[gke-test] %s\n' "$*" >&2; }

# True when GKE refuses a cluster operation because another one runs.
is_busy_error() {
  grep -Eqi 'FAILED_PRECONDITION|incompatible operation|operation .* in progress|currently running' <<<"$1"
}

# Seconds to wait before retry <attempt>: 15, 30, 60, 60, ... plus 0-9 s of
# jitter, so two jobs that failed together do not retry together.
retry_delay() {
  local d=$(( 15 * (1 << ($1 - 1 > 2 ? 2 : $1 - 1)) ))
  echo $(( d + RANDOM % 10 ))
}

# True when the pool already has <nodes> nodes as its target size and no
# cluster operation runs. Any failed lookup says "no", so the resize runs.
pool_at_size() {
  local pool="$1" nodes="$2" group size running
  running="$(gcloud container operations list --project="$GKE_PROJECT" --zone="$GKE_ZONE" \
    --filter="status=RUNNING AND targetLink~/clusters/$GKE_CLUSTER" --format='value(name)' 2>/dev/null)" \
    || return 1
  [[ -z "$running" ]] || return 1
  group="$(gcloud container node-pools describe "$pool" --cluster="$GKE_CLUSTER" \
    --project="$GKE_PROJECT" --zone="$GKE_ZONE" --format='value(instanceGroupUrls[0])' 2>/dev/null)" \
    || return 1
  [[ -n "$group" ]] || return 1
  size="$(gcloud compute instance-groups managed describe "$group" --format='value(targetSize)' 2>/dev/null)" \
    || return 1
  [[ "$size" == "$nodes" ]]
}

# Resize a node pool. GKE refuses a second cluster operation while one is in
# progress (FAILED_PRECONDITION: Cluster is running incompatible operation).
# Two repositories wake at once, or a park races a wake, and they live in
# different repositories, so no GitHub concurrency group can order them. So
# retry with backoff for up to RESIZE_RETRY_SECONDS (default 900) while the
# error says "busy"; skip the resize when the pool already has the target
# size and the cluster is idle. Any other error gets 3 attempts in all.
# RETRY_SLEEP replaces `sleep` in tests.
resize_pool() {
  local pool="$1" nodes="$2" attempt=0 other=0 out delay
  local budget="${RESIZE_RETRY_SECONDS:-900}" start=$SECONDS
  while :; do
    attempt=$((attempt + 1))
    if pool_at_size "$pool" "$nodes"; then
      log "resize $pool to $nodes: already at that size"
      return 0
    fi
    if out="$(gcloud container clusters resize "$GKE_CLUSTER" \
      --project="$GKE_PROJECT" --zone="$GKE_ZONE" \
      --node-pool="$pool" --num-nodes="$nodes" --quiet 2>&1)"; then
      [[ -z "$out" ]] || printf '%s\n' "$out" >&2
      return 0
    fi
    printf '%s\n' "$out" >&2
    if is_busy_error "$out"; then
      delay="$(retry_delay "$attempt")"
      if (( SECONDS - start + delay > budget )); then
        log "resize $pool to $nodes: cluster still busy after ${budget}s ($attempt attempts)"
        return 1
      fi
      log "resize $pool to $nodes: cluster busy (attempt $attempt); retrying in ${delay}s"
    else
      other=$((other + 1))
      if (( other >= 3 )); then
        log "resize $pool to $nodes failed 3 times"
        return 1
      fi
      delay=30
      log "resize $pool to $nodes failed (attempt $attempt); retrying in ${delay}s"
    fi
    ${RETRY_SLEEP:-sleep} "$delay"
  done
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

# Delete the ClusterRoles and ClusterRoleBindings that carry
# faberline.dev/owner-namespace=<namespace>.
delete_owned_cluster_rbac() {
  kubectl delete clusterrolebindings,clusterroles -l "$LABEL_OWNER_NS=$1" \
    --ignore-not-found --wait=true --timeout=120s >&2
}
