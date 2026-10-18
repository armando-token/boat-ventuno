#!/usr/bin/env bash
# git archive HEAD -> clean dir -> pytest (no board, no local release artefacts). Exit 0 only if green.
# Env: SDV_TMP (scratch root, default: mktemp), SDV_PYTHON (interpreter for the venv),
#      SDV_VENV_PYTHON (reuse an existing interpreter instead of creating a venv),
#      SDV_LOCK (lock file; default picked by platform).
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
if [[ -n "${SDV_TMP:-}" ]]; then
  mkdir -p "$SDV_TMP"
  WORK="$(mktemp -d "$SDV_TMP/sdv_clean.XXXXXX")"
else
  WORK="$(mktemp -d)"
fi
trap 'rm -rf "$WORK"' EXIT

git -C "$REPO" archive HEAD | tar -x -C "$WORK"
cd "$WORK"

if [[ -n "${SDV_VENV_PYTHON:-}" ]]; then
  PY="$SDV_VENV_PYTHON"
else
  "${SDV_PYTHON:-python3}" -m venv .venv
  PY="$WORK/.venv/bin/python"
  LOCK="${SDV_LOCK:-}"
  if [[ -z "$LOCK" ]]; then
    case "$(uname -s)-$(uname -m)" in
      Darwin-arm64) LOCK=requirements-macos-arm64.lock ;;
      Linux-aarch64) LOCK=requirements-linux-aarch64.lock ;;
    esac
  fi
  if [[ -n "$LOCK" && -f "$LOCK" ]]; then
    "$PY" -m pip install -q -r "$LOCK"
  else
    "$PY" -m pip install -q -e .
  fi
fi

PYTHONPATH=src "$PY" -m pytest -q -o addopts="" -p no:cacheprovider -m "not board and not release_local"
echo "CLEAN_CLONE_OK"
