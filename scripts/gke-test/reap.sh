#!/usr/bin/env bash
set -euo pipefail

# Delete test namespaces whose faberline.dev/expires time has passed, then
# park. A run normally deletes its own namespace; this catches the runs that
# died first (runner lost, job cancelled), so their PVCs and disks stop costing
# money. Run by .github/workflows/gke-test-reaper.yml every 30 minutes.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"
: "${KUBECONFIG:?KUBECONFIG must point at a task-local file}"

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
  kubectl delete namespace "$ns" --wait=true --timeout=300s >&2 || log "delete of $ns did not finish; next run retries"
done

"$SCRIPT_DIR/park.sh"
