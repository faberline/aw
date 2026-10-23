#!/usr/bin/env bash
# The issue loop, for the repository of the current directory. Three steps
# run as three loops, side by side. The issue label is the state:
#   plan:   opens issues as todo and sets their priority
#   work:   todo -> doing -> done (a PR that closes the issue)
#   review: done -> merged and closed, or back to todo
# Any step can move an issue to blocked when a human must decide.
#
# usage:
#   loop.sh labels   create the state and priority labels; rename the old
#                    agy:todo, agy:doing, and agy:done labels
#   loop.sh status   print the counts and each blocked issue
# exit: 0 done; 2 refused
set -euo pipefail

has_label() { # <name>
  gh label list --limit 500 --json name --jq '.[].name' | grep -qxF -- "$1"
}

label() { # <name> <color> <description>
  gh label create "$1" --force --color "$2" --description "$3"
}

cmd_labels() {
  while read -r name color description; do
    if has_label "agy:$name" && ! has_label "$name"; then
      gh label edit "agy:$name" --name "$name" --color "$color" --description "$description"
      echo "renamed agy:$name to $name"
    else
      label "$name" "$color" "$description"
    fi
    if has_label "agy:$name"; then
      for n in $(gh issue list --state open --label "agy:$name" --limit 200 --json number --jq '.[].number'); do
        gh issue edit "$n" --remove-label "agy:$name" --add-label "$name" >/dev/null
        echo "moved issue #$n from agy:$name to $name"
      done
      echo "note: agy:$name still exists; delete it by hand when you no longer need it"
    fi
  done <<'EOF'
todo 0E8A16 Ready for the work step of the issue loop
doing FBCA04 The work step of the issue loop is on it
done 1D76DB A PR waits for the review step of the issue loop
EOF
  label blocked B60205 "A human must decide; answer, then move it back to todo"
  while read -r name color description; do
    has_label "$name" || label "$name" "$color" "$description"
  done <<'EOF'
priority:p0 B60205 Do now
priority:p1 D93F0B Do next
priority:p2 FBCA04 Normal
priority:p3 C5DEF5 Some day
EOF
  echo "labels ready"
}

cmd_status() {
  count() { gh issue list --state open --label "$1" --limit 500 --json number --jq length; }
  echo "todo=$(count todo) doing=$(count doing) done=$(count "done") blocked=$(count blocked)"
  gh issue list --state open --label blocked --json number,title \
    --jq '.[] | "blocked #\(.number) \(.title)"'
}

case "${1:-}" in
  labels) cmd_labels ;;
  status) cmd_status ;;
  *) echo "usage: loop.sh labels | status" >&2; exit 2 ;;
esac
