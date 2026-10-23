#!/usr/bin/env bash
# Review only after CI-owned done, independently verify the same current head.
# pass records acceptance; fail/block report evidence. No lifecycle or merge writes.
set -euo pipefail
GIT=(git -c core.fsmonitor=false)
here=$(cd "$(dirname "$0")" && pwd -P)
ci_py=$(cd "$here/../.." && pwd -P)/issue-ci/scripts/handoff.py
verify_ci() {
  local repo
  repo=$(gh repo view --json nameWithOwner --jq .nameWithOwner)
  [ -n "${ISSUE_CI_CONFIG:-}" ] || { echo "refused: set ISSUE_CI_CONFIG to trusted gate config" >&2; exit 2; }
  python3 "$ci_py" --repo "$repo" --config "$ISSUE_CI_CONFIG" --pr "$1" --head "$2"
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
  n=$(gh issue list --state open --label "done" --limit 200 \
    --json number,updatedAt --jq 'sort_by(.updatedAt) | .[0].number // empty')
  if [ -z "$n" ]; then
    echo "idle: no open done issue"
    exit 3
  fi

  "${GIT[@]}" fetch -q origin
  read -r pr branch <<<"$(pr_and_branch_of "$n")" || true
  if [ -z "${pr:-}" ]; then
    echo "refused: issue #$n is done but has no open PR from issue/$n; report missing association; leave lifecycle unchanged" >&2
    echo "issue=$n"
    exit 2
  fi

  head=$(gh pr view "$pr" --json headRefOid --jq .headRefOid)
  verify_ci "$pr" "$head"
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
  echo "base=$("${GIT[@]}" merge-base origin/main "$head")"
}

cmd_pass() {
  n=${1:?usage: pass <N> <head-sha>}
  reviewed=${2:?usage: pass <N> <head-sha>}
  need_done "$n"
  read -r pr branch <<<"$(pr_and_branch_of "$n")" || true
  [ -n "${pr:-}" ] || { echo "refused: issue #$n has no open PR from issue/$n" >&2; exit 2; }
  head=$(gh pr view "$pr" --json headRefOid --jq .headRefOid)
  [ "$head" = "$reviewed" ] \
    || { echo "refused: PR #$pr moved to $head after the review of $reviewed; review again" >&2; exit 2; }

  verify_ci "$pr" "$reviewed"
  # Verify one more time after CI collection before recording acceptance.
  [ "$(gh pr view "$pr" --json headRefOid --jq .headRefOid)" = "$reviewed" ] \
    || { echo "refused: PR head moved" >&2; exit 2; }
  gh pr comment "$pr" --body "Review: accepted head $reviewed after independently verifying required CI. Awaiting explicit human merge." >/dev/null
  echo "accepted PR #$pr at $reviewed; lifecycle unchanged; no merge"
}

cmd_fail() {
  n=${1:?usage: fail <N> <review-file>}
  review=${2:?usage: fail <N> <review-file>}
  need_file "$review" review
  need_done "$n"
  read -r pr _ <<<"$(pr_and_branch_of "$n")" || true
  [ -n "${pr:-}" ] || { echo "refused: issue #$n has no open PR from issue/$n" >&2; exit 2; }
  gh pr comment "$pr" --body-file "$review" >/dev/null
  gh issue comment "$n" --body "Review: changes requested; see #$pr. Human triage required; lifecycle unchanged." >/dev/null
  drop_review_tree "$n"
  echo "review evidence reported for #$n; lifecycle unchanged"
}

cmd_block() {
  n=${1:?usage: block <N> <question-file>}
  question=${2:?usage: block <N> <question-file>}
  need_file "$question" question
  need_done "$n"
  gh issue comment "$n" --body-file "$question" >/dev/null
  drop_review_tree "$n"
  echo "question reported for #$n; lifecycle unchanged"
}

case "${1:-}" in
  next) cmd_next ;;
  pass) shift; cmd_pass "$@" ;;
  fail) shift; cmd_fail "$@" ;;
  block) shift; cmd_block "$@" ;;
  *) echo "usage: review.sh next | pass <N> <head-sha> | fail <N> <review-file> | block <N> <question-file>" >&2; exit 2 ;;
esac
