#!/usr/bin/env bash
set -euo pipefail

# Deploy one repository's kustomize overlay into its own namespace on the
# shared GKE cluster, run its test Job and/or test command, and always tear the
# namespace down. Called by .github/workflows/gke-test.yml from the caller's
# checkout.
#
# Required environment:
#   KUBECONFIG     task-local kubeconfig with credentials for the cluster
#   REPO           repository name; owns the pools that pools.yaml gives it
#   OVERLAY        path of the kustomize overlay, relative to the checkout
#   IMAGE          image to deploy, for example ghcr.io/faberline/keep:sha-<git>
#   EVIDENCE_DIR   folder for the rendered manifests, describe output, and logs
#   TEST_JOB and/or TEST_COMMAND, at least one:
#   TEST_JOB       kustomize folder with one Job; applied after every rollout
#                  is done; the run passes only when the Job completes
#   TEST_COMMAND   bash command; runs in the checkout after the Job passes
# Optional:
#   NAMESPACE      default REPO; must match the overlay's Namespace object
#   IMAGE_NAME     image name in the overlay and test Job to replace; default REPO
#   TEST_IMAGE, TEST_IMAGE_NAME   both or neither: also set TEST_IMAGE_NAME
#                  to TEST_IMAGE, in the test Job only; for a client image
#   TEST_ENV       lines of KEY=VALUE; the test Job gets them in the ConfigMap
#                  gke-test-env (made also when TEST_ENV is empty)
#   RUN_LABEL      value of faberline.dev/test-run; default local-<time>
#   TTL_MINUTES    job timeout; the reaper may delete the namespace 15 minutes
#                  after it; default 90
#   GHCR_USER, GHCR_TOKEN   when set, create the ghcr-pull imagePullSecret
#   ROLLOUT_TIMEOUT         default 900s; includes scaling the pool up from 0
#   TEST_JOB_TIMEOUT        seconds; includes the client pool scale-up;
#                           default TTL_MINUTES minus 15 minutes, so the run
#                           still collects evidence and tears down in time
#   POOLS_FILE     default: infra/gke-test/pools.yaml of the same aw checkout

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"

: "${KUBECONFIG:?KUBECONFIG must point at a task-local file}"
: "${REPO:?}" "${OVERLAY:?}" "${IMAGE:?}" "${EVIDENCE_DIR:?}"
TEST_JOB="${TEST_JOB:-}"
TEST_COMMAND="${TEST_COMMAND:-}"
[[ -n "$TEST_JOB$TEST_COMMAND" ]] || { log "set TEST_JOB, TEST_COMMAND, or both"; exit 1; }
NAMESPACE="${NAMESPACE:-$REPO}"
IMAGE_NAME="${IMAGE_NAME:-$REPO}"
TEST_IMAGE="${TEST_IMAGE:-}"
TEST_IMAGE_NAME="${TEST_IMAGE_NAME:-}"
TEST_ENV="${TEST_ENV:-}"
[[ -z "$TEST_IMAGE$TEST_IMAGE_NAME" || ( -n "$TEST_IMAGE" && -n "$TEST_IMAGE_NAME" ) ]] \
  || { log "set TEST_IMAGE and TEST_IMAGE_NAME together"; exit 1; }
# render() sets IMAGE_NAME last, so the same name would undo TEST_IMAGE.
[[ "$TEST_IMAGE_NAME" != "$IMAGE_NAME" ]] \
  || { log "TEST_IMAGE_NAME must differ from IMAGE_NAME ($IMAGE_NAME)"; exit 1; }
[[ -n "$TEST_JOB" || -z "$TEST_IMAGE$TEST_ENV" ]] \
  || { log "TEST_IMAGE and TEST_ENV need TEST_JOB"; exit 1; }
RUN_LABEL="${RUN_LABEL:-local-$(date +%s)}"
TTL_MINUTES="${TTL_MINUTES:-90}"
ROLLOUT_TIMEOUT="${ROLLOUT_TIMEOUT:-900s}"
TEST_JOB_TIMEOUT="${TEST_JOB_TIMEOUT:-$(( (TTL_MINUTES > 20 ? TTL_MINUTES - 15 : 5) * 60 ))}"
POOLS_FILE="${POOLS_FILE:-$SCRIPT_DIR/../../infra/gke-test/pools.yaml}"
export KUBECONFIG REPO NAMESPACE IMAGE EVIDENCE_DIR RUN_LABEL

mkdir -p "$EVIDENCE_DIR"
rendered="$EVIDENCE_DIR/rendered.yaml"
job_rendered="$EVIDENCE_DIR/test-job-rendered.yaml"

# --- Render and check, before any cluster time is spent ----------------------

