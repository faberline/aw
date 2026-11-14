#!/usr/bin/env bash
set -euo pipefail

# Run the batch of one CI stage on the shared GKE cluster: read the manifest of
# the repository, apply every work Job of the stage at once in the run's own
# namespace, wait for all of them, collect the logs and the results, and always
# delete the namespace. Called by .github/workflows/ci-stage.yml.
# Contract: docs/standards/ci-on-gke.md.
#
#   batch.sh          run the batch
#   batch.sh render   check the manifest and write the Jobs to
#                     EVIDENCE_DIR/jobs.json; needs no cluster and no token
#
# Required environment:
#   REPO               repository name, for example som
#   STAGE              lint, test or e2e
#   MANIFEST           path of the manifest, for example .github/ci/jobs.yaml
#   EVIDENCE_DIR       folder for jobs.json, results.json, summary.md, one
#                      <name>.log per work Job, events.txt
#   SOURCE_REPOSITORY  <owner>/<name> on GitHub
#   SOURCE_SHA         the commit that every work Job fetches
# Required to run (not for `render`):
#   KUBECONFIG         task-local kubeconfig with credentials for the cluster
#   SOURCE_TOKEN       short-lived token that can read the repository. It goes
#                      only into the Secret ci-source of the namespace, through
#                      stdin. It is never an argument, a file or a log line.
# Optional:
#   RUN_LABEL          value of faberline.dev/test-run; default local-<time>
#   NAMESPACE          default ci-<REPO>-<RUN_LABEL>-<STAGE>
#   GHCR_USER          when set, SOURCE_TOKEN also becomes the imagePullSecret
#                      ghcr-pull (the token needs packages: read)
#   SCCACHE_BUCKET     Cloud Storage bucket of sccache; unset means no sccache
#   CI_GCP_SERVICE_ACCOUNT  Google service account for Workload Identity; when
#                      set, the Jobs run as the Kubernetes service account
#                      ci-job, which carries the annotation for it
#   STAGE_TIMEOUT_MINUTES   timeout of the calling GitHub job; the largest Job
#                      timeout plus 20 minutes must fit in it
#   POOLS_FILE         default: infra/gke-test/pools.yaml of the same aw checkout
#   BATCH_POLL_SECONDS default 10
#   IMAGE_GRACE_SECONDS  default 180; a pod that still cannot pull its image
#                      after this time fails its Job at once
#   BATCH_SLEEP        replaces `sleep` in tests
#   GITHUB_STEP_SUMMARY  the summary table is added to this file

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"

MODE="${1:-run}"
[[ "$MODE" == run || "$MODE" == render ]] || { log "usage: batch.sh [render]"; exit 2; }

: "${REPO:?}" "${STAGE:?}" "${MANIFEST:?}" "${EVIDENCE_DIR:?}"
: "${SOURCE_REPOSITORY:?}" "${SOURCE_SHA:?}"
if [[ "$MODE" == run ]]; then
  : "${KUBECONFIG:?KUBECONFIG must point at a task-local file}"
  : "${SOURCE_TOKEN:?}"
fi
RUN_LABEL="${RUN_LABEL:-local-$(date +%s)}"
NAMESPACE="${NAMESPACE:-ci-$REPO-$RUN_LABEL-$STAGE}"
SCCACHE_BUCKET="${SCCACHE_BUCKET:-}"
CI_GCP_SERVICE_ACCOUNT="${CI_GCP_SERVICE_ACCOUNT:-}"
STAGE_TIMEOUT_MINUTES="${STAGE_TIMEOUT_MINUTES:-}"
POOLS_FILE="${POOLS_FILE:-$SCRIPT_DIR/../../infra/gke-test/pools.yaml}"
BATCH_POLL_SECONDS="${BATCH_POLL_SECONDS:-10}"
IMAGE_GRACE_SECONDS="${IMAGE_GRACE_SECONDS:-180}"
# The pool of ci-on-gke. Terraform makes it (infra/gke-test/); it is not a row
# of pools.yaml, because it belongs to no single repository.
CI_POOL="aw-ci"
CI_KSA="ci-job"
LABEL_STAGE="faberline.dev/ci-stage"
LABEL_JOB="faberline.dev/ci-job"
# Minutes on top of the largest Job timeout: the queue for a node, the node
# scale-up, the log collection and the teardown.
STAGE_GRACE_MINUTES=20
export KUBECONFIG NAMESPACE EVIDENCE_DIR

