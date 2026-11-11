#!/usr/bin/env bash
set -euo pipefail

# Tests for resize_pool (common.sh) and blockers.sh. No cluster needed: gcloud
# is a fake on PATH. Run: scripts/gke-test/tests/run-tests.sh

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GKE="$HERE/.."
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
fails=0
pass() { printf 'ok   %s\n' "$1"; }
fail() { printf 'FAIL %s\n' "$1"; fails=$((fails + 1)); }
check() { # check <name> <command...>
  local name="$1"
  shift
  if "$@"; then pass "$name"; else fail "$name"; fi
}

# --- resize_pool ---------------------------------------------------------------

mkdir -p "$TMP/bin"
# The fake reads FAKE_RESIZE_OUTCOMES (space list: busy|ok|denied), one per
# resize call, and counts calls in $TMP/resizes. FAKE_AT_SIZE=1 makes the pool
# look already at size.
cat >"$TMP/bin/gcloud" <<'FAKE'
#!/usr/bin/env bash
case "$*" in
  "container operations list"*) exit 0 ;;
  "container node-pools describe"*) [[ "${FAKE_AT_SIZE:-}" == 1 ]] && echo group-url; exit 0 ;;
  "compute instance-groups managed describe"*) echo 1; exit 0 ;;
  "container clusters resize"*)
    n=$(( $(cat "$TMP/resizes" 2>/dev/null || echo 0) + 1 ))
    echo "$n" >"$TMP/resizes"
    outcome="$(tr ' ' '\n' <<<"$FAKE_RESIZE_OUTCOMES" | sed -n "${n}p")"
    case "$outcome" in
      ok) exit 0 ;;
      busy) echo "ERROR: (gcloud.container.clusters.resize) FAILED_PRECONDITION: Cluster is running incompatible operation operation-123." >&2; exit 1 ;;
      *) echo "ERROR: PERMISSION_DENIED" >&2; exit 1 ;;
    esac ;;
esac
exit 0
FAKE
chmod +x "$TMP/bin/gcloud"

run_resize() { # run_resize <outcomes> [budget]; the status is resize_pool's
  rm -f "$TMP/resizes"
  (
    export PATH="$TMP/bin:$PATH" TMP FAKE_RESIZE_OUTCOMES="$1" RESIZE_RETRY_SECONDS="${2:-900}"
    # shellcheck source=../common.sh
    source "$GKE/common.sh"
    # shellcheck disable=SC2329
    fake_sleep() { SECONDS=$((SECONDS + $1)); }
    # shellcheck disable=SC2034
    RETRY_SLEEP=fake_sleep
    resize_pool system-pool 1 >/dev/null 2>&1
  )
}
calls() { cat "$TMP/resizes" 2>/dev/null || echo 0; }

if run_resize "ok" && [[ "$(calls)" == 1 ]]; then pass "resize: first try ok"; else fail "resize: first try ok"; fi
if run_resize "busy busy busy ok" && [[ "$(calls)" == 4 ]]; then
  pass "resize: retries a busy cluster"
else
  fail "resize: retries a busy cluster"
fi
if run_resize "busy busy busy busy busy busy busy busy busy busy busy busy busy busy busy busy busy busy busy busy" 120; then
  fail "resize: gives up after the budget"
elif [[ "$(calls)" -ge 2 && "$(calls)" -lt 20 ]]; then
  pass "resize: gives up after the budget"
else
  fail "resize: gives up after the budget"
fi
if run_resize "denied denied denied denied ok"; then
  fail "resize: other errors stop after 3 attempts"
elif [[ "$(calls)" == 3 ]]; then
  pass "resize: other errors stop after 3 attempts"
else
  fail "resize: other errors stop after 3 attempts"
fi
if FAKE_AT_SIZE=1 run_resize "busy" && [[ "$(calls)" == 0 ]]; then
  pass "resize: skips when already at size"
else
  fail "resize: skips when already at size"
fi

# --- blockers.sh -----------------------------------------------------------------

