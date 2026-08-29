#!/usr/bin/env bash
# Regenerate the animated CLI demos (docs/assets/cli-*.gif) from the
# Charm VHS tapes in docs/tapes/.
#
# Requirements: `brew install vhs` (pulls ffmpeg and ttyd).
# The tapes show only REAL output: `tasks` runs offline for real; the
# `run` demo replays the final summary of a genuine recorded trace
# (see the header comment in docs/tapes/run.tape).
#
# Usage: scripts/gen_cli_demos.sh
set -euo pipefail

cd "$(dirname "$0")/.."

command -v vhs >/dev/null 2>&1 || {
  echo "error: vhs not found; install it with 'brew install vhs'" >&2
  exit 1
}

for tape in docs/tapes/*.tape; do
  echo ">> vhs $tape"
  vhs "$tape"
done

echo "done:"
ls -lh docs/assets/cli-*.gif
