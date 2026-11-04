#!/usr/bin/env bash
# Review only after CI-owned done, independently verify the same current head.
# pass records acceptance; fail/block report evidence. No lifecycle or merge writes.
set -euo pipefail
GIT=(git -c core.fsmonitor=false)
here=$(cd "$(dirname "$0")" && pwd -P)
receipt_py="$here/receipt.py"
ci_py=$(cd "$here/../.." && pwd -P)/issue-ci/scripts/handoff.py
verify_ci() {
  local repo review_policy_dir review_policy_file
  repo=$(gh repo view --json nameWithOwner --jq .nameWithOwner)
  [ -n "${ISSUE_CI_CONFIG:-}" ] || { echo "refused: set ISSUE_CI_CONFIG to trusted gate config" >&2; exit 2; }
  review_policy_dir=$(mktemp -d "${TMPDIR:-/tmp}/aw-review-policy.XXXXXX")
  review_policy_file="$review_policy_dir/gates.json"
  if ! python3 "$receipt_py" verify-policy "$repo" "$3" "$ISSUE_CI_CONFIG" > "$review_policy_file"; then
    rm -f "$review_policy_file"; rmdir "$review_policy_dir"; return 2
  fi
  if ! python3 "$ci_py" --repo "$repo" --config "$review_policy_file" --pr "$1" --head "$2"; then
    rm -f "$review_policy_file"; rmdir "$review_policy_dir"; return 2
  fi
  if ! python3 "$receipt_py" verify-policy "$repo" "$3" "$ISSUE_CI_CONFIG" > /dev/null; then
    rm -f "$review_policy_file"; rmdir "$review_policy_dir"; return 2
  fi
  rm -f "$review_policy_file"; rmdir "$review_policy_dir"
}

common=$(cd "$("${GIT[@]}" rev-parse --git-common-dir)" && pwd)
main_checkout=$(dirname "$common")

review_tree_of() { echo "$main_checkout/.claude/worktrees/review-$1"; }

pr_and_branch_of() { # <N> -> "<pr> <branch>" for the open PR from issue/<N>
  # (or agy/issue-<N>, for an issue the AGY loop started), or nothing
  for b in "issue/$1" "agy/issue-$1"; do
    p=$(gh pr list --head "$b" --state open --json number --jq '.[0].number // empty')
    [ -n "$p" ] && { echo "$p $b"; return 0; }
  done
  return 0
}

need_done() { # <N>
  labels=$(gh issue view "$1" --json labels --jq '[.labels[].name] | join(",")')
  case ",$labels," in
    *,done,*) ;;
    *) echo "refused: issue #$1 is not done (labels: ${labels:-none})" >&2; exit 2 ;;
  esac
}

need_file() { # <path> <what>
  [ -s "$1" ] || { echo "refused: $2 file is empty or missing" >&2; exit 2; }
}

checks_of() { # <pr> -> the number of checks on the PR, run or not
  gh pr view "$1" --json statusCheckRollup --jq '.statusCheckRollup | length'
}

drop_review_tree() { # <N>
  wt=$(review_tree_of "$1")
  [ -d "$wt" ] || return 0
  if [ -n "$("${GIT[@]}" -C "$wt" status --porcelain)" ]; then
    echo "kept $wt: it has local changes"
    return 0
  fi
  "${GIT[@]}" worktree remove "$wt"
}

