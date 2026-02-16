#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

export TESTMON_DATA_DIR="${TESTMON_DATA_DIR:-/tmp/ezmon_netdb_data}"
export EZMON_CI_TOKEN="${EZMON_CI_TOKEN:-ezmon-ci-test-token-2024}"
export FLASK_DEBUG="${FLASK_DEBUG:-0}"

VENV_DIR="${EZMON_NETDB_VENV_DIR:-/tmp/ezmon_netdb_server_venv}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

if [ ! -x "$VENV_DIR/bin/python" ]; then
  "$PYTHON_BIN" -m venv "$VENV_DIR"
  "$VENV_DIR/bin/python" -m pip install -U pip
  "$VENV_DIR/bin/python" -m pip install -r "$ROOT_DIR/ez-viz/requirements.txt"
fi

exec "$VENV_DIR/bin/python" "$ROOT_DIR/ez-viz/app.py"