[[ "$STAGE" == lint || "$STAGE" == test || "$STAGE" == e2e ]] \
  || { log "STAGE must be lint, test or e2e; got $STAGE"; exit 1; }
[[ "$NAMESPACE" =~ ^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$ ]] \
  || { log "NAMESPACE $NAMESPACE is not a DNS label of at most 63 characters"; exit 1; }
[[ "$SOURCE_SHA" =~ ^[0-9a-f]{40}$ ]] || { log "SOURCE_SHA must be a full commit SHA"; exit 1; }
[[ "$SOURCE_REPOSITORY" =~ ^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$ ]] \
  || { log "SOURCE_REPOSITORY must be <owner>/<name>"; exit 1; }
[[ -f "$MANIFEST" ]] || { log "no manifest at $MANIFEST"; exit 1; }
[[ -f "$POOLS_FILE" ]] || { log "no pools file at $POOLS_FILE"; exit 1; }

mkdir -p "$EVIDENCE_DIR"
manifest_json="$EVIDENCE_DIR/manifest.json"
jobs_file="$EVIDENCE_DIR/jobs.json"
results_file="$EVIDENCE_DIR/results.json"
summary_file="$EVIDENCE_DIR/summary.md"

# --- Check the manifest, before any cluster time is spent --------------------

yq -o=json -I=0 '.' "$MANIFEST" >"$manifest_json"
# Pool name -> the repository that owns it.
pool_owners="$(yq -o=json -I=0 '.pools' "$POOLS_FILE" \
  | jq -c 'map({key: .pool, value: .repo}) | from_entries')"

