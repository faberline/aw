#!/usr/bin/env bash
# AGY owns only todo -> doing. Python holds the atomic repository guard.
set -euo pipefail
exec python3 "$(dirname "$0")/work.py" "$@"
