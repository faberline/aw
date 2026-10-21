#!/usr/bin/env bash
# Claude's side of the AGY issue loop, for the repository of the current
# directory. The AGY loop (agy-work-issue) moves an issue
#   agy:todo -> agy:doing -> agy:done
# This script takes the oldest agy:done issue and closes the loop: a passing
# PR is merged, a failing PR or an answered question goes back to agy:todo.
# With nothing to review, Claude plans the next work and opens new agy:todo
# issues with `new`.
#
# usage:
#   review.sh next                        pick the oldest agy:done issue;
#                                         check out its PR head for review
#   review.sh pass <N> <head-sha>         merge the PR (it must still be at
#                                         <head-sha>), then clean up
#   review.sh fail <N> <review-file>      comment the review on the PR, move
#                                         the issue back to agy:todo
#   review.sh answer <N> <answer-file>    comment the answer on the issue,
#                                         move it back to agy:todo
#   review.sh new <title> <body-file>     open an agy:todo issue
# exit: 0 done; 2 refused; 3 nothing to review (next only)
set -euo pipefail
GIT=(git -c core.fsmonitor=false)
here=$(cd "$(dirname "$0")" && pwd -P)
# The gh-merge-pr skill sits next to this one, in place or in an AGY plugin copy.
merge_sh=$(cd "$here/../.." && pwd -P)/gh-merge-pr/scripts/merge-pr.sh
todo_max=${AGY_TODO_MAX:-3}

common=$(cd "$("${GIT[@]}" rev-parse --git-common-dir)" && pwd)
main_checkout=$(dirname "$common")
lock="$common/agy-issue.lock"

review_tree_of() { echo "$main_checkout/.agents/worktrees/review-$1"; }
agy_tree_of() { echo "$main_checkout/.agents/worktrees/issue-$1"; }

open_pr_of() { # <N> -> the open PR from agy/issue-<N>, or nothing
  gh pr list --head "agy/issue-$1" --state open --json number --jq '.[0].number // empty'
}

need_done() { # <N>
  labels=$(gh issue view "$1" --json labels --jq '[.labels[].name] | join(",")')
  case ",$labels," in
    *,agy:done,*) ;;
    *) echo "refused: issue #$1 is not agy:done (labels: ${labels:-none})" >&2; exit 2 ;;
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

to_todo() { # <N>
  gh issue edit "$1" --remove-label agy:done --add-label agy:todo >/dev/null
  echo "issue #$1 is agy:todo again"
}

cmd_next() {
  todo=$(gh issue list --state open --label agy:todo --json number --jq length)
  doing=$(gh issue list --state open --label agy:doing --json number --jq length)
  n=$(gh issue list --state open --label agy:done --limit 200 \
    --json number,updatedAt --jq 'sort_by(.updatedAt) | .[0].number // empty')
  if [ -z "$n" ]; then
    echo "idle: no open agy:done issue (agy:todo=$todo, agy:doing=$doing, todo_max=$todo_max)"
    exit 3
  fi

  "${GIT[@]}" fetch -q origin
  pr=$(open_pr_of "$n")
  echo "issue=$n"
  echo "pr=${pr:-none}"
  if [ -z "$pr" ]; then
    echo "kind=question"
    return 0
  fi

  head=$(gh pr view "$pr" --json headRefOid --jq .headRefOid)
  wt=$(review_tree_of "$n")
  exclude="$common/info/exclude"
  mkdir -p "$(dirname "$exclude")"
  grep -qxF '/.agents/worktrees/' "$exclude" 2>/dev/null \
    || echo '/.agents/worktrees/' >>"$exclude"
  "${GIT[@]}" fetch -q origin "agy/issue-$n"
  if [ -d "$wt" ]; then
    [ -z "$("${GIT[@]}" -C "$wt" status --porcelain)" ] \
      || { echo "refused: $wt has local changes; review must not edit the PR" >&2; exit 2; }
    "${GIT[@]}" -C "$wt" checkout -q --detach "$head"
  else
    "${GIT[@]}" worktree add -q --detach "$wt" "$head"
  fi
  echo "kind=pr"
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
  pr=$(open_pr_of "$n")
  [ -n "$pr" ] || { echo "refused: issue #$n has no open PR from agy/issue-$n" >&2; exit 2; }
  head=$(gh pr view "$pr" --json headRefOid --jq .headRefOid)
  [ "$head" = "$reviewed" ] \
    || { echo "refused: PR #$pr moved to $head after the review of $reviewed; review again" >&2; exit 2; }

  # agy/* branches are per-issue; the merge removes the remote one.
  "$merge_sh" "$pr" --delete-branch

  state=$(gh issue view "$n" --json state --jq .state)
  if [ "$state" != CLOSED ]; then
    gh issue close "$n" --comment "Merged in #$pr." >/dev/null
  fi
  gh issue edit "$n" --remove-label agy:done >/dev/null

  drop_review_tree "$n"
  wt=$(agy_tree_of "$n")
  held=''
  [ -f "$lock" ] && read -r held _ <"$lock"
  if [ -d "$wt" ] && [ "$held" != "$n" ] && [ -z "$("${GIT[@]}" -C "$wt" status --porcelain)" ]; then
    "${GIT[@]}" worktree remove "$wt"
    "${GIT[@]}" branch -q -D "agy/issue-$n" 2>/dev/null || true
  fi
  echo "issue #$n merged in PR #$pr and closed"
}

cmd_fail() {
  n=${1:?usage: fail <N> <review-file>}
  review=${2:?usage: fail <N> <review-file>}
  need_file "$review" review
  need_done "$n"
  pr=$(open_pr_of "$n")
  [ -n "$pr" ] || { echo "refused: issue #$n has no open PR; use answer for a question" >&2; exit 2; }
  gh pr comment "$pr" --body-file "$review" >/dev/null
  gh issue comment "$n" --body "Claude: review failed; see #$pr." >/dev/null
  drop_review_tree "$n"
  to_todo "$n"
}

cmd_answer() {
  n=${1:?usage: answer <N> <answer-file>}
  answer=${2:?usage: answer <N> <answer-file>}
  need_file "$answer" answer
  need_done "$n"
  gh issue comment "$n" --body-file "$answer" >/dev/null
  to_todo "$n"
}

cmd_new() {
  title=${1:?usage: new <title> <body-file>}
  body=${2:?usage: new <title> <body-file>}
  need_file "$body" body
  for section in Goal Acceptance Never Gate; do
    grep -q "^## $section" "$body" \
      || { echo "refused: body has no '## $section' section" >&2; exit 2; }
  done
  todo=$(gh issue list --state open --label agy:todo --json number --jq length)
  [ "$todo" -lt "$todo_max" ] \
    || { echo "refused: $todo open agy:todo issues already (todo_max=$todo_max)" >&2; exit 2; }
  if gh issue list --state open --limit 500 --json title --jq '.[].title' | grep -qxF -- "$title"; then
    echo "refused: an open issue already has this title" >&2
    exit 2
  fi
  url=$(gh issue create --title "$title" --body-file "$body" --label agy:todo)
  echo "opened $url"
}

case "${1:-}" in
  next) cmd_next ;;
  pass) shift; cmd_pass "$@" ;;
  fail) shift; cmd_fail "$@" ;;
  answer) shift; cmd_answer "$@" ;;
  new) shift; cmd_new "$@" ;;
  *) echo "usage: review.sh next | pass <N> <head-sha> | fail <N> <review-file> | answer <N> <answer-file> | new <title> <body-file>" >&2; exit 2 ;;
esac
