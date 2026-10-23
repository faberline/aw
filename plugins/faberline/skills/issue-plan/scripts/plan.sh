#!/usr/bin/env bash
# The plan step of the issue loop, for the repository of the current
# directory. It triages the backlog, fills the todo queue, and sets
# priorities. The work step (issue-work) takes the todo issue with the
# highest priority, then the oldest.
#
# Priority labels: priority:p0 (do now) .. priority:p3 (some day). An issue
# without one counts as priority:p2.
#
# The backlog is the open issues without a state label. An issue is
# untriaged while it has no priority label.
#
# The spec of an issue is its ## Goal, ## Acceptance, ## Never, and ## Gate
# sections: in the latest comment that starts with the spec mark, else in
# the body. A spec comment lets the plan step write the spec of an issue
# that a human wrote, without editing its body.
#
# usage:
#   plan.sh status                           the loop's open issues, by state
#                                            and priority, the queue room,
#                                            and the top of the backlog
#   plan.sh new <title> <body-file> <p0-p3>  open a todo issue
#   plan.sh spec <N> <spec-file>             comment a spec on an open issue
#   plan.sh adopt <N> <p0-p3>                put a backlog issue that has a
#                                            spec into the todo queue
#   plan.sh prioritize <N> <p0-p3>           set the priority of an open issue
#   plan.sh block <N> <question-file>        comment the question on a backlog
#                                            or todo issue that needs a human
#                                            decision, move it to blocked
# exit: 0 done; 2 refused
set -euo pipefail
todo_max=${ISSUE_TODO_MAX:-3}
list_max=${ISSUE_BACKLOG_LIST:-10}
states='todo doing done blocked'
spec_mark='<!-- issue-plan:spec -->'

need_priority() { # <p0-p3>
  case "$1" in p0|p1|p2|p3) ;; *)
    echo "refused: priority must be p0, p1, p2, or p3" >&2; exit 2 ;;
  esac
}

need_sections() { # <text> <what>
  for section in Goal Acceptance Never Gate; do
    grep -q "^## $section" <<<"$1" \
      || { echo "refused: $2 has no '## $section' section" >&2; exit 2; }
  done
}

need_open() { # <N>
  state=$(gh issue view "$1" --json state --jq .state)
  [ "$state" = OPEN ] || { echo "refused: issue #$1 is not open" >&2; exit 2; }
}

need_room() {
  todo=$(gh issue list --state open --label todo --json number --jq length)
  [ "$todo" -lt "$todo_max" ] \
    || { echo "refused: $todo open todo issues already (todo_max=$todo_max)" >&2; exit 2; }
}

spec_of() { # <N>; the latest spec comment, else the body
  spec=$(gh issue view "$1" --json comments --jq \
    "[.comments[].body | select(startswith(\"$spec_mark\"))] | last // \"\"")
  [ -n "$spec" ] || spec=$(gh issue view "$1" --json body --jq .body)
  printf '%s\n' "$spec"
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
  gh issue list --state open --limit 1000 --json number,title,labels --jq "
    [.[] | select([.labels[].name] | any(. == \"todo\" or . == \"doing\" or . == \"done\" or . == \"blocked\") | not)]
    | map(. + {p: ([.labels[].name | select(test(\"^priority:p[0-9]$\"))] | first)})
    | (map(select(.p == null)) | sort_by(.number)) as \$u
    | (map(select(.p != null)) | sort_by(.p, .number)) as \$t
    | \"backlog=\(length) untriaged=\(\$u | length)\",
      (\$u[:$list_max][] | \"untriaged #\(.number) \(.title)\"),
      (\$t[:$list_max][] | \"backlog #\(.number) \(.p) \(.title)\")"
}

cmd_new() {
  title=${1:?usage: new <title> <body-file> <p0-p3>}
  body=${2:?usage: new <title> <body-file> <p0-p3>}
  priority=${3:?usage: new <title> <body-file> <p0-p3>}
  need_priority "$priority"
  [ -s "$body" ] || { echo "refused: body file is empty or missing" >&2; exit 2; }
  need_sections "$(cat "$body")" "the body"
  need_room
  if gh issue list --state open --limit 1000 --json title --jq '.[].title' | grep -qxF -- "$title"; then
    echo "refused: an open issue already has this title" >&2
    exit 2
  fi
  url=$(gh issue create --title "$title" --body-file "$body" \
    --label todo --label "priority:$priority")
  echo "opened $url"
}

cmd_spec() {
  n=${1:?usage: spec <N> <spec-file>}
  file=${2:?usage: spec <N> <spec-file>}
  [ -s "$file" ] || { echo "refused: spec file is empty or missing" >&2; exit 2; }
  need_sections "$(cat "$file")" "the spec file"
  need_open "$n"
  gh issue comment "$n" --body "$(printf '%s\n\n%s\n' "$spec_mark" "$(cat "$file")")" >/dev/null
  echo "issue #$n has a spec comment"
}

cmd_adopt() {
  n=${1:?usage: adopt <N> <p0-p3>}
  priority=${2:?usage: adopt <N> <p0-p3>}
  need_priority "$priority"
  need_open "$n"
  labels=$(gh issue view "$n" --json labels --jq '[.labels[].name] | join(",")')
  for s in $states; do
    case ",$labels," in *",$s,"*)
      echo "refused: issue #$n is already $s" >&2; exit 2 ;;
    esac
  done
  need_sections "$(spec_of "$n")" "issue #$n (body or spec comment)"
  need_room
  gh issue edit "$n" --add-label todo >/dev/null
  set_priority "$n" "$priority"
  echo "issue #$n is todo, priority:$priority"
}

cmd_prioritize() {
  n=${1:?usage: prioritize <N> <p0-p3>}
  priority=${2:?usage: prioritize <N> <p0-p3>}
  need_priority "$priority"
  need_open "$n"
  set_priority "$n" "$priority"
  echo "issue #$n is priority:$priority"
}

cmd_block() {
  n=${1:?usage: block <N> <question-file>}
  question=${2:?usage: block <N> <question-file>}
  [ -s "$question" ] || { echo "refused: question file is empty or missing" >&2; exit 2; }
  need_open "$n"
  labels=$(gh issue view "$n" --json labels --jq '[.labels[].name] | join(",")')
  for s in doing "done" blocked; do
    case ",$labels," in *",$s,"*)
      echo "refused: issue #$n is already $s" >&2; exit 2 ;;
    esac
  done
  gh issue comment "$n" --body-file "$question" >/dev/null
  case ",$labels," in
    *,todo,*) gh issue edit "$n" --remove-label todo --add-label blocked >/dev/null ;;
    *) gh issue edit "$n" --add-label blocked >/dev/null ;;
  esac
  echo "issue #$n is blocked; the controller answers, then the plan step queues it again"
}

case "${1:-}" in
  status) cmd_status ;;
  new) shift; cmd_new "$@" ;;
  spec) shift; cmd_spec "$@" ;;
  adopt) shift; cmd_adopt "$@" ;;
  prioritize) shift; cmd_prioritize "$@" ;;
  block) shift; cmd_block "$@" ;;
  *) echo "usage: plan.sh status | new <title> <body-file> <p0-p3> | spec <N> <spec-file> | adopt <N> <p0-p3> | prioritize <N> <p0-p3> | block <N> <question-file>" >&2; exit 2 ;;
esac
