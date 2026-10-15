#!/usr/bin/env bash
set -euo pipefail

# Deploy one repository's kustomize overlay into its own namespace on the
# shared GKE cluster, run its test command, and always tear the namespace
# down. Called by .github/workflows/gke-test.yml from the caller's checkout.
#
# Required environment:
#   KUBECONFIG     task-local kubeconfig with credentials for the cluster
#   REPO           repository name; also the node pool (pool-<REPO>)
#   OVERLAY        path of the kustomize overlay, relative to the checkout
#   IMAGE          image to deploy, for example ghcr.io/faberline/keep:sha-<git>
#   TEST_COMMAND   bash command; runs in the checkout after every rollout is done
#   EVIDENCE_DIR   folder for the rendered manifests, describe output, and logs
# Optional:
#   NAMESPACE      default REPO; must match the overlay's Namespace object
#   IMAGE_NAME     image name in the overlay to replace; default REPO
#   RUN_LABEL      value of faberline.dev/test-run; default local-<time>
#   TTL_MINUTES    job timeout; the reaper may delete the namespace 15 minutes
#                  after it; default 90
#   GHCR_USER, GHCR_TOKEN   when set, create the ghcr-pull imagePullSecret
#   ROLLOUT_TIMEOUT         default 900s; includes scaling the pool up from 0

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"

: "${KUBECONFIG:?KUBECONFIG must point at a task-local file}"
: "${REPO:?}" "${OVERLAY:?}" "${IMAGE:?}" "${TEST_COMMAND:?}" "${EVIDENCE_DIR:?}"
NAMESPACE="${NAMESPACE:-$REPO}"
IMAGE_NAME="${IMAGE_NAME:-$REPO}"
RUN_LABEL="${RUN_LABEL:-local-$(date +%s)}"
TTL_MINUTES="${TTL_MINUTES:-90}"
ROLLOUT_TIMEOUT="${ROLLOUT_TIMEOUT:-900s}"
export KUBECONFIG REPO NAMESPACE IMAGE EVIDENCE_DIR RUN_LABEL

mkdir -p "$EVIDENCE_DIR"
rendered="$EVIDENCE_DIR/rendered.yaml"

# --- Render and check, before any cluster time is spent ----------------------

[[ -f "$OVERLAY/kustomization.yaml" ]] || { log "no kustomization.yaml in $OVERLAY"; exit 1; }
(cd "$OVERLAY" && kustomize edit set image "$IMAGE_NAME=$IMAGE")
kustomize build "$OVERLAY" >"$rendered"

if grep -n 'REPLACE_ME' "$rendered" >&2; then
  log "rendered manifests still contain REPLACE_ME"
  exit 1
fi

# One JSON document per manifest, for jq.
docs="$EVIDENCE_DIR/rendered.json"
yq -o=json -I=0 '.' "$rendered" | jq -s 'map(select(. != null))' >"$docs"

