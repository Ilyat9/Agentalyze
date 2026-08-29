#!/bin/sh
# Entrypoint of the Agentalyze image.
#
# Why a wrapper instead of a bare ENTRYPOINT:
#   1. Diagnostics: prints the resolved command to stderr, so a PaaS runtime
#      (e.g. Render) that fails to start the container shows WHAT it tried to
#      run instead of a bare "Exited with status 128" and zero output.
#   2. PaaS serve mode: setting AGENTALYZE_SERVE_ON_START=1 makes the image
#      start the demo web service on boot without any dockerCommand override
#      (some platforms mangle command overrides). The default CLI behavior
#      (`docker run image compare ...` / `--help`) is unchanged.
set -u

echo "[agentalyze-entrypoint] argv: $*" >&2

if [ "${AGENTALYZE_SERVE_ON_START:-0}" = "1" ]; then
    exec agentalyze serve --host 0.0.0.0 --port "${PORT:-10000}" --demo-mode
fi

exec agentalyze "$@"