[[ -f "$POOLS_FILE" ]] || { log "no pools file at $POOLS_FILE"; exit 1; }
# Pool name -> its row.
pools_json="$(yq -o=json -I=0 '.pools' "$POOLS_FILE" \
  | jq -c 'map({key: .pool, value: .}) | from_entries')"

# render <kustomize folder> <output file>: also writes <output>.json, one
# JSON document per manifest, for jq.
render() {
  local dir="$1" out="$2"
  [[ -f "$dir/kustomization.yaml" ]] || { log "no kustomization.yaml in $dir"; return 1; }
  (cd "$dir" && kustomize edit set image "$IMAGE_NAME=$IMAGE")
  kustomize build "$dir" >"$out"
  if grep -n 'REPLACE_ME' "$out" >&2; then
    log "$out still contains REPLACE_ME"
    return 1
  fi
  yq -o=json -I=0 '.' "$out" | jq -s 'map(select(. != null))' >"$out.json"
}

# Rules for every pod template, in the overlay and in the test Job. Prints one
# line per problem.
check_pods() {
  jq -r --arg label "$LABEL_POOL" --arg repo "$REPO" --argjson pools "$pools_json" '
    def pod_spec:
      if .kind == "Pod" then .spec
      elif .kind == "CronJob" then .spec.jobTemplate.spec.template.spec
      elif (.kind | IN("Deployment","StatefulSet","DaemonSet","ReplicaSet","Job")) then .spec.template.spec
      else empty end;
    def where: "\(.kind)/\(.metadata.name)";
    def spread_ok:
      any(.topologySpreadConstraints[]?;
          .topologyKey == "kubernetes.io/hostname" and .whenUnsatisfiable == "DoNotSchedule"
          and (.maxSkew // 1) == 1)
      or any(.affinity.podAntiAffinity.requiredDuringSchedulingIgnoredDuringExecution[]?;
             .topologyKey == "kubernetes.io/hostname");
    .[] | . as $o | pod_spec as $s | ($o | where) as $w
    | ($s.nodeSelector[$label] // "") as $p
    | (if $p == "" then "\($w): nodeSelector \($label) is missing"
       elif $pools[$p] == null then "\($w): pool \($p) is not in infra/gke-test/pools.yaml of aw"
       elif $pools[$p].repo != $repo then "\($w): pool \($p) belongs to \($pools[$p].repo), not \($repo)"
       else empty end),
      (if $p != "" and (any($s.tolerations[]?; .key == $label and .value == $p and .effect == "NoSchedule") | not)
         then "\($w): toleration \($label)=\($p):NoSchedule is missing" else empty end),
      (if any($s.imagePullSecrets[]?; .name == "ghcr-pull") | not
         then "\($w): imagePullSecrets ghcr-pull is missing" else empty end),
      ((($s.containers // []) + ($s.initContainers // []))[]
        | select((.resources.requests.cpu // null) == null or (.resources.requests.memory // null) == null)
        | "\($w): container \(.name) needs resources.requests cpu and memory"),
      (if ($o.kind | IN("Deployment","StatefulSet")) and (($o.spec.replicas // 1) > 1) then
         (if ($s | spread_ok) | not
            then "\($w): \($o.spec.replicas) replicas need one per node: topologySpreadConstraints with topologyKey kubernetes.io/hostname, maxSkew 1, whenUnsatisfiable DoNotSchedule"
          else empty end),
         (if $pools[$p] != null and $o.spec.replicas > $pools[$p].max_nodes
            then "\($w): \($o.spec.replicas) replicas need \($o.spec.replicas) nodes, but pool \($p) has max_nodes \($pools[$p].max_nodes)"
          else empty end)
       else empty end)
  ' "$1"
}

render "$OVERLAY" "$rendered"
problems="$(check_pods "$rendered.json")
$(jq -r --arg ns "$NAMESPACE" '.[] | select(.kind == "Namespace" and .metadata.name != $ns)
  | "\(.kind)/\(.metadata.name): the overlay namespace must be \($ns)"' "$rendered.json")"

job_name=""
if [[ -n "$TEST_JOB" ]]; then
  # The Job lives in the run's namespace, so it goes away with the namespace.
  (cd "$TEST_JOB" && kustomize edit set namespace "$NAMESPACE")
  if [[ -n "$TEST_IMAGE" ]]; then
    (cd "$TEST_JOB" && kustomize edit set image "$TEST_IMAGE_NAME=$TEST_IMAGE")
  fi
  # Per-call values, for example one cell of a matrix. Blank lines and lines
  # that start with # are skipped. Keys must be environment variable names.
  bad_env="$(grep -vE '^[[:space:]]*(#|$)' <<<"$TEST_ENV" | grep -vE '^[A-Za-z_][A-Za-z0-9_]*=' || true)"
  if [[ -n "$bad_env" ]]; then
    log "TEST_ENV lines must be KEY=VALUE:"
    sed 's/^/  /' <<<"$bad_env" >&2
    exit 1
  fi
  grep -vE '^[[:space:]]*(#|$)' <<<"$TEST_ENV" >"$TEST_JOB/.gke-test-env" || true
  (cd "$TEST_JOB" && kustomize edit add configmap gke-test-env \
    --from-env-file=.gke-test-env --disableNameSuffixHash)
  render "$TEST_JOB" "$job_rendered"
  problems+="
$(check_pods "$job_rendered.json")
$(jq -r '
  (.[] | select(.kind | IN("Job","ConfigMap","Secret","ServiceAccount","Role","RoleBinding") | not)
       | "test Job: \(.kind)/\(.metadata.name) is not allowed; use Job, ConfigMap, Secret, ServiceAccount, Role, RoleBinding"),
  (if ([.[] | select(.kind == "Job")] | length) != 1 then "test Job: \(env.TEST_JOB) must render exactly one Job" else empty end)
' "$job_rendered.json")"
  job_name="$(jq -r '.[] | select(.kind == "Job") | .metadata.name' "$job_rendered.json" | head -n 1)"
fi

# A row merged into pools.yaml has no node pool until an aw maintainer runs
# terraform apply. A pod for it stays Pending until the run times out, so stop
# here instead.
live_pools="$(gcloud container clusters describe "$GKE_CLUSTER" \
  --project="$GKE_PROJECT" --zone="$GKE_ZONE" --format='value(nodePools[].name)' | tr ';' '\n')"
for p in $(cat "$rendered.json" ${TEST_JOB:+"$job_rendered.json"} | jq -r --arg label "$LABEL_POOL" '
    .[] | (.spec.template.spec // .spec.jobTemplate.spec.template.spec // .spec)
    | .nodeSelector[$label]? // empty' | sort -u); do
  grep -qx "pool-$p" <<<"$live_pools" \
    || problems+="
the cluster has no node pool pool-$p; when pools.yaml has it, an aw maintainer must run terraform apply"
done

problems="$(sed '/^$/d' <<<"$problems")"
if [[ -n "$problems" ]]; then
  log "the manifests are not ready for the shared cluster (see docs/standards/test-cluster.md in aw):"
  sed 's/^/  /' <<<"$problems" >&2
  exit 1
fi

# --- From here on, always clean up -------------------------------------------

collect_evidence() {
  set +e
  kubectl get nodes -L "$LABEL_POOL" -L node.kubernetes.io/instance-type -o wide >"$EVIDENCE_DIR/nodes.txt" 2>&1
  kubectl -n "$NAMESPACE" get all,pvc,configmap -o wide >"$EVIDENCE_DIR/resources.txt" 2>&1
  kubectl -n "$NAMESPACE" get events --sort-by=.lastTimestamp >"$EVIDENCE_DIR/events.txt" 2>&1
  kubectl -n "$NAMESPACE" describe pods >"$EVIDENCE_DIR/pods-describe.txt" 2>&1
  if [[ -n "$job_name" ]]; then
    kubectl -n "$NAMESPACE" get job "$job_name" -o yaml >"$EVIDENCE_DIR/test-job-status.yaml" 2>&1
  fi
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

if [[ -n "$job_name" ]]; then
  log "starting test Job $job_name"
  kubectl apply --server-side --force-conflicts --field-manager=gke-test -f "$job_rendered" >&2
  deadline=$((SECONDS + TEST_JOB_TIMEOUT))
  state=""
  while [[ -z "$state" ]]; do
    if (( SECONDS >= deadline )); then
      log "test Job $job_name did not finish in ${TEST_JOB_TIMEOUT}s"
      state="Timeout"
      break
    fi
    sleep 10
    state="$(kubectl -n "$NAMESPACE" get job "$job_name" -o json | jq -r '
      [.status.conditions[]? | select(.status == "True") | .type]
      | if index("Complete") then "Complete" elif index("Failed") then "Failed" else "" end')"
  done
  # Into the run log too, so a reader does not need the artifact for a
  # quick look. The evidence keeps the full logs of every pod.
  kubectl -n "$NAMESPACE" logs -l "job-name=$job_name" --all-containers --prefix --tail=-1 >&2 || true
  if [[ "$state" != "Complete" ]]; then
    log "test Job $job_name: $state"
    exit 1
  fi
  log "test Job $job_name passed"
fi

if [[ -n "$TEST_COMMAND" ]]; then
  log "running test command"
  bash -euo pipefail -c "$TEST_COMMAND"
  log "test command passed"
fi
