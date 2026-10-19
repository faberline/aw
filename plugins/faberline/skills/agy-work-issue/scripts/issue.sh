#!/usr/bin/env bash
# One step of the AGY issue loop, for the repository of the current directory.
# One loop per repository works on one issue at a time. The issue label is
# the state:
#   agy:todo -> agy:doing -> agy:done   (Claude reviews)
# agy:done means the loop's turn is over: a PR waits for review, or the last
# issue comment is a question. Claude merges a PR it accepts. Otherwise Claude
# writes the review or the answer and moves the issue back to agy:todo; the
# next run continues the same branch, agy/issue-<N>.
#
# usage:
#   issue.sh labels                       create or update the three labels
#   issue.sh next                         claim or resume one issue; prepare
#                                         its worktree
#   issue.sh finish <N> <summary-file>    push, open or reuse the PR, move the
#                                         issue to agy:done
#   issue.sh ask <N> <question-file>      comment the question, move the
#                                         issue to agy:done
# exit: 0 done; 2 refused; 3 nothing to do (no agy:todo issue, or another run
#   holds the lock)
set -euo pipefail
GIT=(git -c core.fsmonitor=false)
here=$(cd "$(dirname "$0")" && pwd)
push_sh="$here/../../git-push/scripts/push.sh"

common=$(cd "$("${GIT[@]}" rev-parse --git-common-dir)" && pwd)
main_checkout=$(dirname "$common")
lock="$common/agy-issue.lock"
lock_ttl=${AGY_LOCK_TTL:-7200}

worktree_of() { echo "$main_checkout/.agents/worktrees/issue-$1"; }

release_lock() {
  [ -f "$lock" ] || return 0
  read -r held _ <"$lock" || true
  [ "${held:-}" = "$1" ] && rm -f "$lock"
  return 0
}

relabel() { # <N> <from> <to>
  gh issue edit "$1" --remove-label "$2" --add-label "$3" >/dev/null
}

cmd_labels() {
  gh label create agy:todo    --force --color 0E8A16 --description "Ready for the AGY loop"
  gh label create agy:doing   --force --color FBCA04 --description "The AGY loop is working on it"
  gh label create agy:done    --force --color 1D76DB --description "AGY is done; Claude reviews the PR or answers the question"
}

cmd_next() {
  if [ -f "$lock" ]; then
    read -r held since <"$lock" || true
    age=$(( $(date +%s) - ${since:-0} ))
    if [ "$age" -lt "$lock_ttl" ]; then
      echo "busy: another run holds issue #${held:-?} (${age}s old)"
      exit 3
    fi
    echo "stale lock on issue #${held:-?} (${age}s old); resuming it"
  fi

  n=$(gh issue list --state open --label agy:doing --json number \
    --jq 'sort_by(.number) | .[0].number // empty')
  if [ -z "$n" ]; then
    n=$(gh issue list --state open --label agy:todo --limit 200 \
      --json number,createdAt --jq 'sort_by(.createdAt) | .[0].number // empty')
    [ -n "$n" ] || { rm -f "$lock"; echo "idle: no open agy:todo issue"; exit 3; }
    relabel "$n" agy:todo agy:doing
    echo "claimed issue #$n"
  else
    echo "resuming issue #$n (already agy:doing)"
  fi
  echo "$n $(date +%s)" >"$lock"

  wt=$(worktree_of "$n")
  branch="agy/issue-$n"
  exclude="$common/info/exclude"
  mkdir -p "$(dirname "$exclude")"
  grep -qxF '/.agents/worktrees/' "$exclude" 2>/dev/null \
    || echo '/.agents/worktrees/' >>"$exclude"

  "${GIT[@]}" fetch -q origin
  if [ -d "$wt" ]; then
    echo "reusing worktree"
  elif "${GIT[@]}" rev-parse -q --verify "refs/remotes/origin/$branch" >/dev/null; then
    "${GIT[@]}" worktree add -q -B "$branch" "$wt" "origin/$branch"
    "${GIT[@]}" -C "$wt" branch -q --set-upstream-to "origin/$branch"
  elif "${GIT[@]}" rev-parse -q --verify "refs/heads/$branch" >/dev/null; then
    "${GIT[@]}" worktree add -q "$wt" "$branch"
  else
    "${GIT[@]}" worktree add -q --no-track -b "$branch" "$wt" origin/main
  fi

  pr=$(gh pr list --head "$branch" --state open --json number \
    --jq '.[0].number // empty')
  echo "issue=$n"
  echo "branch=$branch"
  echo "worktree=$wt"
  echo "pr=${pr:-none}"
}

cmd_finish() {
  n=${1:?usage: finish <N> <summary-file>}
  summary=${2:?usage: finish <N> <summary-file>}
  [ -s "$summary" ] || { echo "refused: summary file is empty or missing" >&2; exit 2; }
  wt=$(worktree_of "$n")
  branch="agy/issue-$n"
  [ -d "$wt" ] || { echo "refused: no worktree at $wt" >&2; exit 2; }
  [ "$("${GIT[@]}" -C "$wt" branch --show-current)" = "$branch" ] \
    || { echo "refused: $wt is not on $branch" >&2; exit 2; }
  [ -z "$("${GIT[@]}" -C "$wt" status --porcelain)" ] \
    || { echo "refused: uncommitted changes in $wt; commit them first" >&2; exit 2; }
  [ "$("${GIT[@]}" -C "$wt" rev-list --count origin/main..HEAD)" -gt 0 ] \
    || { echo "refused: $branch has no commits over origin/main" >&2; exit 2; }

  (cd "$wt" && "$push_sh")

  pr=$(gh pr list --head "$branch" --state open --json number \
    --jq '.[0].number // empty')
  if [ -z "$pr" ]; then
    title=$(gh issue view "$n" --json title --jq .title)
    body=$(printf 'Closes #%s\n\n%s\n' "$n" "$(cat "$summary")")
    gh pr create --base main --head "$branch" --title "$title" --body "$body" >/dev/null
    pr=$(gh pr list --head "$branch" --state open --json number --jq '.[0].number')
    echo "created PR #$pr"
  else
    gh pr comment "$pr" --body-file "$summary" >/dev/null
    echo "updated PR #$pr"
  fi

  gh issue comment "$n" --body "AGY: ready for review in #$pr." >/dev/null
  relabel "$n" agy:doing agy:done
  release_lock "$n"
  gh pr view "$pr" --json number,url,state,mergeable,statusCheckRollup
}

cmd_ask() {
  n=${1:?usage: ask <N> <question-file>}
  question=${2:?usage: ask <N> <question-file>}
  [ -s "$question" ] || { echo "refused: question file is empty or missing" >&2; exit 2; }
  gh issue comment "$n" --body-file "$question" >/dev/null
  relabel "$n" agy:doing agy:done
  release_lock "$n"
  echo "issue #$n is agy:done with a question"
}

case "${1:-}" in
  labels) cmd_labels ;;
  next) cmd_next ;;
  finish) shift; cmd_finish "$@" ;;
  ask) shift; cmd_ask "$@" ;;
  *) echo "usage: issue.sh labels | next | finish <N> <summary-file> | ask <N> <question-file>" >&2; exit 2 ;;
esac