NOW=1800000000 # 2027-01-15T08:00:00Z
old_ts="2027-01-15T07:50:00Z" # 600 s before NOW
new_ts="2027-01-15T07:59:30Z" # 30 s before NOW
echo '{"items":[]}' >"$TMP/empty.json"

pod() { # pod <name> <phase> <node> <created> [annotations-json]
  jq -n --arg n "$1" --arg ph "$2" --arg node "$3" --arg c "$4" --argjson a "${5:-"{}"}" \
    '{metadata:{name:$n,creationTimestamp:$c,annotations:$a},spec:{nodeName:$node},status:{phase:$ph}}'
}
event() { # event <kind> <name> <reason> <message>
  jq -n --arg k "$1" --arg n "$2" --arg r "$3" --arg m "$4" \
    '{involvedObject:{kind:$k,name:$n},reason:$r,message:$m,lastTimestamp:"2027-01-15T07:59:00Z"}'
}
pvc() { jq -n --arg n "$1" --arg ph "$2" --arg c "$3" '{metadata:{name:$n,creationTimestamp:$c},status:{phase:$ph}}'; }
items() { jq -s '{items: .}'; }

blockers() { # blockers <events> <pods> <pvcs>
  "$GKE/blockers.sh" "$1" "$2" "$3" 120 "$NOW"
}
has() { grep -q "$1"; }
quiet() { [[ -z "$(blockers "$@")" ]]; }

# Signal 1
event PersistentVolumeClaim data-0 ProvisioningFailed "InvalidArgument infeasible: Disk size cannot be smaller than 4 GB" | items >"$TMP/e1.json"
pvc data-0 Pending "$old_ts" | items >"$TMP/c1.json"
check "blockers: infeasible PVC" has infeasible < <(blockers "$TMP/e1.json" "$TMP/empty.json" "$TMP/c1.json")
pvc data-0 Pending "$new_ts" | items >"$TMP/c1new.json"
check "blockers: infeasible PVC inside the grace time is not a blocker" quiet "$TMP/e1.json" "$TMP/empty.json" "$TMP/c1new.json"
pvc data-0 Bound "$old_ts" | items >"$TMP/c1b.json"
check "blockers: a Bound PVC is not a blocker" quiet "$TMP/e1.json" "$TMP/empty.json" "$TMP/c1b.json"
event PersistentVolumeClaim data-0 ProvisioningFailed "quota exceeded, retrying" | items >"$TMP/e1x.json"
check "blockers: a retryable PVC error is not a blocker" quiet "$TMP/e1x.json" "$TMP/empty.json" "$TMP/c1.json"

# Signal 2
pod web-0 Pending "" "$old_ts" | items >"$TMP/p.json"
event Pod web-0 NotTriggerScaleUp "pod didn't trigger scale-up: node affinity" | items >"$TMP/e2.json"
check "blockers: NotTriggerScaleUp" has NotTriggerScaleUp < <(blockers "$TMP/e2.json" "$TMP/p.json" "$TMP/empty.json")
{ event Pod web-0 NotTriggerScaleUp "x"; event Pod web-0 TriggeredScaleUp "pod triggered scale-up"; } | items >"$TMP/e2b.json"
check "blockers: NotTriggerScaleUp with TriggeredScaleUp is a normal wait" quiet "$TMP/e2b.json" "$TMP/p.json" "$TMP/empty.json"
pod web-0 Pending "" "$old_ts" '{"cluster_autoscaler_unhelpable_until":"Inf"}' | items >"$TMP/p2.json"
check "blockers: unhelpable annotation" has unhelpable < <(blockers "$TMP/empty.json" "$TMP/p2.json" "$TMP/empty.json")
pod web-0 Pending "" "$old_ts" '{"cluster_autoscaler_unhelpable_until":"2027-01-15T08:05:00Z"}' | items >"$TMP/p2b.json"
check "blockers: a timed unhelpable backoff is not a blocker" quiet "$TMP/empty.json" "$TMP/p2b.json" "$TMP/empty.json"

