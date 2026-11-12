#!/usr/bin/env bash
# For each repository: the job count of one recent pull request, and the
# median job minutes. One GraphQL call for all repositories (the REST quota
# on this machine is often 0). It needs `gh` and `jq`. It writes only to
# stdout. See docs/standards/ci.md, "Queue".
#
#   scripts/ci/pr-jobs.sh                      # the eight cloud repos and aw
#   scripts/ci/pr-jobs.sh core lumen           # chosen repos
#   scripts/ci/pr-jobs.sh --json               # JSON instead of a table
#
# A repo name without `/` means `faberline/<name>`. The pull request is the
# most recently updated one that is not a draft and has check runs.
set -euo pipefail

json=0
repos=()
for arg in "$@"; do
  case "$arg" in
    --json) json=1 ;;
    -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
    *) repos+=("$arg") ;;
  esac
done
if [ "${#repos[@]}" -eq 0 ]; then
  repos=(core lumen som defer tape relay keep sift aw)
fi

query='query {'
i=0
for repo in "${repos[@]}"; do
  case "$repo" in */*) full="$repo" ;; *) full="faberline/$repo" ;; esac
  owner="${full%%/*}"
  name="${full#*/}"
  query+=" r$i: repository(owner: \"$owner\", name: \"$name\") {
    nameWithOwner
    pullRequests(first: 5, orderBy: {field: UPDATED_AT, direction: DESC}, states: [OPEN, MERGED]) {
      nodes { number isDraft commits(last: 1) { nodes { commit { statusCheckRollup { contexts(first: 100) {
        nodes { __typename ... on CheckRun { name startedAt completedAt } }
      } } } } } }
    }
  }"
  i=$((i + 1))
done
query+=' }'

raw=$(gh api graphql -f query="$query")

report=$(jq '
  def minutes: ((.completedAt | fromdateiso8601) - (.startedAt | fromdateiso8601)) / 60;
  def median: sort | if length == 0 then null else .[(length - 1) / 2 | floor] end;
  [ .data[] | select(. != null) | {
      repo: .nameWithOwner,
      pr: ([ .pullRequests.nodes[]
             | select(.isDraft | not)
             | { number,
                 jobs: [ .commits.nodes[0].commit.statusCheckRollup.contexts.nodes[]?
                         | select(.__typename == "CheckRun") ] }
             | select(.jobs | length > 0) ] | first)
    }
    | { repo, pr: (.pr.number // null), jobs: ((.pr.jobs // []) | length),
        median_minutes: ((.pr.jobs // [])
          | map(select(.completedAt != null and .startedAt != null) | minutes)
          | median | if . == null then null else (. * 10 | round / 10) end) }
  ]' <<<"$raw")

if [ "$json" -eq 1 ]; then
  echo "$report"
else
  printf '%-22s %6s %6s %s\n' repo pr jobs median_min
  jq -r '.[] | [.repo, (.pr // "-"), .jobs, (.median_minutes // "-")] | @tsv' <<<"$report" |
    while IFS=$'\t' read -r repo pr jobs median; do
      printf '%-22s %6s %6s %s\n' "$repo" "$pr" "$jobs" "$median"
    done
  jq -r 'map(.jobs) | add | "total jobs for one pull request of each repo: \(.)"' <<<"$report"
fi
