#!/usr/bin/env bash
# The measured coverage of the base of a pull request (standard S8, COV6).
#
#   base-measured.sh <owner/name> <base sha>
#
# Every coverage run writes the annotation `measured=<value>` with the title
# `aw-coverage` on its check run (see action.yml). This script reads that
# annotation from the base commit, or from the newest of its 4 parents that
# has one, and prints the value. It prints nothing when it finds none; COV6
# is then skipped. It needs only read access to the public check runs: no
# `actions: read` permission and no artifact download. Needs bash, jq and gh.
set -euo pipefail

repo=$1
base=$2

for sha in $(gh api "repos/$repo/commits?sha=$base&per_page=5" --jq '.[].sha'); do
  for id in $(gh api "repos/$repo/commits/$sha/check-runs?per_page=100" \
      --jq '.check_runs[] | select(.name | test("coverage")) | .id'); do
    value=$(gh api "repos/$repo/check-runs/$id/annotations?per_page=100" --jq '
      [.[] | select(.title == "aw-coverage") | .message
        | capture("measured=(?<m>[0-9.]+)").m][0] // empty')
    if [ -n "$value" ]; then
      echo "$value"
      exit 0
    fi
  done
done