# Prints one line per problem.
check_manifest() {
  jq -r --arg stage "$STAGE" --arg repo "$REPO" --arg ci_pool "$CI_POOL" \
    --argjson pools "$pool_owners" '
    def s: if type == "string" then . else "" end;
    ["name","stage","kind","command","image","pool","resources","timeout","paths","env"] as $fields
    | . as $m
    | (if type != "object" then "the manifest must be a map" else empty end),
      (if (.version? // null) != 1 then "version must be 1" else empty end),
      (if (.jobs? | type) != "array" then "jobs must be a list" else empty end),
      ((.jobs? | if type == "array" then . else [] end) as $jobs
       | ($jobs | map(.name? | s) | group_by(.) | map(select(length > 1 and .[0] != ""))[]
          | "job \(.[0]): the name is used more than once"),
         ($jobs[] | select(type != "object") | "jobs: every entry must be a map"),
         ($jobs[] | select(type == "object")
          | (.name | s) as $n | "job \($n)" as $w | (.kind // "command") as $k
          | (if ($n | test("^[a-z0-9]([a-z0-9-]{0,38}[a-z0-9])?$")) | not
               then "\($w): name must be [a-z0-9-], at most 40 characters" else empty end),
            (if (.stage | s | IN("lint", "test", "e2e")) | not
               then "\($w): stage must be lint, test or e2e" else empty end),
            (if ($k | IN("command", "image", "suite")) | not
               then "\($w): kind must be command, image or suite"
             elif $k != "command" then
               (if .stage == $stage
                  then "\($w): kind \($k) is not implemented yet; the batch runner runs only kind command"
                else empty end)
             else
               ((keys - $fields)[] | "\($w): unknown field \(.)"),
               (if (.command | s) == "" then "\($w): command is required" else empty end),
               ((.image // $m.image | s) as $i
                | if $i == "" then "\($w): no image; set image in the Job or at the top of the manifest"
                  elif ($i | test("@sha256:[0-9a-f]{64}$")) then empty
                  elif ($i | test(":[A-Za-z0-9_][A-Za-z0-9_.-]*$") | not) or ($i | endswith(":latest"))
                    then "\($w): image \($i) must be pinned by tag or digest, never latest"
                  else empty end),
               ((.pool // $ci_pool | s) as $p
                | if $p != $ci_pool and $pools[$p] != $repo
                    then "\($w): pool \($p) is not \($ci_pool) and not a pool of \($repo) in infra/gke-test/pools.yaml of aw"
                  else empty end),
               (if (.resources.cpu? // "2" | tostring | test("^([0-9]+(\\.[0-9]+)?|[0-9]+m)$")) | not
                  then "\($w): resources.cpu must be a number of CPUs, for example \"4\" or 500m" else empty end),
               (if (.resources.memory? // "6Gi" | s | test("^[0-9]+(Mi|Gi)$")) | not
                  then "\($w): resources.memory must be <n>Mi or <n>Gi" else empty end),
               (if (.timeout | s | test("^[1-9][0-9]*m$")) | not
                  then "\($w): timeout is required, as <n>m" else empty end),
               ((.env // {})
                | if type != "object" then "\($w): env must be a map"
                  else to_entries[]
                    | select((.key | test("^[A-Za-z_][A-Za-z0-9_]*$") | not)
                             or (.value | type | IN("string", "number", "boolean") | not))
                    | "\($w): env \(.key) must be a variable name with a plain value"
                  end)
             end)))
  ' "$manifest_json"
}

problems="$(check_manifest | sed '/^$/d')"
if [[ -n "$problems" ]]; then
  log "$MANIFEST breaks the manifest rules (see docs/standards/ci-on-gke.md in aw):"
  sed 's/^/  /' <<<"$problems" >&2
  exit 1
fi

jq -r --arg stage "$STAGE" '.jobs[] | select(.stage == $stage and has("paths"))
  | "job \(.name): paths is not implemented yet; the Job always runs"' "$manifest_json" \
  | while read -r line; do log "warning: $line"; done

# The work Jobs of the stage, as one Kubernetes List.
#
# - The init container `source` fetches the one commit with the token from the
#   Secret ci-source. Only it mounts the Secret. The token reaches git through
#   the environment of that one process, so it is not in the Job spec, not in
#   an argument, not in .git/config and not in the log.
# - The container `work` runs the command in the checkout, as a user without
#   root rights and without a Kubernetes token.
# - A Spot preemption (pod condition DisruptionTarget) starts the pod again. Any
#   other exit code that is not 0 fails the Job at once: a test is never retried.
read -r -d '' SOURCE_SCRIPT <<'SCRIPT' || true
mkdir -p /work/src /work/home /work/cargo
cd /work/src
git init -q .
git remote add origin "https://github.com/$(cat /var/run/ci-source/repository).git"
auth="$(printf 'x-access-token:%s' "$(cat /var/run/ci-source/token)" | base64 | tr -d '\n')"
GIT_TERMINAL_PROMPT=0 GIT_CONFIG_COUNT=1 \
  GIT_CONFIG_KEY_0=http.https://github.com/.extraheader \
  GIT_CONFIG_VALUE_0="AUTHORIZATION: basic ${auth}" \
  git fetch -q --depth 1 origin "$(cat /var/run/ci-source/sha)"
unset auth
git checkout -q --detach FETCH_HEAD
echo "source: $(cat /var/run/ci-source/repository)@$(git rev-parse HEAD)"
SCRIPT

jq --arg stage "$STAGE" --arg ns "$NAMESPACE" --arg repo "$REPO" --arg ci_pool "$CI_POOL" \
  --arg repository "$SOURCE_REPOSITORY" --arg sha "$SOURCE_SHA" \
  --arg bucket "$SCCACHE_BUCKET" --arg ksa "${CI_GCP_SERVICE_ACCOUNT:+$CI_KSA}" \
  --arg pull "${GHCR_USER:+ghcr-pull}" --arg source_script "$SOURCE_SCRIPT" \
  --arg label_stage "$LABEL_STAGE" --arg label_job "$LABEL_JOB" --arg label_pool "$LABEL_POOL" '
  . as $m
  | { allowPrivilegeEscalation: false, capabilities: { drop: ["ALL"] } } as $locked
  | [ .jobs[] | select(.stage == $stage and (.kind // "command") == "command")
      | (.pool // $ci_pool) as $pool
      | { ($label_stage): $stage, ($label_job): .name } as $labels
      | {
          apiVersion: "batch/v1",
          kind: "Job",
          metadata: {
            name: "\($stage)-\(.name)", namespace: $ns, labels: $labels,
            annotations: { ($label_pool): $pool }
          },
          spec: {
            backoffLimit: 2,
            activeDeadlineSeconds: ((.timeout | rtrimstr("m") | tonumber) * 60),
            podFailurePolicy: { rules: [
              { action: "Ignore", onPodConditions: [ { type: "DisruptionTarget" } ] },
              { action: "FailJob", onExitCodes: { operator: "NotIn", values: [0] } }
            ] },
            template: {
              metadata: { labels: $labels },
              spec: ({
                restartPolicy: "Never",
                automountServiceAccountToken: false,
                nodeSelector: { ($label_pool): $pool },
                tolerations: [ { key: $label_pool, operator: "Equal", value: $pool, effect: "NoSchedule" } ],
                securityContext: {
                  runAsNonRoot: true, runAsUser: 1000, runAsGroup: 1000, fsGroup: 1000,
                  seccompProfile: { type: "RuntimeDefault" }
                },
                volumes: [
                  { name: "work", emptyDir: {} },
                  { name: "ci-source", secret: { secretName: "ci-source", defaultMode: 288 } }
                ],
                initContainers: [ {
                  name: "source",
                  image: (.image // $m.image),
                  command: ["bash", "-euo", "pipefail", "-c", $source_script],
                  resources: { requests: { cpu: "100m", memory: "256Mi" } },
                  securityContext: $locked,
                  volumeMounts: [
                    { name: "work", mountPath: "/work" },
                    { name: "ci-source", mountPath: "/var/run/ci-source", readOnly: true }
                  ]
                } ],
                containers: [ {
                  name: "work",
                  image: (.image // $m.image),
                  workingDir: "/work/src",
                  command: ["bash", "-euo", "pipefail", "-c", .command],
                  env: (
                    [ { name: "CI", value: "true" },
                      { name: "CI_STAGE", value: $stage },
                      { name: "CI_JOB", value: .name },
                      { name: "GITHUB_REPOSITORY", value: $repository },
                      { name: "GITHUB_SHA", value: $sha },
                      { name: "HOME", value: "/work/home" },
                      { name: "CARGO_HOME", value: "/work/cargo" } ]
                    + (if $bucket != "" then
                        [ { name: "RUSTC_WRAPPER", value: "sccache" },
                          { name: "SCCACHE_GCS_BUCKET", value: $bucket },
                          { name: "SCCACHE_GCS_KEY_PREFIX", value: $repo },
                          { name: "SCCACHE_GCS_RW_MODE", value: "READ_WRITE" },
                          { name: "CARGO_INCREMENTAL", value: "0" } ]
                       else [] end)
                    + ((.env // {}) | to_entries | map({ name: .key, value: (.value | tostring) }))
                  ),
                  resources: { requests: {
                    cpu: (.resources.cpu // "2" | tostring),
                    memory: (.resources.memory // "6Gi")
                  } },
                  securityContext: $locked,
                  volumeMounts: [ { name: "work", mountPath: "/work" } ]
                } ]
              }
              + (if $ksa != "" then { serviceAccountName: $ksa } else {} end)
              + (if $pull != "" then { imagePullSecrets: [ { name: $pull } ] } else {} end))
            }
          }
        } ]
  | { apiVersion: "v1", kind: "List", items: . }
' "$manifest_json" >"$jobs_file"

job_count="$(jq '.items | length' "$jobs_file")"
max_minutes="$(jq '[.items[].spec.activeDeadlineSeconds / 60] | max // 0' "$jobs_file")"
stage_minutes=$((max_minutes + STAGE_GRACE_MINUTES))
if [[ -n "$STAGE_TIMEOUT_MINUTES" ]] && (( job_count > 0 && stage_minutes > STAGE_TIMEOUT_MINUTES )); then
  log "the largest timeout of stage $STAGE is ${max_minutes}m; with $STAGE_GRACE_MINUTES minutes for the queue the stage job needs timeout-minutes $stage_minutes, but it has $STAGE_TIMEOUT_MINUTES"
  exit 1
fi

if [[ "$MODE" == render ]]; then
  log "$job_count work Job(s) of stage $STAGE written to $jobs_file"
  exit 0
fi

# Add text to the job summary of GitHub and keep a copy in the evidence.
summary() {
  printf '%s\n' "$@" >>"$summary_file"
  if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then printf '%s\n' "$@" >>"$GITHUB_STEP_SUMMARY"; fi
}

if (( job_count == 0 )); then
  log "stage $STAGE has no work Job in $MANIFEST; the stage passes"
  echo '[]' >"$results_file"
  summary "### CI stage \`$STAGE\`" "" "No work Job in \`$MANIFEST\`. The stage passes."
  exit 0
fi

# A pod for a pool that the cluster does not have stays Pending until its
# timeout, so stop here instead.
live_pools="$(gcloud container node-pools list --cluster="$GKE_CLUSTER" \
  --project="$GKE_PROJECT" --zone="$GKE_ZONE" --format='value(name)')"
missing=""
for p in $(jq -r --arg k "$LABEL_POOL" '.items[].metadata.annotations[$k]' "$jobs_file" | sort -u); do
  grep -qxF "pool-$p" <<<"$live_pools" || missing+="pool-$p "
done
if [[ -n "$missing" ]]; then
  log "the cluster has no node pool ${missing% }; an aw maintainer must run terraform apply in infra/gke-test"
  exit 1
fi

# --- From here on, always clean up -------------------------------------------

sleep_fn="${BATCH_SLEEP:-sleep}"
blocked='[]' # names of the Jobs whose pod cannot pull its image
collected=""

# Read the Jobs and pods of the namespace, write results.json, the logs and the
# summary, and print each log as a folded group. Returns 1 when a Job did not
# pass.
collect() {
  collected=1
  local live="$EVIDENCE_DIR/live-jobs.json" pods="$EVIDENCE_DIR/live-pods.json"
  kubectl -n "$NAMESPACE" get jobs -o json >"$live" 2>/dev/null || echo '{"items":[]}' >"$live"
  kubectl -n "$NAMESPACE" get pods -o json >"$pods" 2>/dev/null || echo '{"items":[]}' >"$pods"
  kubectl -n "$NAMESPACE" get events --sort-by=.lastTimestamp >"$EVIDENCE_DIR/events.txt" 2>&1 || true

  jq -n --slurpfile rendered "$jobs_file" --slurpfile live "$live" --slurpfile pods "$pods" \
    --argjson blocked "$blocked" --arg label_job "$LABEL_JOB" --arg label_pool "$LABEL_POOL" '
    def cond($t): [.status.conditions[]? | select(.type == $t and .status == "True")][0];
    ($live[0].items | map({ key: .metadata.name, value: . }) | from_entries) as $jobs
    | ($pods[0].items | map(select(.metadata.labels["job-name"] != null))
       | sort_by(.metadata.creationTimestamp) | group_by(.metadata.labels["job-name"])
       | map({ key: .[0].metadata.labels["job-name"], value: . }) | from_entries) as $by_job
    | [ $rendered[0].items[]
        | .metadata.name as $k | ($jobs[$k] // {}) as $l | ($by_job[$k] // []) as $ps
        | ($l | cond("Complete")) as $c | ($l | cond("Failed")) as $f
        | {
            name: .metadata.labels[$label_job],
            job: $k,
            result: (if $c then "success"
                     elif $f then (if $f.reason == "DeadlineExceeded" then "timeout" else "failure" end)
                     elif ($blocked | index($k)) then "image-error"
                     else "not-finished" end),
            exit_code: ($ps[-1] // {}
              | [ (.status.containerStatuses // [])[], (.status.initContainerStatuses // [])[]
                  | .state.terminated.exitCode // empty ]
              | (map(select(. != 0))[0] // .[0] // null)),
            started_at: ($l.status.startTime // null),
            finished_at: ($l.status.completionTime // $f.lastTransitionTime // null),
            pool: .metadata.annotations[$label_pool],
            retries: ([($ps | length) - 1, 0] | max),
            pods: ($ps | map(.metadata.name))
          }
        | .seconds = (if .started_at and .finished_at
                      then (.finished_at | fromdateiso8601) - (.started_at | fromdateiso8601)
                      else null end) ]
  ' >"$results_file"

  local name pod file
  for name in $(jq -r '.[].name' "$results_file"); do
    file="$EVIDENCE_DIR/$name.log"
    : >"$file"
    for pod in $(jq -r --arg n "$name" '.[] | select(.name == $n) | .pods[]' "$results_file"); do
      printf -- '--- pod %s ---\n' "$pod" >>"$file"
      kubectl -n "$NAMESPACE" logs "$pod" --all-containers >>"$file" 2>&1 || true
    done
    [[ -s "$file" ]] || echo "no pod of this Job started; see events.txt" >"$file"
  done
  if jq -e 'any(.[]; .result != "success")' "$results_file" >/dev/null; then
    kubectl -n "$NAMESPACE" describe pods >"$EVIDENCE_DIR/pods-describe.txt" 2>&1 || true
  fi

  # A work Job can print a workflow command (::error::, ::add-mask::). Stop
  # the commands while its log is printed. Failed Jobs come first.
  local stop result minutes
  stop="ci-log-$$-$RANDOM$RANDOM"
  while IFS=$'\t' read -r name result minutes; do
    printf '::group::%s/%s: %s (%s min)\n' "$STAGE" "$name" "$result" "$minutes"
    printf '::stop-commands::%s\n' "$stop"
    cat "$EVIDENCE_DIR/$name.log"
    printf '::%s::\n' "$stop"
    printf '::endgroup::\n'
  done < <(jq -r 'sort_by(.result == "success") | .[]
    | [.name, .result, (if .seconds then (.seconds / 6 | round) / 10 else "-" end)] | @tsv' "$results_file")

  summary "### CI stage \`$STAGE\`" "" "| Job | Result | Time (min) | Pool | Retries |" "|---|---|---|---|---|"
  local row
  while IFS= read -r row; do
    summary "$row"
  done < <(jq -r 'sort_by(.result == "success") | .[]
    | "| `\(.name)` | \(.result) | \(if .seconds then (.seconds / 6 | round) / 10 else "-" end) | \(.pool) | \(.retries) |"' \
    "$results_file")
  summary """Commit \`$SOURCE_SHA\`, namespace \`$NAMESPACE\`. Logs: artifact \`ci-$STAGE-logs\`."

  local failed
  failed="$(jq -r '[.[] | select(.result != "success") | "\(.name) (\(.result))"] | join(", ")' "$results_file")"
  if [[ -n "$failed" ]]; then
    log "stage $STAGE failed: $failed"
    return 1
  fi
  log "stage $STAGE passed: $job_count work Job(s)"
}

on_exit() {
  local status=$?
  trap - EXIT
  set +e
  if [[ -z "$collected" ]]; then
    collect || status=1
  fi
  # No manifest to delete in order: everything of the batch is in the namespace.
  RENDERED="" "$SCRIPT_DIR/teardown.sh" || status=1
  exit "$status"
}

# The name holds the run id and the attempt, so a namespace that is already
# here is the leftover of this same attempt.
if kubectl get namespace "$NAMESPACE" >/dev/null 2>&1; then
  log "namespace $NAMESPACE is a leftover; deleting it first"
  RENDERED="" "$SCRIPT_DIR/teardown.sh"
fi

trap on_exit EXIT
# The reaper may delete the namespace 15 minutes after the stage job must have
# ended. The label also keeps the parker from parking system-pool.
expires=$(($(date +%s) + (stage_minutes + 15) * 60))
kubectl create namespace "$NAMESPACE" >&2
kubectl label namespace "$NAMESPACE" "$LABEL_RUN=$RUN_LABEL" "$LABEL_EXPIRES=$expires" \
  "$LABEL_STAGE=$STAGE" >&2
log "namespace $NAMESPACE labeled $LABEL_RUN=$RUN_LABEL, $job_count work Job(s), stage timeout ${stage_minutes}m"

"$SCRIPT_DIR/wake.sh"

# jq reads the token from the environment and kubectl reads the object from
# stdin. `create`, not `apply`: apply would copy the object into an annotation.
jq -n --arg repository "$SOURCE_REPOSITORY" --arg sha "$SOURCE_SHA" '{
  apiVersion: "v1", kind: "Secret", type: "Opaque", metadata: { name: "ci-source" },
  data: { repository: ($repository | @base64), sha: ($sha | @base64), token: (env.SOURCE_TOKEN | @base64) }
}' | kubectl -n "$NAMESPACE" create -f - >&2
if [[ -n "${GHCR_USER:-}" ]]; then
  jq -n --arg user "$GHCR_USER" '{
    apiVersion: "v1", kind: "Secret", type: "kubernetes.io/dockerconfigjson", metadata: { name: "ghcr-pull" },
    data: { ".dockerconfigjson": ({ auths: { "ghcr.io": {
      username: $user, password: env.SOURCE_TOKEN, auth: ("\($user):\(env.SOURCE_TOKEN)" | @base64)
    } } } | tojson | @base64) }
  }' | kubectl -n "$NAMESPACE" create -f - >&2
fi
if [[ -n "$CI_GCP_SERVICE_ACCOUNT" ]]; then
  jq -n --arg name "$CI_KSA" --arg gsa "$CI_GCP_SERVICE_ACCOUNT" '{
    apiVersion: "v1", kind: "ServiceAccount", automountServiceAccountToken: false,
    metadata: { name: $name, annotations: { "iam.gke.io/gcp-service-account": $gsa } }
  }' | kubectl -n "$NAMESPACE" create -f - >&2
fi

log "applying $job_count work Job(s) of stage $STAGE"
kubectl create -f "$jobs_file" >&2

# Each Job has its own activeDeadlineSeconds, so Kubernetes ends a late Job.
# This deadline is only the net under it.
deadline=$((SECONDS + (max_minutes + 5) * 60))
polls=0 errors=0 last=""
while :; do
  if state="$(kubectl -n "$NAMESPACE" get jobs -l "$LABEL_STAGE=$STAGE" -o json 2>/dev/null)"; then
    errors=0
    finished="$(jq -c --argjson blocked "$blocked" '
      [.items[] | select(any(.status.conditions[]?; .status == "True" and (.type == "Complete" or .type == "Failed")))
        | .metadata.name] + $blocked | unique' <<<"$state")"
    done_count="$(jq 'length' <<<"$finished")"
    if [[ "$done_count" != "$last" ]]; then
      log "$done_count of $job_count work Job(s) finished"
      last="$done_count"
    fi
    (( done_count >= job_count )) && break
  else
    errors=$((errors + 1))
    if (( errors >= 6 )); then
      log "the cluster did not answer 6 times in a row"
      exit 1
    fi
  fi
  if (( SECONDS >= deadline )); then
    log "the batch did not finish ${max_minutes}m plus 5 minutes after it started"
    break
  fi
  "$sleep_fn" "$BATCH_POLL_SECONDS"
  # An image that cannot be pulled never starts. Do not wait for the timeout.
  if (( ++polls % 3 == 0 )) && pods="$(kubectl -n "$NAMESPACE" get pods -o json 2>/dev/null)"; then
    stuck="$(jq -c --argjson now "$(date +%s)" --argjson grace "$IMAGE_GRACE_SECONDS" '
      [.items[]
        | select($now - (.metadata.creationTimestamp | fromdateiso8601) > $grace)
        | select([ (.status.initContainerStatuses // [])[], (.status.containerStatuses // [])[]
                   | .state.waiting.reason // empty ]
                 | any(IN("ImagePullBackOff", "ErrImagePull", "InvalidImageName", "CreateContainerConfigError")))
        | .metadata.labels["job-name"] // empty] | unique' <<<"$pods")"
    if [[ "$stuck" != "[]" && "$(jq -c --argjson b "$blocked" '. - $b' <<<"$stuck")" != "[]" ]]; then
      log "fail fast: the pod cannot start its image: $(jq -r 'join(", ")' <<<"$stuck")"
      blocked="$(jq -c --argjson b "$blocked" '. + $b | unique' <<<"$stuck")"
    fi
  fi
done

collect
