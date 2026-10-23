#!/usr/bin/env bash
# The work step of the issue loop, for the repository of the current
# directory. The issue label is the state:
#   todo -> doing -> done   (issue-review merges, or moves it back to todo)
#   doing -> blocked        (a human decides, then moves it back to todo)
# One issue at a time per repository. The next todo issue is the one with
# the highest priority (priority:p0 first; no priority label counts as p2),
# then the oldest. Each issue has its own branch, issue/<N>, and worktree,
# .claude/worktrees/issue-<N>.
#
# usage:
#   work.sh next                          claim or resume one issue; prepare
#                                         its worktree
#   work.sh finish <N> <summary-file>     push, open or reuse the PR, move the
#                                         issue to done
#   work.sh block <N> <question-file>     comment the question, move the
#                                         issue to blocked
# exit: 0 done; 2 refused; 3 nothing to do (no todo issue, or another run
#   holds the lock)
set -euo pipefail
GIT=(git -c core.fsmonitor=false)
here=$(cd "$(dirname "$0")" && pwd -P)
# The git-push skill sits next to this one in the plugin.
push_sh=$(cd "$here/../.." && pwd -P)/git-push/scripts/push.sh

need_push_sh() {
  [ -x "$push_sh" ] || {
    echo "refused: $push_sh is missing; install the whole faberline plugin, not one skill" >&2
    exit 2
  }
}

common=$(cd "$("${GIT[@]}" rev-parse --git-common-dir)" && pwd)
main_checkout=$(dirname "$common")
lock="$common/issue-loop.lock"
lock_ttl=${ISSUE_LOCK_TTL:-7200}

worktree_of() { echo "$main_checkout/.claude/worktrees/issue-$1"; }

branch_of() { # <N>; an issue the AGY loop started keeps its agy/issue-<N> branch
  if "${GIT[@]}" rev-parse -q --verify "refs/remotes/origin/agy/issue-$1" >/dev/null \
    && ! "${GIT[@]}" rev-parse -q --verify "refs/remotes/origin/issue/$1" >/dev/null; then
    echo "agy/issue-$1"
  else
    echo "issue/$1"
  fi
}

release_lock() {
  [ -f "$lock" ] || return 0
  read -r held _ <"$lock" || true
  [ "${held:-}" = "$1" ] && rm -f "$lock"
  return 0
}

relabel() { # <N> <from> <to>
  gh issue edit "$1" --remove-label "$2" --add-label "$3" >/dev/null
}

cmd_next() {
  need_push_sh
  if [ -f "$lock" ]; then
    read -r held since <"$lock" || true
    age=$(( $(date +%s) - ${since:-0} ))
    if [ "$age" -lt "$lock_ttl" ]; then
      echo "busy: another run holds issue #${held:-?} (${age}s old)"
      exit 3
    fi
    echo "stale lock on issue #${held:-?} (${age}s old); resuming it"
  fi

  n=$(gh issue list --state open --label doing --json number \
    --jq 'sort_by(.number) | .[0].number // empty')
  if [ -z "$n" ]; then
    n=$(gh issue list --state open --label todo --limit 200 \
      --json number,createdAt,labels --jq '
        map({n: .number, c: .createdAt,
             p: ([.labels[].name | select(test("^priority:p[0-9]$"))
                  | ltrimstr("priority:p") | tonumber] | min // 2)})
        | sort_by(.p, .c) | .[0].n // empty')
    [ -n "$n" ] || { rm -f "$lock"; echo "idle: no open todo issue"; exit 3; }
    relabel "$n" todo doing
    echo "claimed issue #$n"
  else
    echo "resuming issue #$n (already doing)"
  fi
  echo "$n $(date +%s)" >"$lock"

  "${GIT[@]}" fetch -q origin
  wt=$(worktree_of "$n")
  branch=$(branch_of "$n")
  exclude="$common/info/exclude"
  mkdir -p "$(dirname "$exclude")"
  grep -qxF '/.claude/worktrees/' "$exclude" 2>/dev/null \
    || echo '/.claude/worktrees/' >>"$exclude"

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
  need_push_sh
  n=${1:?usage: finish <N> <summary-file>}
  summary=${2:?usage: finish <N> <summary-file>}
  [ -s "$summary" ] || { echo "refused: summary file is empty or missing" >&2; exit 2; }
  wt=$(worktree_of "$n")
  branch=$(branch_of "$n")
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

  gh issue comment "$n" --body "Work: ready for review in #$pr." >/dev/null
  relabel "$n" doing "done"
  release_lock "$n"
  gh pr view "$pr" --json number,url,state,mergeable,statusCheckRollup
}

cmd_block() {
  n=${1:?usage: block <N> <question-file>}
  question=${2:?usage: block <N> <question-file>}
  [ -s "$question" ] || { echo "refused: question file is empty or missing" >&2; exit 2; }
  gh issue comment "$n" --body-file "$question" >/dev/null
  relabel "$n" doing blocked
  release_lock "$n"
  echo "issue #$n is blocked; a human answers, then moves it back to todo"
}

case "${1:-}" in
  next) cmd_next ;;
  finish) shift; cmd_finish "$@" ;;
  block) shift; cmd_block "$@" ;;
  *) echo "usage: work.sh next | finish <N> <summary-file> | block <N> <question-file>" >&2; exit 2 ;;
esac
