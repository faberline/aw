#!/usr/bin/env bash
set -euo pipefail

# The queue of the shared cluster. Runs of one repository share its namespace
# and its cluster-wide CRDs, so only one may run at a time. A GitHub
# `concurrency` group cannot do this: it keeps one waiting job and cancels the
# others (aw#293). So each run takes a Kubernetes Lease in the cluster and
# waits while another run holds it. Nothing is cancelled.
#
#   lock.sh acquire   wait for the Lease and take it; renew it when this run
#                     already holds it. Fails after QUEUE_MINUTES.
#   lock.sh renew     renew the Lease when this run holds it; fail otherwise
#   lock.sh release   delete the Lease when this run holds it; else do nothing
#
# The Lease is gke-test-<repo>, or gke-test-<repo>-lane-<lane> for a lane run,
# in the namespace gke-test-locks. That namespace has no test-run label, so
# the reaper and the parker ignore it.
#
# A run that dies without `release` (a lost runner) leaves its Lease. The
# Lease expires TTL_MINUTES plus 15 minutes after its last renewal, the same
# grace as the reaper, and then the next run takes it over.
#
# Environment:
#   KUBECONFIG     task-local kubeconfig with credentials for the cluster
#   REPO           repository name
#   LOCK_HOLDER    unique name of this run, for example gha-<run>-<attempt>-<random>
# Optional:
#   LANE           as in run.sh
#   TTL_MINUTES    default 90
#   QUEUE_MINUTES  longest wait in `acquire`; default 120
#   LOCK_NAMESPACE     default gke-test-locks
#   LOCK_POLL_SECONDS  default 20; each wait adds 0-9 s of jitter
#   LOCK_SLEEP         replaces `sleep` in tests

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"

: "${KUBECONFIG:?KUBECONFIG must point at a task-local file}"
: "${REPO:?}" "${LOCK_HOLDER:?}"
LANE="${LANE:-}"
TTL_MINUTES="${TTL_MINUTES:-90}"
QUEUE_MINUTES="${QUEUE_MINUTES:-120}"
LOCK_NAMESPACE="${LOCK_NAMESPACE:-gke-test-locks}"
LOCK_POLL_SECONDS="${LOCK_POLL_SECONDS:-20}"

# A Lease name is a DNS name: lower case letters, digits, and hyphens. Two
# keys that become the same name only wait for each other.
lease="$(tr '[:upper:]' '[:lower:]' <<<"gke-test-$REPO${LANE:+-lane-$LANE}" \
  | sed -E 's/[^a-z0-9-]+/-/g; s/-+$//' | cut -c1-63)"
duration=$(( (TTL_MINUTES + 15) * 60 ))

kc() { kubectl --request-timeout=30s -n "$LOCK_NAMESPACE" "$@"; }

# The Lease as this run wants it. $1 is the resourceVersion to replace, or
# empty for a new Lease. The API server refuses a replace when the Lease
# changed after that version, so two runs never both take a Lease.
manifest() {
  jq -n --arg name "$lease" --arg ns "$LOCK_NAMESPACE" --arg holder "$LOCK_HOLDER" \
    --arg rv "$1" --argjson duration "$duration" '
    (now | strftime("%Y-%m-%dT%H:%M:%S.000000Z")) as $t |
    {apiVersion: "coordination.k8s.io/v1", kind: "Lease",
     metadata: ({name: $name, namespace: $ns} + (if $rv == "" then {} else {resourceVersion: $rv} end)),
     spec: {holderIdentity: $holder, leaseDurationSeconds: $duration, acquireTime: $t, renewTime: $t}}'
}

# Prints the Lease as JSON; prints nothing when there is none. Fails on any
# other error.
read_lease() {
  local out
  if out="$(kc get lease "$lease" -o json 2>&1)"; then
    printf '%s\n' "$out"
  elif grep -q 'NotFound' <<<"$out"; then
    return 0
  else
    printf '%s\n' "$out" >&2
    return 1
  fi
}

