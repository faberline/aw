#!/usr/bin/env bash
set -euo pipefail

# Tests for resize_pool (common.sh), blockers.sh, check-cr-storage.sh,
# lock.sh and batch.sh. No cluster needed: gcloud and kubectl are fakes on PATH. Run: scripts/gke-test/tests/run-tests.sh

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

# --- lock.sh -------------------------------------------------------------------
# The fake kubectl keeps one Lease in $TMP/lease.json and counts versions like
# the API server: `replace` with an old resourceVersion is a Conflict.
# FAKE_KUBECTL_DOWN=1 makes every Lease request fail.
cat >"$TMP/bin/kubectl" <<'FAKE'
#!/usr/bin/env bash
f="$TMP/lease.json"
args=()
while (($#)); do
  case "$1" in
    --request-timeout=*|--ignore-not-found) ;;
    -n|-o|-f) shift ;;
    *) args+=("$1") ;;
  esac
  shift
done
set -- "${args[@]}"
if [[ "$1 $2" == "create namespace" ]]; then
  echo "Error from server (AlreadyExists): namespaces \"$3\" already exists" >&2
  exit 1
fi
if [[ "${FAKE_KUBECTL_DOWN:-}" == 1 ]]; then
  echo "Unable to connect to the server: timeout" >&2
  exit 1
fi
# Store stdin as the Lease, with the next resourceVersion.
store() {
  local rv=$(( $(cat "$TMP/rv" 2>/dev/null || echo 0) + 1 ))
  jq --arg rv "$rv" '.metadata.resourceVersion = $rv' >"$f"
  echo "$rv" >"$TMP/rv"
}
case "$1" in
  get)
    [[ -f "$f" ]] || { echo "Error from server (NotFound): leases.coordination.k8s.io \"$3\" not found" >&2; exit 1; }
    cat "$f" ;;
  create)
    [[ ! -f "$f" ]] || { echo "Error from server (AlreadyExists): leases.coordination.k8s.io already exists" >&2; exit 1; }
    store ;;
  replace)
    in="$(cat)"
    [[ -f "$f" ]] || { echo "Error from server (NotFound): leases.coordination.k8s.io not found" >&2; exit 1; }
    [[ "$(jq -r '.metadata.resourceVersion' <<<"$in")" == "$(jq -r '.metadata.resourceVersion' "$f")" ]] \
      || { echo "Error from server (Conflict): the object has been modified" >&2; exit 1; }
    store <<<"$in" ;;
  delete) rm -f "$f" ;;
esac
FAKE
chmod +x "$TMP/bin/kubectl"

lock() { # lock <holder> <action>; QUEUE_MINUTES, LANE and the FAKE_ values pass through
  env PATH="$TMP/bin:$PATH" TMP="$TMP" KUBECONFIG="$TMP/kubeconfig" REPO=lumen LOCK_HOLDER="$1" \
    LOCK_SLEEP="${LOCK_SLEEP:-true}" "$GKE/lock.sh" "$2" 2>"$TMP/lock.log"
}
lease_holder() { jq -r '.spec.holderIdentity' "$TMP/lease.json" 2>/dev/null; }
no_lease() { rm -f "$TMP/lease.json"; }
# A Lease that <holder> renewed <age> seconds ago, with a 600 s duration.
put_lease() { # put_lease <holder> <age>
  jq -n --arg h "$1" --argjson age "$2" '{metadata: {name: "gke-test-lumen", resourceVersion: "7"},
    spec: {holderIdentity: $h, leaseDurationSeconds: 600,
           renewTime: ((now - $age) | strftime("%Y-%m-%dT%H:%M:%S.000000Z"))}}' >"$TMP/lease.json"
  echo 7 >"$TMP/rv"
}

no_lease
check "lock: a free lease is taken" lock run-a acquire
check "lock: the lease names its holder" test "$(lease_holder)" = run-a
check "lock: the lease lasts TTL_MINUTES plus 15 minutes" \
  test "$(jq -r '.spec.leaseDurationSeconds' "$TMP/lease.json")" -eq 6300
rv_before="$(cat "$TMP/rv")"
check "lock: the holder renews" lock run-a renew
check "lock: a renew writes the lease" test "$(cat "$TMP/rv")" -gt "$rv_before"
if lock run-b renew; then fail "lock: another run cannot renew"; else pass "lock: another run cannot renew"; fi
check "lock: the holder acquires again without a wait" env QUEUE_MINUTES=0 \
  PATH="$TMP/bin:$PATH" TMP="$TMP" KUBECONFIG="$TMP/kubeconfig" REPO=lumen LOCK_HOLDER=run-a "$GKE/lock.sh" acquire 2>/dev/null
