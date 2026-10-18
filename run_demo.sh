#!/usr/bin/env bash
# One command to run the demo: ./run_demo.sh  (creates .venv if needed, installs, serves UI on :8765)
set -euo pipefail
cd "$(dirname "$0")"

PORT="${PORT:-8765}"
HOST="${HOST:-127.0.0.1}"

case "$(uname -s)-$(uname -m)" in
  Darwin-arm64) LOCK="requirements-macos-arm64.lock" ;;
  Linux-aarch64) LOCK="requirements-linux-aarch64.lock" ;;
  *) LOCK="" ;;
esac

if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv --system-site-packages
  if [ -n "$LOCK" ]; then
    .venv/bin/pip install -q -r "$LOCK"
  fi
  .venv/bin/pip install -q --no-build-isolation --no-deps -e .
fi

export PYTHONPATH="${PWD}/src${PYTHONPATH:+:${PYTHONPATH}}"
.venv/bin/python -m sdv_governor.cli doctor --no-remote >/dev/null
echo "SDV governor UI: http://${HOST}:${PORT}  (Ctrl+C to stop)"
exec .venv/bin/python -m sdv_governor.cli serve --host "$HOST" --port "$PORT"
