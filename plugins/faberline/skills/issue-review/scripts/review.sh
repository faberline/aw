#!/usr/bin/env bash
# The review step of the issue loop, for the repository of the current
# directory. The work step (issue-work) moves an issue
#   todo -> doing -> done
# This script takes the oldest done issue and closes the loop: a passing PR
# is merged, which closes the issue; a failing PR goes back to todo; a review
# that needs a human decision moves the issue to blocked.
#
# usage:
#   review.sh next                        pick the oldest done issue; check
#                                         out its PR head for review
#   review.sh pass <N> <head-sha>         merge the PR (it must still be at
#                                         <head-sha>), close the issue, clean up
#   review.sh fail <N> <review-file>      comment the review on the PR, move
#                                         the issue back to todo
#   review.sh block <N> <question-file>   comment the question on the issue,
#                                         move it to blocked
# exit: 0 done; 2 refused; 3 nothing to review (next only)
set -euo pipefail
GIT=(git -c core.fsmonitor=false)
here=$(cd "$(dirname "$0")" && pwd -P)
# The gh-merge-pr skill sits next to this one in the plugin.
merge_sh=$(cd "$here/../.." && pwd -P)/gh-merge-pr/scripts/merge-pr.sh

common=$(cd "$("${GIT[@]}" rev-parse --git-common-dir)" && pwd)
main_checkout=$(dirname "$common")
lock="$common/issue-loop.lock"

review_tree_of() { echo "$main_checkout/.claude/worktrees/review-$1"; }
work_tree_of() { echo "$main_checkout/.claude/worktrees/issue-$1"; }

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
    echo "refused: issue #$n is done but has no open PR from issue/$n; move it back to todo or block it" >&2
    echo "issue=$n"
    exit 2
  fi

  head=$(gh pr view "$pr" --json headRefOid --jq .headRefOid)
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
  echo "base=$("${GIT[@]}" merge-base origin/main "$head")"
}

cmd_pass() {
  n=${1:?usage: pass <N> <head-sha>}
  reviewed=${2:?usage: pass <N> <head-sha>}
  [ -x "$merge_sh" ] || {
    echo "refused: $merge_sh is missing; install the whole faberline plugin, not one skill" >&2
    exit 2
  }
  need_done "$n"
  read -r pr branch <<<"$(pr_and_branch_of "$n")" || true
  [ -n "${pr:-}" ] || { echo "refused: issue #$n has no open PR from issue/$n" >&2; exit 2; }
  head=$(gh pr view "$pr" --json headRefOid --jq .headRefOid)
  [ "$head" = "$reviewed" ] \
    || { echo "refused: PR #$pr moved to $head after the review of $reviewed; review again" >&2; exit 2; }

  # issue/* branches are per-issue; the merge removes the remote one.
  "$merge_sh" "$pr" --delete-branch

  state=$(gh issue view "$n" --json state --jq .state)
  if [ "$state" != CLOSED ]; then
    gh issue close "$n" --comment "Merged in #$pr." >/dev/null
  fi
  gh issue edit "$n" --remove-label "done" >/dev/null

  drop_review_tree "$n"
  wt=$(work_tree_of "$n")
  held=''
  [ -f "$lock" ] && read -r held _ <"$lock"
  if [ -d "$wt" ] && [ "$held" != "$n" ] && [ -z "$("${GIT[@]}" -C "$wt" status --porcelain)" ]; then
    "${GIT[@]}" worktree remove "$wt"
    "${GIT[@]}" branch -q -D "$branch" 2>/dev/null || true
  fi
  echo "issue #$n merged in PR #$pr and closed"
}

cmd_fail() {
  n=${1:?usage: fail <N> <review-file>}
  review=${2:?usage: fail <N> <review-file>}
  need_file "$review" review
  need_done "$n"
  read -r pr _ <<<"$(pr_and_branch_of "$n")" || true
  [ -n "${pr:-}" ] || { echo "refused: issue #$n has no open PR from issue/$n" >&2; exit 2; }
  gh pr comment "$pr" --body-file "$review" >/dev/null
  gh issue comment "$n" --body "Review: failed; see #$pr." >/dev/null
  drop_review_tree "$n"
  gh issue edit "$n" --remove-label "done" --add-label todo >/dev/null
  echo "issue #$n is todo again"
}

cmd_block() {
  n=${1:?usage: block <N> <question-file>}
  question=${2:?usage: block <N> <question-file>}
  need_file "$question" question
  need_done "$n"
  gh issue comment "$n" --body-file "$question" >/dev/null
  drop_review_tree "$n"
  gh issue edit "$n" --remove-label "done" --add-label blocked >/dev/null
  echo "issue #$n is blocked; a human answers, then moves it back to todo"
}

case "${1:-}" in
  next) cmd_next ;;
  pass) shift; cmd_pass "$@" ;;
  fail) shift; cmd_fail "$@" ;;
  block) shift; cmd_block "$@" ;;
  *) echo "usage: review.sh next | pass <N> <head-sha> | fail <N> <review-file> | block <N> <question-file>" >&2; exit 2 ;;
esac