# A held lease: the second run waits, then fails at QUEUE_MINUTES. It never
# takes the lease, and the holder keeps it.
if QUEUE_MINUTES=1 lock run-b acquire; then
  fail "lock: a second run waits and times out"
elif [[ "$(lease_holder)" == run-a ]] && grep -q 'still waiting after 1 minutes' "$TMP/lock.log"; then
  pass "lock: a second run waits and times out"
else
  fail "lock: a second run waits and times out"
fi
check "lock: release by another run does nothing" lock run-b release
check "lock: the lease is still held after that" test "$(lease_holder)" = run-a
# The holder releases while the second run waits: the second run gets it.
cat >"$TMP/bin/holder-releases" <<'FAKE'
#!/usr/bin/env bash
rm -f "$TMP/lease.json"
FAKE
chmod +x "$TMP/bin/holder-releases"
check "lock: a waiting run gets the lease after a release" env LOCK_SLEEP=holder-releases \
  PATH="$TMP/bin:$PATH" TMP="$TMP" KUBECONFIG="$TMP/kubeconfig" REPO=lumen LOCK_HOLDER=run-b "$GKE/lock.sh" acquire 2>/dev/null
check "lock: the waiting run is the new holder" test "$(lease_holder)" = run-b
check "lock: the holder releases" lock run-b release
check "lock: a released lease is gone" test ! -f "$TMP/lease.json"
check "lock: release twice is safe" lock run-b release
# A dead run: its lease expired, so the next run takes it over.
put_lease dead-run 700
check "lock: an expired lease is taken over" lock run-c acquire
check "lock: the take-over names the new holder" test "$(lease_holder)" = run-c
put_lease live-run 100
if QUEUE_MINUTES=1 lock run-c acquire; then
  fail "lock: a lease inside its duration is not taken over"
else
  check "lock: a lease inside its duration is not taken over" test "$(lease_holder)" = live-run
fi
# Never run without the lease when the cluster does not answer.
no_lease
if FAKE_KUBECTL_DOWN=1 lock run-d acquire; then
  fail "lock: an API error fails the run"
else
  check "lock: an API error fails the run" grep -q 'refused 5 requests' "$TMP/lock.log"
fi
if FAKE_KUBECTL_DOWN=1 lock run-d renew; then fail "lock: an API error fails a renew"; else pass "lock: an API error fails a renew"; fi
if lock run-d renew; then fail "lock: no lease fails a renew"; else pass "lock: no lease fails a renew"; fi
# A lane has its own lease; the name is a DNS name.
no_lease
check "lock: a lane run takes its own lease" env LANE=Perf_A1 \
  PATH="$TMP/bin:$PATH" TMP="$TMP" KUBECONFIG="$TMP/kubeconfig" REPO=lumen LOCK_HOLDER=run-e "$GKE/lock.sh" acquire 2>/dev/null
check "lock: the lane lease name" test "$(jq -r '.metadata.name' "$TMP/lease.json")" = gke-test-lumen-lane-perf-a1

# The workflow must not bring back a concurrency group for the job: a group
# cancels waiting runs (aw#293).
WF="$GKE/../../.github/workflows/gke-test.yml"
step_index() { # step_index <text in the step's run>
  yq ".jobs.gke-test.steps | to_entries | map(select(.value.run // \"\" | contains(\"$1\"))) | .[0].key" "$WF"
}
check "workflow: no concurrency group" test "$(yq '.jobs.gke-test | has("concurrency")' "$WF")" = false
check "workflow: waits for the lease before run.sh" test "$(step_index 'lock.sh acquire')" -lt "$(step_index 'gke-test/run.sh')"
check "workflow: the release step always runs, and is last" test \
  "$(yq '.jobs.gke-test.steps[-1] | (.if == "always()" and (.run | contains("lock.sh release")))' "$WF")" = true
check "workflow: the backup teardown runs only with the lease" test \
  "$(yq '.jobs.gke-test.steps[] | select(.name == "Tear down (backup)") | .run | contains("lock.sh renew")' "$WF")" = true

# --- batch.sh ------------------------------------------------------------------