# Signal 3
event Pod web-0 FailedScheduling "0/1 nodes are available: 1 Insufficient cpu." | items >"$TMP/e3.json"
check "blockers: FailedScheduling without scale-up" has FailedScheduling < <(blockers "$TMP/e3.json" "$TMP/p.json" "$TMP/empty.json")
{ event Pod web-0 FailedScheduling "x"; event Pod web-0 TriggeredScaleUp "pod triggered scale-up"; } | items >"$TMP/e3b.json"
check "blockers: FailedScheduling then TriggeredScaleUp is a normal scale-up" quiet "$TMP/e3b.json" "$TMP/p.json" "$TMP/empty.json"
pod web-0 Pending "" "$new_ts" | items >"$TMP/pnew.json"
check "blockers: FailedScheduling inside the grace time is not a blocker" quiet "$TMP/e3.json" "$TMP/pnew.json" "$TMP/empty.json"
pod web-0 Running node-1 "$old_ts" | items >"$TMP/prun.json"
# Several Pending pods; only web-0 got TriggeredScaleUp. web-1 has none.
{ pod web-0 Pending "" "$old_ts"; pod web-1 Pending "" "$old_ts"; } | items >"$TMP/p2pods.json"
{ event Pod web-1 FailedScheduling "x"; event Pod web-0 TriggeredScaleUp "pod triggered scale-up"; } | jq -c '.' | items >"$TMP/e3c.json"
check "blockers: a TriggeredScaleUp for another pod covers the namespace" quiet "$TMP/e3c.json" "$TMP/p2pods.json" "$TMP/empty.json"
{ event Pod web-1 NotTriggerScaleUp "x"; event Pod web-0 TriggeredScaleUp "pod triggered scale-up"; } | items >"$TMP/e2c.json"
check "blockers: NotTriggerScaleUp is covered by a scale-up of another pod" quiet "$TMP/e2c.json" "$TMP/p2pods.json" "$TMP/empty.json"
# An old TriggeredScaleUp (over 600 s) no longer covers.
{ event Pod web-1 FailedScheduling "x"; event Pod web-0 TriggeredScaleUp "y" | jq '.lastTimestamp="2027-01-15T07:40:00Z"'; } | items >"$TMP/e3d.json"
check "blockers: a TriggeredScaleUp older than 600 s does not cover" has FailedScheduling < <(blockers "$TMP/e3d.json" "$TMP/p2pods.json" "$TMP/empty.json")
# The Inf annotation ignores the scale-up cover.
pod web-1 Pending "" "$old_ts" '{"cluster_autoscaler_unhelpable_until":"Inf"}' | items >"$TMP/p2inf.json"
check "blockers: unhelpable=Inf is a blocker even during a scale-up" has unhelpable < <(blockers "$TMP/e3c.json" "$TMP/p2inf.json" "$TMP/empty.json")
# FailedScheduling uses the longer grace time: 200 s old is not enough, 400 s is.
pod web-0 Pending "" "2027-01-15T07:56:40Z" | items >"$TMP/p200.json"
check "blockers: FailedScheduling at 200 s is inside the scheduling grace" quiet "$TMP/e3.json" "$TMP/p200.json" "$TMP/empty.json"
pod web-0 Pending "" "2027-01-15T07:53:20Z" | items >"$TMP/p400.json"
check "blockers: FailedScheduling at 400 s is a blocker" has FailedScheduling < <(blockers "$TMP/e3.json" "$TMP/p400.json" "$TMP/empty.json")
check "blockers: a placed pod is not a blocker" quiet "$TMP/e3.json" "$TMP/prun.json" "$TMP/empty.json"
check "blockers: empty namespace" quiet "$TMP/empty.json" "$TMP/empty.json" "$TMP/empty.json"