holder_of() { jq -r '.spec.holderIdentity // ""' <<<"$1"; }

# Seconds until the Lease expires; 0 or less means it is stale. A Lease
# without times is stale.
seconds_left() {
  jq -r '((.spec.renewTime // .spec.acquireTime // "1970-01-01T00:00:00Z")
          | sub("\\.[0-9]+Z$"; "Z") | fromdateiso8601)
         + (.spec.leaseDurationSeconds // 0) - (now | floor)' <<<"$1"
}

acquire() {
  local start=$SECONDS budget=$((QUEUE_MINUTES * 60)) errors=0 polls=0
  local cur out holder left delay
  out="$(kubectl --request-timeout=30s create namespace "$LOCK_NAMESPACE" 2>&1)" \
    || grep -q 'AlreadyExists' <<<"$out" \
    || { printf '%s\n' "$out" >&2; log "cannot create namespace $LOCK_NAMESPACE"; return 1; }
  while :; do
    if cur="$(read_lease)"; then
      if [[ -z "$cur" ]]; then
        if out="$(manifest "" | kc create -f - 2>&1)"; then
          log "took lease $lease as $LOCK_HOLDER"
          return 0
        fi
        # AlreadyExists: another run was faster. Read the Lease again.
        grep -q 'AlreadyExists' <<<"$out" || { printf '%s\n' "$out" >&2; errors=$((errors + 1)); }
      else
        holder="$(holder_of "$cur")"
        left="$(seconds_left "$cur")"
        if [[ "$holder" == "$LOCK_HOLDER" || "$left" -le 0 ]]; then
          if out="$(manifest "$(jq -r '.metadata.resourceVersion' <<<"$cur")" | kc replace -f - 2>&1)"; then
            if [[ "$holder" == "$LOCK_HOLDER" ]]; then
              log "renewed lease $lease"
            else
              log "took over lease $lease: its holder $holder did not renew or release it in time"
            fi
            return 0
          fi
          # Conflict or NotFound: the Lease changed meanwhile. Read it again.
          grep -Eq 'Conflict|NotFound|the object has been modified' <<<"$out" \
            || { printf '%s\n' "$out" >&2; errors=$((errors + 1)); }
        elif (( polls % 15 == 0 )); then
          log "waiting for lease $lease; $holder holds it (expires in ${left}s when not released)"
        fi
      fi
    else
      errors=$((errors + 1))
    fi
    # Never run without the Lease: an API server that does not answer fails
    # the run.
    if (( errors >= 5 )); then
      log "lease $lease: the cluster refused 5 requests"
      return 1
    fi
    delay=$(( LOCK_POLL_SECONDS + RANDOM % 10 ))
    if (( SECONDS - start + delay > budget )); then
      log "lease $lease: still waiting after ${QUEUE_MINUTES} minutes (QUEUE_MINUTES); other runs of $REPO hold the cluster"
      return 1
    fi
    polls=$((polls + 1))
    ${LOCK_SLEEP:-sleep} "$delay"
  done
}

holds() {
  local cur
  cur="$(read_lease)" || return 1
  [[ -n "$cur" && "$(holder_of "$cur")" == "$LOCK_HOLDER" ]]
}

# Unlike `acquire`, never waits: a run that lost its Lease must stop.
renew() {
  local cur
  cur="$(read_lease)" || return 1
  if [[ -z "$cur" || "$(holder_of "$cur")" != "$LOCK_HOLDER" ]]; then
    log "lease $lease is not held by $LOCK_HOLDER"
    return 1
  fi
  manifest "$(jq -r '.metadata.resourceVersion' <<<"$cur")" | kc replace -f - >&2
}

release() {
  if holds; then
    kc delete lease "$lease" --ignore-not-found >&2
    log "released lease $lease"
  else
    log "lease $lease is not held by $LOCK_HOLDER; nothing to release"
  fi
}

case "${1:-}" in
  acquire) acquire ;;
  renew) renew ;;
  release) release ;;
  *) log "usage: lock.sh acquire|renew|release"; exit 2 ;;
esac
