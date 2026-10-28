#!/usr/bin/env bash
# AGY owns only todo -> doing. Python holds the atomic repository guard.
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
exec python3 "$SCRIPT_DIR/../../issue-loop/scripts/coordination_entrypoint.py" work "$@"
