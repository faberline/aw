#!/usr/bin/env bash
# The controller of the issue loops, run from the aw repository. Each app
# repository runs plan, work, and review in their own conversations. A step
# that needs a human decision moves its issue to blocked and comments the
# question. The controller finds those issues in all repositories of the
# owner, answers what a known user decision covers, and asks the user the
# rest. All talk goes through the issues.
#
# usage:
#   control.sh scan                       print each open blocked issue that
#                                         is new or changed since the last scan
#   control.sh answer <owner/repo> <N> <answer-file>
#                                         comment the answer, take the issue
#                                         out of blocked; the plan step puts
#                                         it back in the todo queue
# exit: 0 done; 2 refused; 3 nothing new
# env: ISSUE_CONTROL_OWNER (default faberline)
set -euo pipefail
owner=${ISSUE_CONTROL_OWNER:-faberline}
state=${XDG_STATE_HOME:-$HOME/.local/state}/faberline/issue-control
seen="$state/$owner.seen"

cmd_scan() {
  mkdir -p "$state"
  touch "$seen"
  now=$(gh search issues --owner "$owner" --label blocked --state open --limit 100 \
    --json repository,number,title,updatedAt,url \
    --jq '.[] | "\(.repository.nameWithOwner)#\(.number) \(.updatedAt) \(.url) \(.title)"' | LC_ALL=C sort)
  new=$(LC_ALL=C comm -13 "$seen" <(printf '%s\n' "$now" | sed '/^$/d'))
  printf '%s\n' "$now" | sed '/^$/d' > "$seen"
  echo "open blocked in $owner: $(sed '/^$/d' "$seen" | wc -l | tr -d ' ')"
  [ -n "$new" ] || exit 3
  printf '%s\n' "$new" | sed 's/^/NEW /'
}

cmd_answer() {
  repo=${1:?usage: answer <owner/repo> <N> <answer-file>}
  n=${2:?usage: answer <owner/repo> <N> <answer-file>}
  answer=${3:?usage: answer <owner/repo> <N> <answer-file>}
  [ -s "$answer" ] || { echo "refused: answer file is empty or missing" >&2; exit 2; }
  labels=$(gh issue view "$n" --repo "$repo" --json labels --jq '[.labels[].name] | join(" ")')
  case " $labels " in
    *" blocked "*) ;;
    *) echo "refused: $repo#$n is not blocked (labels: $labels)" >&2; exit 2 ;;
  esac
  gh issue comment "$n" --repo "$repo" --body-file "$answer" >/dev/null
  gh issue edit "$n" --repo "$repo" --remove-label blocked >/dev/null
  echo "$repo#$n answered; the plan step puts it back in the todo queue"
}

case "${1:-}" in
  scan) shift; cmd_scan "$@" ;;
  answer) shift; cmd_answer "$@" ;;
  *) echo "usage: control.sh scan | answer <owner/repo> <N> <answer-file>" >&2; exit 2 ;;
esac