export B="$TMP/b"
mkdir -p "$B/bin"
# The fake cluster. It writes every call to $B/calls and every object that
# comes through stdin to $B/stdin-objects. The namespace exists while $B/ns
# does. `get jobs` answers $B/jobs-<n>.json for call n when that file exists,
# else $B/jobs.json.
cat >"$B/bin/kubectl" <<'FAKE'
#!/usr/bin/env bash
echo "kubectl $*" >>"$B/calls"
case "$*" in
  "get namespace "*) [[ -f "$B/ns" ]]; exit ;;
  "create namespace "*) touch "$B/ns" ;;
  "delete namespace "*) rm -f "$B/ns"; echo "$3" >>"$B/deleted" ;;
  "get pv "*) echo '{"items":[]}' ;;
  "get nodes "*) echo '{"items":[{"status":{"conditions":[{"type":"Ready","status":"True"}]}}]}' ;;
  *"create -f -") cat >>"$B/stdin-objects" ;;
  *"get jobs"*)
    n=$(( $(cat "$B/job-calls" 2>/dev/null || echo 0) + 1 ))
    echo "$n" >"$B/job-calls"
    if [[ -f "$B/jobs-$n.json" ]]; then cat "$B/jobs-$n.json"; else cat "$B/jobs.json"; fi ;;
  *"get pods -o json") cat "$B/pods.json" ;;
  *"get events"*) echo "no events" ;;
  *" logs "*) echo "log line of $4" ;;
esac
exit 0
FAKE
cat >"$B/bin/gcloud" <<'FAKE'
#!/usr/bin/env bash
case "$*" in
  "container node-pools list"*) tr ' ' '\n' <<<"${FAKE_POOLS-pool-aw-ci pool-som-client}" ;;
esac
exit 0
FAKE
chmod +x "$B/bin/kubectl" "$B/bin/gcloud"

cat >"$B/jobs.yaml" <<'YAML'
version: 1
image: ghcr.io/faberline/ci-rust:1.99.1
jobs:
  - name: fmt-clippy
    stage: lint
    command: cargo fmt --all -- --check
    timeout: 20m
  - name: unit
    stage: test
    command: |
      cargo test --workspace --locked
    resources: { cpu: 4, memory: 12Gi }
    timeout: 40m
    env: { RUST_BACKTRACE: 1 }
  - name: doc
    stage: test
    command: cargo test --doc
    pool: som-client
    image: ghcr.io/faberline/ci-rust@sha256:0000000000000000000000000000000000000000000000000000000000000000
    timeout: 15m
  - name: image-server
    stage: e2e
    kind: image
    timeout: 30m
YAML
cat >"$B/pools.yaml" <<'YAML'
pools:
  - { repo: som, pool: som-client, machine_type: n4-standard-2, spot: true, max_nodes: 1 }
  - { repo: keep, pool: keep-server, machine_type: n4-standard-2, spot: true, max_nodes: 1 }
YAML
TOKEN_VALUE="tok3n-that-must-not-leak"
SHA="0123456789abcdef0123456789abcdef01234567"

batch() { # batch <stage> [render]; the FAKE_ and other values pass through
  rm -rf "$B/ev" "$B/calls" "$B/deleted" "$B/stdin-objects" "$B/ns" "$B/job-calls" "$B/summary"
  PATH="$B/bin:$PATH" KUBECONFIG="$B/kubeconfig" REPO=som STAGE="$1" \
    MANIFEST="${BATCH_MANIFEST:-$B/jobs.yaml}" EVIDENCE_DIR="$B/ev" POOLS_FILE="$B/pools.yaml" \
    SOURCE_REPOSITORY=faberline/som SOURCE_SHA="$SHA" SOURCE_TOKEN="$TOKEN_VALUE" \
    RUN_LABEL=gha-1-1 GHCR_USER=bot BATCH_SLEEP=true GITHUB_STEP_SUMMARY="$B/summary" \
    "$GKE/batch.sh" ${2:+"$2"} >"$B/out" 2>"$B/err"
}
rendered() { jq -e "$1" "$B/ev/jobs.json" >/dev/null; }
result_of() { jq -r --arg n "$1" '.[] | select(.name == $n) | .result' "$B/ev/results.json"; }
bjob() { # bjob <name> <condition type or ""> [reason]
  jq -n --arg n "$1" --arg t "$2" --arg r "${3:-}" '{
    metadata: {name: $n},
    status: ({startTime: "2027-01-15T08:00:00Z"}
      + (if $t == "" then {} else {conditions: [{type: $t, status: "True", reason: $r,
          lastTransitionTime: "2027-01-15T08:03:00Z"}]} end)
      + (if $t == "Complete" then {completionTime: "2027-01-15T08:02:00Z"} else {} end))}'
}
bpod() { # bpod <name> <job> <exit code> [waiting reason]
  jq -n --arg n "$1" --arg j "$2" --argjson c "$3" --arg w "${4:-}" '{
    metadata: {name: $n, creationTimestamp: "2020-01-01T00:00:00Z", labels: {"job-name": $j}},
    status: {containerStatuses: [{name: "work", state:
      (if $w != "" then {waiting: {reason: $w}} else {terminated: {exitCode: $c}} end)}]}}'
}

