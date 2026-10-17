#!/usr/bin/env bash
set -euo pipefail

# Remove everything one test run deployed. Safe to run more than once, and
# safe when the run died before it deployed anything.
#
# Order matters for operators: custom resources go first, while the operator
# that owns their finalizers still runs; the namespace next; CRDs last.
#
# Environment: KUBECONFIG, NAMESPACE, and EVIDENCE_DIR as in run.sh.
# RENDERED overrides the manifest file; empty means "delete the namespace only".

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"
: "${KUBECONFIG:?KUBECONFIG must point at a task-local file}"
: "${NAMESPACE:?}"
RENDERED="${RENDERED-${EVIDENCE_DIR:-}/rendered.yaml}"
status=0

if [[ -n "$RENDERED" && -s "$RENDERED" ]]; then
  dir="$(dirname "$RENDERED")"
  # kustomize puts custom resources last, so the reverse order deletes them
  # first and the Namespace last.
  yq 'select(.kind != "CustomResourceDefinition")' "$RENDERED" \
    | yq ea '[.] | reverse | .[] | split_doc' >"$dir/teardown-order.yaml"
  log "deleting the run's objects in reverse order"
  kubectl delete -f "$dir/teardown-order.yaml" --ignore-not-found --wait=true --timeout=300s >&2 \
    || { log "some objects did not delete in time"; status=1; }
fi

if kubectl get namespace "$NAMESPACE" >/dev/null 2>&1; then
  log "deleting namespace $NAMESPACE"
  kubectl delete namespace "$NAMESPACE" --wait=true --timeout=600s >&2 \
    || { log "namespace $NAMESPACE did not delete in time; the reaper retries"; status=1; }
fi

# Cluster RBAC that an operator made at run time is not in the manifests.
# Delete it after the namespace, so the operator cannot make it again.
log "deleting cluster RBAC labelled $LABEL_OWNER_NS=$NAMESPACE"
delete_owned_cluster_rbac "$NAMESPACE" || { log "some cluster RBAC did not delete; the reaper retries"; status=1; }

if [[ -n "$RENDERED" && -s "$RENDERED" ]]; then
  crds="$(dirname "$RENDERED")/teardown-crds.yaml"
  yq 'select(.kind == "CustomResourceDefinition")' "$RENDERED" >"$crds"
  if grep -q '^kind:' "$crds"; then
    log "deleting the run's CRDs"
    kubectl delete -f "$crds" --ignore-not-found --wait=true --timeout=300s >&2 \
      || { log "some CRDs did not delete in time"; status=1; }
  fi
fi

# A PV with reclaimPolicy Retain outlives its PVC and keeps a disk billed.
deadline=$((SECONDS + 300))
while :; do
  left="$(kubectl get pv -o json | jq -r --arg ns "$NAMESPACE" \
    '.items[] | select(.spec.claimRef.namespace == $ns) | .metadata.name')"
  [[ -z "$left" ]] && break
  if (( SECONDS >= deadline )); then
    log "persistent volumes of $NAMESPACE are still here:"
    sed 's/^/  /' <<<"$left" >&2
    status=1
    break
  fi
  sleep 10
done

# system-pool stays up: the next call (for example the next test suite)
# needs it soon. The parker (infra/gke-test/park.yaml) parks it 5 to 15
# minutes after the last test namespace is gone.

exit "$status"