cmd_next() {
  candidates=$(gh api --paginate --slurp \
    'repos/{owner}/{repo}/issues?state=open&labels=done&per_page=100' \
    | python3 -c 'import json, sys
pages = json.load(sys.stdin)
if not isinstance(pages, list) or any(not isinstance(page, list) for page in pages):
    raise ValueError("expected complete paginated issue arrays")
issues = [issue for page in pages for issue in page if "pull_request" not in issue]
for issue in sorted(issues, key=lambda issue: issue["updated_at"]):
    print(issue["number"])
')
  if [ -z "$candidates" ]; then
    echo "idle: no unreviewed current-head qualified done issue"
    exit 3
  fi
  "${GIT[@]}" fetch -q origin
  selected=false
  for n in $candidates; do
    read -r pr branch <<<"$(pr_and_branch_of "$n")" || true
    if [ -z "${pr:-}" ]; then
      echo "skip: done issue #$n has no associated open PR; lifecycle unchanged" >&2
      continue
    fi
    head=$(gh pr view "$pr" --json headRefOid --jq .headRefOid)
    base=$(gh pr view "$pr" --json baseRefOid --jq .baseRefOid)
    if python3 "$receipt_py" notify "$n" "$pr" "$head" "$base"; then
      :
    else
      notify_status=$?
      if [ "$notify_status" != 3 ]; then
        echo "skip: could not wake CI for issue #$n; retry on the next run" >&2
      fi
    fi
    if python3 "$receipt_py" seen "$n" "$pr" "$head" "$base"; then
      continue
    else
      status=$?
      [ "$status" = 1 ] || return "$status"
    fi
    # A pending/invalid CI head must not starve other qualified done issues.
    if ! verify_ci "$pr" "$head" "$base"; then
      echo "skip: issue #$n current head is not qualified" >&2
      continue
    fi
    if [ "$(gh pr view "$pr" --json headRefOid --jq .headRefOid)" != "$head" ] || \
       [ "$(gh pr view "$pr" --json baseRefOid --jq .baseRefOid)" != "$base" ]; then
      echo "skip: issue #$n PR head or base changed during CI verification" >&2
      continue
    fi
    selected=true
    break
  done
  if [ "$selected" != true ]; then
    echo "idle: no unreviewed current-head qualified done issue"
    exit 3
  fi
  wt=$(review_tree_of "$n")
  exclude="$common/info/exclude"
  mkdir -p "$(dirname "$exclude")"
  grep -qxF '/.claude/worktrees/' "$exclude" 2>/dev/null \
    || echo '/.claude/worktrees/' >>"$exclude"
  "${GIT[@]}" fetch -q origin "$branch"
  if [ -d "$wt" ]; then
    [ -z "$("${GIT[@]}" -C "$wt" status --porcelain)" ] \
      || { echo "refused: $wt has local changes; review must not edit the PR" >&2; exit 2; }
    "${GIT[@]}" -C "$wt" checkout -q --detach "$head"
  else
    "${GIT[@]}" worktree add -q --detach "$wt" "$head"
  fi
  echo "issue=$n"
  echo "pr=$pr"
  echo "head=$head"
  echo "worktree=$wt"
  echo "checks=$(checks_of "$pr")"
  echo "base=$base"
}

cmd_pass() {
  n=${1:?usage: pass <N> <head-sha> <base-sha>}
  reviewed=${2:?usage: pass <N> <head-sha> <base-sha>}
  reviewed_base=${3:?usage: pass <N> <head-sha> <base-sha>}
  need_done "$n"
  read -r pr branch <<<"$(pr_and_branch_of "$n")" || true
  [ -n "${pr:-}" ] || { echo "refused: issue #$n has no open PR from issue/$n" >&2; exit 2; }
  head=$(gh pr view "$pr" --json headRefOid --jq .headRefOid)
  [ "$head" = "$reviewed" ] \
    || { echo "refused: PR #$pr moved to $head after the review of $reviewed; review again" >&2; exit 2; }
  [ "$(gh pr view "$pr" --json baseRefOid --jq .baseRefOid)" = "$reviewed_base" ] \
    || { echo "refused: PR base moved; review again" >&2; exit 2; }

  verify_ci "$pr" "$reviewed" "$reviewed_base"
  # Verify one more time after CI collection before recording acceptance.
  [ "$(gh pr view "$pr" --json headRefOid --jq .headRefOid)" = "$reviewed" ] \
    || { echo "refused: PR head moved" >&2; exit 2; }
  [ "$(gh pr view "$pr" --json baseRefOid --jq .baseRefOid)" = "$reviewed_base" ] \
    || { echo "refused: PR base moved" >&2; exit 2; }
  python3 "$receipt_py" record "$n" "$pr" "$reviewed" "$reviewed_base" pass
  echo "accepted PR #$pr at $reviewed; lifecycle unchanged; no merge"
}

cmd_report() {
  outcome=$1
  n=${2:?usage: fail|block <N> <head-sha> <base-sha> <evidence-file>}
  reviewed=${3:?usage: fail|block <N> <head-sha> <base-sha> <evidence-file>}
  reviewed_base=${4:?usage: fail|block <N> <head-sha> <base-sha> <evidence-file>}
  evidence=${5:?usage: fail|block <N> <head-sha> <base-sha> <evidence-file>}
  need_file "$evidence" evidence
  need_done "$n"
  read -r pr _ <<<"$(pr_and_branch_of "$n")" || true
  [ -n "${pr:-}" ] || { echo "refused: issue #$n has no open PR" >&2; exit 2; }
  python3 "$receipt_py" record "$n" "$pr" "$reviewed" "$reviewed_base" "$outcome" "$evidence"
  if [ "$outcome" = fail ]; then
    if python3 "$receipt_py" notify-required "$n" "$pr" "$reviewed" "$reviewed_base" "$evidence"; then
      :
    else
      notify_status=$?
      [ "$notify_status" = 3 ] || return "$notify_status"
      echo "review evidence saved; CI feedback is not enabled and the issue was not returned to todo" >&2
    fi
  fi
  drop_review_tree "$n"
  echo "review evidence reported for #$n at $reviewed; lifecycle unchanged"
}

case "${1:-}" in
  next) cmd_next ;;
  pass) shift; cmd_pass "$@" ;;
  fail) shift; cmd_report fail "$@" ;;
  block) shift; cmd_report block "$@" ;;
  *) echo "usage: review.sh next | pass <N> <head-sha> <base-sha> | fail <N> <head-sha> <base-sha> <review-file> | block <N> <head-sha> <base-sha> <question-file>" >&2; exit 2 ;;
esac