# The manifest and the rendered Jobs.
check "batch: render writes the Jobs of the stage only" batch test render
check "batch: one Job per entry, named <stage>-<name>" rendered \
  '[.items[].metadata.name] == ["test-unit", "test-doc"]'
check "batch: the timeout is the Job deadline" rendered '.items[0].spec.activeDeadlineSeconds == 2400'
check "batch: the default pool is aw-ci, with its toleration" rendered \
  '.items[0].spec.template.spec | .nodeSelector["faberline.dev/pool"] == "aw-ci" and .tolerations[0].value == "aw-ci"'
check "batch: a Job may use a pool of its repository" rendered \
  '.items[1].spec.template.spec.nodeSelector["faberline.dev/pool"] == "som-client"'
check "batch: requests come from the manifest, defaults else" rendered \
  '[.items[].spec.template.spec.containers[0].resources.requests] == [{cpu: "4", memory: "12Gi"}, {cpu: "1500m", memory: "5Gi"}]'
check "batch: env of the manifest reaches the work container" rendered \
  '.items[0].spec.template.spec.containers[0].env | any(.name == "RUST_BACKTRACE" and .value == "1")'
check "batch: only the init container mounts the Secret" rendered \
  'all(.items[].spec.template.spec; (.initContainers[0].volumeMounts | any(.name == "ci-source"))
     and (.containers[0].volumeMounts | all(.name != "ci-source")) and .automountServiceAccountToken == false)'
check "batch: a preemption retries, a test failure does not" rendered \
  '.items[0].spec.podFailurePolicy.rules | (.[0].action == "Ignore" and .[0].onPodConditions[0].type == "DisruptionTarget"
     and .[1].action == "FailJob")'
check "batch: no sccache without a bucket" rendered \
  '.items[0].spec.template.spec.containers[0].env | all(.name != "RUSTC_WRAPPER")'
