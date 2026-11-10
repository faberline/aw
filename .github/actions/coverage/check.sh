#!/usr/bin/env bash
# Coverage ratchet: standard S8, docs/standards/coverage.md in aw.
#
#   check.sh <summary.json> <aw.toml> [<base aw.toml>]
#
# <summary.json> is the output of `cargo llvm-cov --json --summary-only`.
# <aw.toml> holds [standards.coverage] of this commit. <base aw.toml> is the
# same file on the pull request base; leave it out on a push. A path that
# does not exist counts as a file with no [standards.coverage].
# COVERAGE_ROOT (default: the current directory) is cut from file names.
#
# Prints a JSON report on stdout. Appends a Markdown report to
# GITHUB_STEP_SUMMARY when it is set. Exits 1 when a check fails.
# Needs bash, jq and python3 3.11 or later (for tomllib).
set -euo pipefail

if [ $# -lt 2 ] || [ $# -gt 3 ]; then
  echo "usage: check.sh <summary.json> <aw.toml> [<base aw.toml>]" >&2
  exit 2
fi
summary=$1
config=$2
base=${3:-}
root=${COVERAGE_ROOT:-$PWD}

# Points that one run may lose to test noise.
tolerance=0.1
# Points that the baseline may stay below the measured value.
slack=0.5

# Prints [standards.coverage] of a TOML file as JSON, or null.
section() {
  if [ -z "$1" ] || [ ! -f "$1" ]; then
    echo null
    return
  fi
  python3 -c '
import json, sys, tomllib
with open(sys.argv[1], "rb") as f:
    data = tomllib.load(f)
print(json.dumps(data.get("standards", {}).get("coverage")))
' "$1"
}

head_cfg=$(section "$config")
base_cfg=$(section "$base")

report=$(jq -n \
  --slurpfile summary "$summary" \
  --argjson cfg "$head_cfg" \
  --argjson base "$base_cfg" \
  --arg root "${root%/}/" \
  --argjson tolerance "$tolerance" \
  --argjson slack "$slack" '
def floor2: . * 100 | floor / 100;
def status(ok): if ok then "pass" else "fail" end;
($cfg // {}) as $c
| ($c.line | if type == "number" then . else null end) as $line
| ($base // {} | .line | if type == "number" then . else null end) as $base_line
| [($c.exclude // [])[] | .path // empty] as $paths
| [($base // {} | .exclude // [])[] | .path // empty] as $base_paths
| [$summary[0].data[0].files[] | .filename |= ltrimstr($root)] as $all
| [$all[] | select(.filename as $f | all($paths[]; . as $re | $f | test($re) | not))]
    as $files
| ($files | map(.summary.lines.count) | add // 0) as $count
| ($files | map(.summary.lines.covered) | add // 0) as $covered
| (if $count == 0 then 100 else $covered / $count * 100 | floor2 end) as $measured
| {
    measured: $measured,
    covered: $covered,
    lines: $count,
    files: ($files | length),
    excluded_files: (($all | length) - ($files | length)),
    line: $line,
    base_line: $base_line,
    target: $c.target,
    new_excludes: ($paths - $base_paths),
    findings: [
      {check: "COV1",
       status: status($cfg != null and $line != null and $c.target == 100),
       detail: (if $cfg == null
         then "add [standards.coverage] with line = \($measured) and target = 100"
         else "line = \($c.line), target = \($c.target)" end)},
      {check: "COV2",
       status: status(all(($c.exclude // [])[];
         (.path // "") != "" and (.reason // "") != "")),
       detail: "every [[standards.coverage.exclude]] needs path and reason"},
      (if $line == null then {check: "COV3", status: "skipped", detail: "no line"}
       else {check: "COV3",
         status: status($measured >= $line - $tolerance),
         detail: "measured \($measured), line \($line), tolerance \($tolerance)"} end),
      (if $line == null then {check: "COV4", status: "skipped", detail: "no line"}
       else {check: "COV4",
         status: status($measured <= $line + $slack),
         detail: (if $measured <= $line + $slack
           then "line is within \($slack) of measured"
           else "raise line to \($measured)" end)} end),
      (if $line == null or $base_line == null
       then {check: "COV5", status: "skipped", detail: "no base line"}
       else {check: "COV5",
         status: status($line >= $base_line),
         detail: "line \($line), base line \($base_line)"} end)
    ],
    most_uncovered: [$files[]
      | {file: .filename,
         percent: (.summary.lines.percent | floor2),
         uncovered: (.summary.lines.count - .summary.lines.covered)}
      | select(.uncovered > 0)]
      | sort_by(-.uncovered, .file) | .[:10]
  }')

echo "$report"

if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
  jq -r '
    "## Coverage (S8)",
    "",
    "Line coverage **\(.measured)%**: \(.covered) of \(.lines) lines in \(.files) files (\(.excluded_files) files excluded). Baseline `line` = \(.line), base `line` = \(.base_line), target \(.target).",
    "",
    "| Check | Status | Detail |",
    "|---|---|---|",
    (.findings[] | "| \(.check) | \(.status) | \(.detail) |"),
    "",
    (if (.new_excludes | length) > 0
     then "New excludes in this change (review checks the reason): \(.new_excludes | map("`\(.)`") | join(", "))", ""
     else empty end),
    "### Most uncovered files",
    "",
    "| File | Line % | Uncovered lines |",
    "|---|---|---|",
    (.most_uncovered[] | "| `\(.file)` | \(.percent) | \(.uncovered) |")
  ' <<<"$report" >>"$GITHUB_STEP_SUMMARY"
fi

if jq -e 'any(.findings[]; .status == "fail")' <<<"$report" >/dev/null; then
  exit 1
fi
