#!/usr/bin/env bash
# The plan step of the issue loop, for the repository of the current
# directory. It fills the todo queue and sets priorities. The work step
# (issue-work) takes the todo issue with the highest priority, then the
# oldest.
#
# Priority labels: priority:p0 (do now) .. priority:p3 (some day). An issue
# without one counts as priority:p2.
#
# usage:
#   plan.sh status                           the loop's open issues, by state
#                                            and priority, and the queue room
#   plan.sh new <title> <body-file> <p0-p3>  open a todo issue
#   plan.sh adopt <N> <p0-p3>                put an open issue without a state
#                                            label into the todo queue
#   plan.sh prioritize <N> <p0-p3>           set the priority of an open issue
# The body of a new or adopted issue has ## Goal, ## Acceptance, ## Never,
# and ## Gate sections.
# exit: 0 done; 2 refused
set -euo pipefail
todo_max=${ISSUE_TODO_MAX:-3}
states='todo doing done blocked'

need_priority() { # <p0-p3>
  case "$1" in p0|p1|p2|p3) ;; *)
    echo "refused: priority must be p0, p1, p2, or p3" >&2; exit 2 ;;
  esac
}

need_sections() { # <body text>
  for section in Goal Acceptance Never Gate; do
    grep -q "^## $section" <<<"$1" \
      || { echo "refused: body has no '## $section' section" >&2; exit 2; }
  done
}

need_room() {
  todo=$(gh issue list --state open --label todo --json number --jq length)
  [ "$todo" -lt "$todo_max" ] \
    || { echo "refused: $todo open todo issues already (todo_max=$todo_max)" >&2; exit 2; }
}

set_priority() { # <N> <p0-p3>
  old=$(gh issue view "$1" --json labels \
    --jq '[.labels[].name | select(test("^priority:p[0-9]$"))] | join(",")')
  if [ -n "$old" ]; then
    gh issue edit "$1" --remove-label "$old" --add-label "priority:$2" >/dev/null
  else
    gh issue edit "$1" --add-label "priority:$2" >/dev/null
  fi
}

cmd_status() {
  for s in $states; do
    gh issue list --state open --label "$s" --limit 200 --json number,title,labels --jq "
      .[] | \"$s #\(.number) \([.labels[].name | select(test(\"^priority:p[0-9]$\"))] | first // \"priority:p2 (default)\") \(.title)\""
  done
  todo=$(gh issue list --state open --label todo --json number --jq length)
  echo "room=$(( todo_max > todo ? todo_max - todo : 0 )) (todo=$todo, todo_max=$todo_max)"
}

cmd_new() {
  title=${1:?usage: new <title> <body-file> <p0-p3>}
  body=${2:?usage: new <title> <body-file> <p0-p3>}
  priority=${3:?usage: new <title> <body-file> <p0-p3>}
  need_priority "$priority"
  [ -s "$body" ] || { echo "refused: body file is empty or missing" >&2; exit 2; }
  need_sections "$(cat "$body")"
  need_room
  if gh issue list --state open --limit 500 --json title --jq '.[].title' | grep -qxF -- "$title"; then
    echo "refused: an open issue already has this title" >&2
    exit 2
  fi
  url=$(gh issue create --title "$title" --body-file "$body" \
    --label todo --label "priority:$priority")
  echo "opened $url"
}

cmd_adopt() {
  n=${1:?usage: adopt <N> <p0-p3>}
  priority=${2:?usage: adopt <N> <p0-p3>}
  need_priority "$priority"
  info=$(gh issue view "$n" --json state,labels \
    --jq '"\(.state) \([.labels[].name] | join(","))"')
  [ "${info%% *}" = OPEN ] || { echo "refused: issue #$n is not open" >&2; exit 2; }
  for s in $states; do
    case ",${info#* }," in *",$s,"*)
      echo "refused: issue #$n is already $s" >&2; exit 2 ;;
    esac
  done
  need_sections "$(gh issue view "$n" --json body --jq .body)"
  need_room
  gh issue edit "$n" --add-label todo >/dev/null
  set_priority "$n" "$priority"
  echo "issue #$n is todo, priority:$priority"
}

cmd_prioritize() {
  n=${1:?usage: prioritize <N> <p0-p3>}
  priority=${2:?usage: prioritize <N> <p0-p3>}
  need_priority "$priority"
  state=$(gh issue view "$n" --json state --jq .state)
  [ "$state" = OPEN ] || { echo "refused: issue #$n is not open" >&2; exit 2; }
  set_priority "$n" "$priority"
  echo "issue #$n is priority:$priority"
}

case "${1:-}" in
  status) cmd_status ;;
  new) shift; cmd_new "$@" ;;
  adopt) shift; cmd_adopt "$@" ;;
  prioritize) shift; cmd_prioritize "$@" ;;
  *) echo "usage: plan.sh status | new <title> <body-file> <p0-p3> | adopt <N> <p0-p3> | prioritize <N> <p0-p3>" >&2; exit 2 ;;
esac