check "batch: a bucket turns sccache on" env SCCACHE_BUCKET=cache-bucket CI_GCP_SERVICE_ACCOUNT=ci@example.test \
  bash -c "$(declare -f batch rendered); B='$B' GKE='$GKE' SHA='$SHA' TOKEN_VALUE=x; batch test render && rendered '
    .items[0].spec.template.spec | .serviceAccountName == \"ci-job\"
      and (.containers[0].env | any(.name == \"SCCACHE_GCS_BUCKET\" and .value == \"cache-bucket\"))'"
check "batch: render needs no cluster" test ! -e "$B/calls"
if STAGE_TIMEOUT_MINUTES=50 batch test render; then
  fail "batch: the stage timeout must hold the largest Job timeout plus 20 minutes"
else
  check "batch: the stage timeout must hold the largest Job timeout plus 20 minutes" grep -q 'needs timeout-minutes 60' "$B/err"
fi
if batch e2e render; then
  fail "batch: a kind that is not implemented fails its stage"
else
  check "batch: a kind that is not implemented fails its stage" grep -q 'kind image is not implemented' "$B/err"
fi
cat >"$B/bad.yaml" <<'YAML'
version: 2
jobs:
  - { name: Bad_Name, stage: build, command: "", image: "rust:latest", pool: keep-server, timeout: 40, bogus: 1 }
  - { name: twin, stage: test, command: "true", image: "rust:1", timeout: 5m, resources: { cpu: lots, memory: 2G } }
  - { name: twin, stage: test, command: "true", timeout: 5m, env: { "bad-key": 1 } }
YAML
if BATCH_MANIFEST="$B/bad.yaml" batch test render; then
  fail "batch: a bad manifest fails before anything is applied"
else
  pass "batch: a bad manifest fails before anything is applied"
fi
for want in 'version must be 1' 'Bad_Name: name must be' 'stage must be lint, test or e2e' 'unknown field bogus' \
  'command is required' 'never latest' 'pool keep-server is not aw-ci and not a pool of som' \
  'timeout is required' 'twin: the name is used more than once' 'resources.cpu must be' \
  'resources.memory must be' 'twin: no image' 'env bad-key must be'; do
  check "batch: manifest rule: $want" grep -q "$want" "$B/err"
done

# A run: one Job passes, one fails, one times out.
{ bjob test-unit Complete; bjob test-doc Failed PodFailurePolicy; } | items >"$B/jobs.json"
{ bpod test-unit-aaa test-unit 0; bpod test-doc-bbb test-doc 101; } | items >"$B/pods.json"
if batch test; then fail "batch: a failed Job fails the stage"; else pass "batch: a failed Job fails the stage"; fi
check "batch: results name each Job" test "$(result_of unit),$(result_of doc)" = success,failure
check "batch: results keep the exit code and the time" jq -e \
  '.[] | select(.name == "doc") | .exit_code == 101 and .seconds == 180 and .pool == "som-client"' "$B/ev/results.json"
check "batch: one log file per Job" grep -q 'log line of test-doc-bbb' "$B/ev/doc.log"
check "batch: the failed Job comes first in the log" test \
  "$(grep -n '^::group::' "$B/out" | head -n 1)" = '1:::group::test/doc: failure (3 min)'
check "batch: each log is a folded group with commands stopped" test \
  "$(grep -c '^::group::' "$B/out"),$(grep -c '^::endgroup::' "$B/out"),$(grep -c '^::stop-commands::' "$B/out")" = 2,2,2
check "batch: the summary table has a row per Job" test "$(grep -c '^| `' "$B/summary")" = 2
check "batch: the summary names the result" grep -q '^| `doc` | failure | 3 | som-client | 0 |$' "$B/summary"
check "batch: the namespace is ci-<repo>-<run>-<stage>, with its labels" grep -q \
  'label namespace ci-som-gha-1-1-test faberline.dev/test-run=gha-1-1 faberline.dev/expires=[0-9]* faberline.dev/ci-stage=test' "$B/calls"
check "batch: the namespace is deleted after a failed stage" test "$(cat "$B/deleted")" = ci-som-gha-1-1-test
check "batch: the namespace exists before the wake" test \
  "$(grep -n 'create namespace' "$B/calls" | cut -d: -f1)" -lt "$(grep -n 'kube-system wait' "$B/calls" | cut -d: -f1)"
check "batch: the token is in the Secret ci-source" test \
  "$(jq -rs '.[] | select(.metadata.name == "ci-source") | .data.token | @base64d' "$B/stdin-objects")" = "$TOKEN_VALUE"
check "batch: the token is in the pull Secret" test \
  "$(jq -rs '.[] | select(.metadata.name == "ghcr-pull") | .data[".dockerconfigjson"] | @base64d | fromjson
     | .auths["ghcr.io"].password' "$B/stdin-objects")" = "$TOKEN_VALUE"
if grep -rqF "$TOKEN_VALUE" "$B/out" "$B/err" "$B/calls" "$B/ev" "$B/summary"; then
  fail "batch: the token is in no log, argument, Job spec or artifact"
else
  pass "batch: the token is in no log, argument, Job spec or artifact"
fi
if grep -rqF "$(printf '%s' "$TOKEN_VALUE" | base64)" "$B/out" "$B/err" "$B/calls" "$B/ev"; then
  fail "batch: the encoded token is in no log, argument, Job spec or artifact"
else
  pass "batch: the encoded token is in no log, argument, Job spec or artifact"
fi

{ bjob test-unit Complete; bjob test-doc Failed DeadlineExceeded; } | items >"$B/jobs.json"
if batch test; then fail "batch: a Job past its timeout fails the stage"; else
  check "batch: a Job past its timeout fails the stage" test "$(result_of doc)" = timeout
fi

# All Jobs pass. The first answer of the cluster shows them still running.
{ bjob test-unit ""; bjob test-doc ""; } | items >"$B/jobs-1.json"
{ bjob test-unit Complete; bjob test-doc Complete; } | items >"$B/jobs.json"
{ bpod test-unit-aaa test-unit 0; bpod test-doc-bbb test-doc 0; } | items >"$B/pods.json"
check "batch: waits, then passes when every Job passes" batch test
check "batch: it waited for the Jobs" test "$(cat "$B/job-calls")" -ge 3
check "batch: the namespace is deleted after a passed stage" test "$(cat "$B/deleted")" = ci-som-gha-1-1-test
rm -f "$B/jobs-1.json"

# A pod that cannot pull its image fails its Job without the timeout.
{ bjob test-unit Complete; bjob test-doc ""; } | items >"$B/jobs.json"
{ bpod test-unit-aaa test-unit 0; bpod test-doc-bbb test-doc 0 ImagePullBackOff; } | items >"$B/pods.json"
if batch test; then fail "batch: an image that cannot be pulled fails fast"; else
  check "batch: an image that cannot be pulled fails fast" test "$(result_of doc)" = image-error
fi

# Nothing to do, and a pool that the cluster does not have.
cat >"$B/lint-only.yaml" <<'YAML'
version: 1
image: ghcr.io/faberline/ci-rust:1.99.1
jobs:
  - { name: fmt, stage: lint, command: "cargo fmt --check", timeout: 5m }
YAML
check "batch: a stage without a Job passes at once" env BATCH_MANIFEST="$B/lint-only.yaml" \
  bash -c "$(declare -f batch); B='$B' GKE='$GKE' SHA='$SHA' TOKEN_VALUE=x; batch test"
check "batch: a stage without a Job does not touch the cluster" test ! -e "$B/calls"
if FAKE_POOLS="pool-keep-server" batch test; then
  fail "batch: a missing node pool stops the run before the namespace"
else
  check "batch: a missing node pool stops the run before the namespace" test ! -e "$B/calls"
fi

# ci-stage.yml: the guard comes first, and the teardown always runs.
CS="$GKE/../../.github/workflows/ci-stage.yml"
check "ci-stage: the first step refuses forks and other pushes, and has no private rule" test \
  "$(yq '.jobs.batch.steps[0].run | (contains("from a fork") and contains("default branch") and (contains("PRIVATE") | not) and (contains("pull_request_target") | not))' "$CS")" = true
check "ci-stage: nothing in the workflow uses pull_request_target or the private flag" test \
  "$(grep -v '^ *#' "$CS" | grep -c 'pull_request_target\|repository\.private' || true)" = 0
yq '.jobs.batch.steps[0].run' "$CS" >"$TMP/guard.sh"
guard() { # guard <event> <head repo> <ref> [repository]; the status is the step's
  (
    export EVENT="$1" HEAD_REPOSITORY="$2" REF="$3" GITHUB_REPOSITORY="${4:-faberline/som}" DEFAULT_BRANCH=main
    bash "$TMP/guard.sh" >/dev/null 2>&1
  )
}
# The repository is public in every case: the guard does not read the flag.
if guard pull_request someone/som refs/pull/1/merge; then
  fail "ci-stage: a fork pull request on a public repository is refused"
else
  pass "ci-stage: a fork pull request on a public repository is refused"
fi
check "ci-stage: a same-repository pull request on a public repository runs" \
  guard pull_request faberline/som refs/pull/1/merge
if guard push "" refs/heads/feature; then
  fail "ci-stage: a push to a branch other than the default is refused"
else
  pass "ci-stage: a push to a branch other than the default is refused"
fi
check "ci-stage: a push to the default branch runs" guard push "" refs/heads/main
check "ci-stage: workflow_dispatch runs" guard workflow_dispatch "" refs/heads/feature
if guard pull_request_target faberline/som refs/heads/main; then
  fail "ci-stage: pull_request_target is refused"
else
  pass "ci-stage: pull_request_target is refused"
fi
check "ci-stage: the token goes only to the batch step" test \
  "$(yq '[.jobs.batch.steps[] | select(.env.SOURCE_TOKEN != null) | .run] | length' "$CS")" = 1
check "ci-stage: the checkouts keep no credentials" test \
  "$(yq '[.jobs.batch.steps[] | select(.uses == "actions/checkout*") | .with.persist-credentials] | all_c(. == false)' "$CS")" = true
check "ci-stage: the backup teardown always runs, and is last" test \
  "$(yq '.jobs.batch.steps[-1] | (.if == "always()" and (.run | contains("teardown.sh")))' "$CS")" = true
check "ci-stage: no concurrency group" test "$(yq '.jobs.batch | has("concurrency")' "$CS")" = false

if ((fails > 0)); then
  printf '%s test(s) failed\n' "$fails" >&2
  exit 1
fi
echo "all tests passed"
