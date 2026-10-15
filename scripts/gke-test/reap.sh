#!/usr/bin/env bash
set -euo pipefail

# The safety net under every test run, so nothing keeps costing money after a
# run that died first (runner lost, job cancelled, teardown failed). Run by
# .github/workflows/gke-test-reaper.yml every 30 minutes.
#
# 1. Delete test namespaces whose faberline.dev/expires time has passed.
# 2. Park system-pool (park.sh).
# 3. Delete orphan PVC disks: GKE made them for a PersistentVolume, no VM
#    uses them, they are older than 1 hour, and no PersistentVolume names them.
# 4. Report pods outside test and system namespaces. They can keep a node up,
#    but they may be someone's manual work, so the reaper does not delete them.
#
# Exits 1 when step 1 or 3 could not finish, or when step 4 found a pod. The
# workflow then opens an issue on faberline/aw.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"
: "${KUBECONFIG:?KUBECONFIG must point at a task-local file}"

problems=0

# --- 1. Expired test namespaces -----------------------------------------------

now="$(date +%s)"
expired="$(
  kubectl get namespaces -l "$LABEL_RUN" -o json | jq -r \
    --arg key "$LABEL_EXPIRES" --argjson now "$now" '
      .items[]
      | select(.status.phase != "Terminating")
      | (.metadata.labels[$key] // "0") as $exp
      # A missing or unreadable expiry counts as expired: nothing should hold
      # a test-run label without a deadline.
      | select(($exp | tonumber? // 0) < $now)
      | .metadata.name'
)"

for ns in $expired; do
  log "deleting expired test namespace $ns"
  if ! kubectl delete namespace "$ns" --wait=true --timeout=300s >&2; then
    log "delete of $ns did not finish; the next run retries"
    problems=$((problems + 1))
  fi
done

# --- 2. Park ------------------------------------------------------------------

"$SCRIPT_DIR/park.sh"

# --- 3. Orphan PVC disks ------------------------------------------------------

# Disks that a PersistentVolume still names. A pod that moves between nodes
# leaves its disk unattached for a moment; this list keeps such a disk safe.
# If kubectl fails, set -e stops here, before any disk is deleted.
pv_disks="$(
  kubectl get persistentvolumes -o json | jq -r '
    .items[]
    | (.spec.csi.volumeHandle // .spec.gcePersistentDisk.pdName // empty)
    | split("/") | last'
)"

candidates="$(
  gcloud compute disks list --project="$GKE_PROJECT" \
    --filter='name~^pvc- AND -users:* AND description:kubernetes.io/created-for AND creationTimestamp<-PT1H' \
    --format='value(name,zone.basename())'
)"

while read -r disk zone; do
  [[ -n "$disk" ]] || continue
  if grep -qxF "$disk" <<<"$pv_disks"; then
    log "keeping unattached disk $disk: a PersistentVolume still names it"
    continue
  fi
  log "deleting orphan disk $disk in $zone"
  if ! gcloud compute disks delete "$disk" --project="$GKE_PROJECT" --zone="$zone" --quiet >&2; then
    log "delete of disk $disk failed"
    problems=$((problems + 1))
  fi
done <<<"$candidates"

# --- 4. Pods outside test and system namespaces -------------------------------

strays="$(
  kubectl get pods --all-namespaces -o json | jq -r \
    --arg live "$(test_namespaces)" '
      ($live | split("\n")) as $tests
      | .items[]
      | .metadata.namespace as $ns
      | select(($tests | index($ns)) | not)
      | select($ns | test("^(kube-|gke-|gmp-)") | not)
      | select(.status.phase != "Succeeded" and .status.phase != "Failed")
      | "\($ns)/\(.metadata.name) on \(.spec.nodeName // "no node")"'
)"

if [[ -n "$strays" ]]; then
  log "pods outside any test namespace; delete them or label their namespace:"
  sed "s/^/  /" <<<"$strays" >&2
  problems=$((problems + 1))
fi

if ((problems > 0)); then
  log "$problems problem(s) need a person"
  exit 1
fi
log "clean"
