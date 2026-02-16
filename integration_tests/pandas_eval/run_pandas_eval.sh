#!/bin/bash
#
# Pandas Plugin Comparison Script
#
# This script compares: bare pytest, testmon (upstream), ezmon (coverage), nocov (this experiment)
#
# Prerequisites:
#   - pandas-dev conda environment with pandas installed
#   - Run from within the pandas repo directory
#
# Usage:
#   # Activate environment first
#   micromamba activate pandas-dev
#
#   # Run from pandas repo
#   cd ~/pandas/pandas-repo
#   /path/to/run_pandas_eval.sh [test_subset]
#
# Example:
#   ./run_pandas_eval.sh pandas/tests/computation
#   ./run_pandas_eval.sh  # Full test suite (WARNING: takes 20+ minutes)

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
EZMON_REPO="$(dirname "$(dirname "$SCRIPT_DIR")")"
SUBSET="${1:-pandas/tests/computation}"
MARKERS="not slow and not db and not network and not single_cpu"

# Verify we're in the right place
if [ ! -f "pyproject.toml" ] || ! grep -q "pandas" pyproject.toml 2>/dev/null; then
    echo "ERROR: Run this script from within the pandas repo directory"
    echo "Usage: cd ~/pandas/pandas-repo && $0 [test_subset]"
    exit 1
fi

# Verify pandas is importable
if ! python -c "import pandas" 2>/dev/null; then
    echo "ERROR: pandas not installed. Activate pandas-dev environment first."
    echo "Usage: micromamba activate pandas-dev"
    exit 1
fi

RESULTS_FILE="$SCRIPT_DIR/pandas_results_$(date +%Y%m%d_%H%M%S).txt"

echo "============================================================"
echo "Pandas Plugin Comparison"
echo "Subset: $SUBSET"
echo "Results: $RESULTS_FILE"
echo "============================================================"
echo "" | tee "$RESULTS_FILE"

# Helper function
uninstall_all() {
    pip uninstall -y pytest-testmon 2>/dev/null || true
    pip uninstall -y pytest-ezmon 2>/dev/null || true
    pip uninstall -y ezmon 2>/dev/null || true
}

run_test() {
    local name="$1"
    local plugin_args="$2"
    local first_run="${3:-true}"

    if [ "$first_run" = "true" ]; then
        rm -f .testmondata 2>/dev/null || true
    fi
    rm -rf .pytest_cache 2>/dev/null || true

    echo "Running: $name" | tee -a "$RESULTS_FILE"
    echo "Plugin args: $plugin_args" | tee -a "$RESULTS_FILE"

    local start=$(date +%s.%N)
    pytest -m "$MARKERS" --tb=no -q $plugin_args "$SUBSET" 2>&1 | tail -5 | tee -a "$RESULTS_FILE"
    local end=$(date +%s.%N)
    local elapsed=$(echo "$end - $start" | bc)

    echo "Time: ${elapsed}s" | tee -a "$RESULTS_FILE"

    if [ -f .testmondata ]; then
        local size=$(du -h .testmondata | cut -f1)
        echo "Testmondata: $size" | tee -a "$RESULTS_FILE"
    fi
    echo "" | tee -a "$RESULTS_FILE"
}

# 1. Bare pytest
echo "============================================================" | tee -a "$RESULTS_FILE"
echo "1. BARE PYTEST (no plugin)" | tee -a "$RESULTS_FILE"
echo "============================================================" | tee -a "$RESULTS_FILE"
uninstall_all
run_test "bare" ""

# 2. Upstream testmon
echo "============================================================" | tee -a "$RESULTS_FILE"
echo "2. TESTMON (upstream)" | tee -a "$RESULTS_FILE"
echo "============================================================" | tee -a "$RESULTS_FILE"
uninstall_all
pip install pytest-testmon --quiet
echo "First run:" | tee -a "$RESULTS_FILE"
run_test "testmon-first" "--testmon --testmon-forceselect" true
echo "Warm run:" | tee -a "$RESULTS_FILE"
run_test "testmon-warm" "--testmon" false

# 3. nocov (this experiment)
echo "============================================================" | tee -a "$RESULTS_FILE"
echo "3. NOCOV (no coverage - this experiment)" | tee -a "$RESULTS_FILE"
echo "============================================================" | tee -a "$RESULTS_FILE"
uninstall_all
pip install -e "$EZMON_REPO" --quiet
echo "First run:" | tee -a "$RESULTS_FILE"
run_test "nocov-first" "--ezmon --ezmon-forceselect" true
echo "Warm run:" | tee -a "$RESULTS_FILE"
run_test "nocov-warm" "--ezmon" false

# 4. ezmon with coverage (optional - very slow on large test suites)
if [ "${RUN_EZMON_COVERAGE:-false}" = "true" ]; then
    echo "============================================================" | tee -a "$RESULTS_FILE"
    echo "4. EZMON (with coverage)" | tee -a "$RESULTS_FILE"
    echo "============================================================" | tee -a "$RESULTS_FILE"
    uninstall_all
    pip install "git+https://github.com/andreas16700/pytest-testmon.git@main" --quiet
    echo "First run:" | tee -a "$RESULTS_FILE"
    run_test "ezmon-first" "--ezmon --ezmon-forceselect" true
    echo "Warm run:" | tee -a "$RESULTS_FILE"
    run_test "ezmon-warm" "--ezmon" false
fi

# Cleanup
uninstall_all
rm -f .testmondata 2>/dev/null || true

echo "============================================================" | tee -a "$RESULTS_FILE"
echo "COMPLETE" | tee -a "$RESULTS_FILE"
echo "Results saved to: $RESULTS_FILE" | tee -a "$RESULTS_FILE"
echo "============================================================" | tee -a "$RESULTS_FILE"

echo ""
echo "To also run ezmon with coverage (slow!), set RUN_EZMON_COVERAGE=true:"
echo "  RUN_EZMON_COVERAGE=true $0 $SUBSET"