# --- check-cr-storage.sh -------------------------------------------------------
# A custom resource whose operator renders the PVC (aw#265). The pool n4 takes
# only Hyperdisk; e2 does not.
cat >"$TMP/cr-config.yaml" <<'YAML'
crs:
  - repo: tape
    kind: Tape
    storage_class: [spec, storageClass]
    size: [spec, storage]
    pool: [spec, placement, nodeSelector, faberline.dev/pool]
YAML
CR_POOLS='{"tape-server":{"machine_type":"n4-standard-2"},"tape-old":{"machine_type":"e2-standard-4"}}'
HD_RE='^(n4|n4a|n4d|c4|c4a|c4d)-'
cr_json() { # cr_json <kind> <pool> <spec fields as jq object text>
  jq -n --arg kind "$1" --arg pool "$2" "[{kind: \$kind, metadata: {name: \"tape\"},
    spec: ({placement: {nodeSelector: {\"faberline.dev/pool\": \$pool}}} + $3)}]"
}
cr_check() { # cr_check <file> [repo]
  "$GKE/check-cr-storage.sh" "$1" "$TMP/cr-config.yaml" "${2:-tape}" "$CR_POOLS" "$HD_RE"
}
cr_json Tape tape-server '{storage: "1Gi"}' >"$TMP/cr-bad.json"
cr_json Tape tape-server '{storage: "4Gi", storageClass: "dynamic-rwo"}' >"$TMP/cr-ok.json"
cr_json Tape tape-server '{storage: "4G", storageClass: "dynamic-rwo"}' >"$TMP/cr-4g.json"
cr_json Tape tape-server '{storage: "1Gi", storageClass: "dynamic-rwo"}' >"$TMP/cr-small.json"
cr_json Tape tape-server '{storage: "4Gi", storageClass: "standard-rwo"}' >"$TMP/cr-class.json"
cr_json Tape tape-server '{storageClass: "dynamic-rwo"}' >"$TMP/cr-nosize.json"
cr_json Tape tape-old '{storage: "1Gi"}' >"$TMP/cr-pd.json"
cr_json Other tape-server '{storage: "1Gi"}' >"$TMP/cr-other.json"
cr_has() { grep -q "$1"; }
check "cr-storage: 1Gi and no storage class fails on both" test "$(cr_check "$TMP/cr-bad.json" | wc -l)" -eq 2
check "cr-storage: names the missing storage class" cr_has "not set" < <(cr_check "$TMP/cr-bad.json")
check "cr-storage: names the small size" cr_has "at least 4Gi" < <(cr_check "$TMP/cr-bad.json")
check "cr-storage: 4Gi with dynamic-rwo passes" test -z "$(cr_check "$TMP/cr-ok.json")"
check "cr-storage: 4G (below 4Gi) fails" cr_has "at least 4Gi" < <(cr_check "$TMP/cr-4g.json")
check "cr-storage: dynamic-rwo with 1Gi fails on size only" test "$(cr_check "$TMP/cr-small.json" | wc -l)" -eq 1
check "cr-storage: standard-rwo fails" cr_has "standard-rwo\|set spec.storageClass" < <(cr_check "$TMP/cr-class.json")
check "cr-storage: a missing size fails" cr_has "not a size" < <(cr_check "$TMP/cr-nosize.json")
check "cr-storage: a non-Hyperdisk pool is not checked" test -z "$(cr_check "$TMP/cr-pd.json")"
check "cr-storage: another kind is not checked" test -z "$(cr_check "$TMP/cr-other.json")"
check "cr-storage: another repository is not checked" test -z "$(cr_check "$TMP/cr-bad.json" keep)"
check "cr-storage: the tape row of infra/gke-test/cr-storage.yaml works" \
  test "$("$GKE/check-cr-storage.sh" "$TMP/cr-bad.json" "$GKE/../../infra/gke-test/cr-storage.yaml" tape "$CR_POOLS" "$HD_RE" | wc -l)" -eq 2

if ((fails > 0)); then
  printf '%s test(s) failed\n' "$fails" >&2
  exit 1
fi
echo "all tests passed"