problems="$(jq -r --arg pool "$LABEL_POOL" --arg repo "$REPO" --arg ns "$NAMESPACE" '
  def pod_spec:
    if .kind == "Pod" then .spec
    elif .kind == "CronJob" then .spec.jobTemplate.spec.template.spec
    elif (.kind | IN("Deployment","StatefulSet","DaemonSet","ReplicaSet","Job")) then .spec.template.spec
    else empty end;
  def where: "\(.kind)/\(.metadata.name)";
  (.[] | select(.kind == "Namespace" and .metadata.name != $ns)
       | "\(where): the overlay namespace must be \($ns)"),
  (.[] | . as $o | pod_spec as $s | ($o | where) as $w |
     (if ($s.nodeSelector[$pool] // "") != $repo
        then "\($w): nodeSelector \($pool): \($repo) is missing" else empty end),
     (if any($s.tolerations[]?; .key == $pool and .value == $repo and .effect == "NoSchedule") | not
        then "\($w): toleration \($pool)=\($repo):NoSchedule is missing" else empty end),
     (if any($s.imagePullSecrets[]?; .name == "ghcr-pull") | not
        then "\($w): imagePullSecrets ghcr-pull is missing" else empty end),
     ((($s.containers // []) + ($s.initContainers // []))[]
       | select((.resources.requests.cpu // null) == null or (.resources.requests.memory // null) == null)
       | "\($w): container \(.name) needs resources.requests cpu and memory"))
' "$docs")"
if [[ -n "$problems" ]]; then
  log "the overlay is not ready for the shared cluster (see docs/standards/test-cluster.md in aw):"
  sed 's/^/  /' <<<"$problems" >&2
  exit 1
fi

# --- From here on, always clean up -------------------------------------------

collect_evidence() {
  set +e
  kubectl get nodes -L "$LABEL_POOL" -o wide >"$EVIDENCE_DIR/nodes.txt" 2>&1
  kubectl -n "$NAMESPACE" get all,pvc,configmap -o wide >"$EVIDENCE_DIR/resources.txt" 2>&1
  kubectl -n "$NAMESPACE" get events --sort-by=.lastTimestamp >"$EVIDENCE_DIR/events.txt" 2>&1
  kubectl -n "$NAMESPACE" describe pods >"$EVIDENCE_DIR/pods-describe.txt" 2>&1
  local pod
  for pod in $(kubectl -n "$NAMESPACE" get pods -o name 2>/dev/null); do
    kubectl -n "$NAMESPACE" logs "$pod" --all-containers --prefix --timestamps \
      >"$EVIDENCE_DIR/logs-${pod#pod/}.txt" 2>&1
    kubectl -n "$NAMESPACE" logs "$pod" --all-containers --prefix --timestamps --previous \
      >"$EVIDENCE_DIR/logs-${pod#pod/}-previous.txt" 2>/dev/null \
      || rm -f "$EVIDENCE_DIR/logs-${pod#pod/}-previous.txt"
  done
  set -e
}

on_exit() {
  local status=$?
  trap - EXIT
  log "collecting evidence into $EVIDENCE_DIR"
  collect_evidence
  "$SCRIPT_DIR/teardown.sh" || status=1
  exit "$status"
}

# Same-repository runs are serialized by the workflow's concurrency group, so
# a namespace that is already here is the leftover of a run that died.
if kubectl get namespace "$NAMESPACE" >/dev/null 2>&1; then
  log "namespace $NAMESPACE is a leftover; deleting it first"
  RENDERED="" "$SCRIPT_DIR/teardown.sh"
fi

trap on_exit EXIT
# 15 minutes of grace, so the reaper deletes only after the job has ended.
expires=$(($(date +%s) + (TTL_MINUTES + 15) * 60))
kubectl create namespace "$NAMESPACE" >&2
kubectl label namespace "$NAMESPACE" "$LABEL_RUN=$RUN_LABEL" "$LABEL_EXPIRES=$expires" >&2
log "namespace $NAMESPACE labeled $LABEL_RUN=$RUN_LABEL, expires $(date -u -d "@$expires" +%FT%TZ 2>/dev/null || echo "$expires")"

"$SCRIPT_DIR/wake.sh"

if [[ -n "${GHCR_TOKEN:-}" ]]; then
  kubectl -n "$NAMESPACE" create secret docker-registry ghcr-pull \
    --docker-server=ghcr.io --docker-username="${GHCR_USER:?}" --docker-password="$GHCR_TOKEN" >&2
fi

# CRDs first, so the custom resources in the same overlay find their kind.
yq 'select(.kind == "CustomResourceDefinition")' "$rendered" >"$EVIDENCE_DIR/crds.yaml"
yq 'select(.kind != "CustomResourceDefinition")' "$rendered" >"$EVIDENCE_DIR/rest.yaml"
if [[ -s "$EVIDENCE_DIR/crds.yaml" ]] && grep -q '^kind:' "$EVIDENCE_DIR/crds.yaml"; then
  kubectl apply --server-side --force-conflicts --field-manager=gke-test -f "$EVIDENCE_DIR/crds.yaml" >&2
  kubectl wait --for=condition=Established --timeout=120s -f "$EVIDENCE_DIR/crds.yaml" >&2
fi
# Server-side apply: large CRDs and ConfigMaps overflow the client-side
# last-applied annotation.
kubectl apply --server-side --force-conflicts --field-manager=gke-test -f "$EVIDENCE_DIR/rest.yaml" >&2

for workload in $(kubectl -n "$NAMESPACE" get deployment,statefulset,daemonset -o name); do
  log "waiting for $workload"
  kubectl -n "$NAMESPACE" rollout status "$workload" --timeout="$ROLLOUT_TIMEOUT" >&2
done

log "running test command"
bash -euo pipefail -c "$TEST_COMMAND"
log "test command passed"
