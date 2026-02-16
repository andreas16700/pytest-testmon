#!/bin/bash
# Quick pandas profiling - compares ezmon vs nocov

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PIP="/Users/andrew_yos/mamba/envs/pandas-dev/bin/pip"
PYTEST="/Users/andrew_yos/mamba/envs/pandas-dev/bin/pytest"
PANDAS_REPO="/Users/andrew_yos/pandas/pandas-repo"
EZMON_REPO="$(dirname "$(dirname "$SCRIPT_DIR")")"

# Default subset
SUBSET="${1:-pandas/tests/computation}"

echo "============================================================"
echo "Pandas Quick Profile: ezmon vs nocov"
echo "Subset: $SUBSET"
echo "============================================================"

# Helper function to uninstall testmon variants
uninstall_all() {
    $PIP uninstall -y pytest-testmon 2>/dev/null || true
    $PIP uninstall -y pytest-ezmon 2>/dev/null || true
    $PIP uninstall -y ezmon 2>/dev/null || true
}

cd "$PANDAS_REPO"

# Clean all testmondata files
rm -f .testmondata* 2>/dev/null || true
rm -rf .pytest_cache 2>/dev/null || true

echo ""
echo "============================================================"
echo "Testing: bare (no plugin) - baseline"
echo "============================================================"
uninstall_all
echo "First run..."
time $PYTEST -m "not slow and not db and not network and not single_cpu" --tb=no -q "$SUBSET" 2>&1 | tail -20

echo ""
echo "============================================================"
echo "Testing: nocov (this experiment - no coverage)"
echo "============================================================"
uninstall_all
rm -f .testmondata* 2>/dev/null || true
rm -rf .pytest_cache 2>/dev/null || true
echo "Installing nocov from local repo..."
$PIP install -e "$EZMON_REPO" --quiet
echo ""
echo "First run (cold)..."
time $PYTEST -m "not slow and not db and not network and not single_cpu" --tb=no -q --ezmon --ezmon-forceselect "$SUBSET" 2>&1 | tail -20
echo ""
echo "Testmondata size:"
ls -lh .testmondata 2>/dev/null || echo "No testmondata"
echo ""
echo "Warm run (no changes)..."
time $PYTEST -m "not slow and not db and not network and not single_cpu" --tb=no -q --ezmon --ezmon-forceselect "$SUBSET" 2>&1 | tail -20

echo ""
echo "============================================================"
echo "Testing: ezmon (with coverage)"
echo "============================================================"
uninstall_all
rm -f .testmondata* 2>/dev/null || true
rm -rf .pytest_cache 2>/dev/null || true
echo "Installing ezmon from GitHub..."
$PIP install "git+https://github.com/andreas16700/pytest-testmon.git@main" --quiet
echo ""
echo "First run (cold)..."
time $PYTEST -m "not slow and not db and not network and not single_cpu" --tb=no -q --ezmon --ezmon-forceselect "$SUBSET" 2>&1 | tail -20
echo ""
echo "Testmondata size:"
ls -lh .testmondata 2>/dev/null || echo "No testmondata"
echo ""
echo "Warm run (no changes)..."
time $PYTEST -m "not slow and not db and not network and not single_cpu" --tb=no -q --ezmon --ezmon-forceselect "$SUBSET" 2>&1 | tail -20

echo ""
echo "============================================================"
echo "COMPLETE - Summary"
echo "============================================================"

# Cleanup
uninstall_all
rm -f .testmondata* 2>/dev/null || true
