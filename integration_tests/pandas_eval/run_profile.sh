#!/bin/bash
# Run pandas profiling with the pandas-dev conda environment

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="/Users/andrew_yos/mamba/envs/pandas-dev/bin/python"
PIP="/Users/andrew_yos/mamba/envs/pandas-dev/bin/pip"
PYTEST="/Users/andrew_yos/mamba/envs/pandas-dev/bin/pytest"
PANDAS_REPO="/Users/andrew_yos/pandas/pandas-repo"
EZMON_REPO="$(dirname "$(dirname "$SCRIPT_DIR")")"

# Default subset
SUBSET="${1:-pandas/tests/computation}"

echo "============================================================"
echo "Pandas Plugin Comparison"
echo "Subset: $SUBSET"
echo "============================================================"

# Helper function to uninstall testmon variants
uninstall_all() {
    $PIP uninstall -y pytest-testmon 2>/dev/null || true
    $PIP uninstall -y pytest-ezmon 2>/dev/null || true
    $PIP uninstall -y ezmon 2>/dev/null || true
}

# Helper function to run pytest with timing
run_pytest() {
    local plugin="$1"
    local run_type="$2"  # "first" or "warm"
    local plugin_args=""

    case "$plugin" in
        testmon)
            plugin_args="--testmon --testmon-forceselect"
            ;;
        ezmon|nocov)
            plugin_args="--ezmon --ezmon-forceselect"
            ;;
    esac

    # Clean testmondata for first run
    if [ "$run_type" = "first" ]; then
        rm -f "$PANDAS_REPO/.testmondata" 2>/dev/null || true
    fi

    # Clean pytest cache
    rm -rf "$PANDAS_REPO/.pytest_cache" 2>/dev/null || true

    # Run pytest with timing
    cd "$PANDAS_REPO"
    /usr/bin/time -l $PYTEST \
        -m "not slow and not db and not network and not single_cpu" \
        --tb=no -q \
        $plugin_args \
        "$SUBSET" 2>&1
}

echo ""
echo "============================================================"
echo "Testing: bare (no plugin)"
echo "============================================================"
uninstall_all
echo "First run (cold)..."
run_pytest "bare" "first"

echo ""
echo "============================================================"
echo "Testing: testmon (upstream)"
echo "============================================================"
uninstall_all
echo "Installing pytest-testmon..."
$PIP install pytest-testmon --quiet
echo "First run (cold)..."
run_pytest "testmon" "first"
echo ""
echo "Warm run (no changes)..."
run_pytest "testmon" "warm"

echo ""
echo "============================================================"
echo "Testing: ezmon (with coverage)"
echo "============================================================"
uninstall_all
echo "Installing ezmon from GitHub..."
$PIP install "git+https://github.com/andreas16700/pytest-testmon.git@main" --quiet
echo "First run (cold)..."
run_pytest "ezmon" "first"
echo ""
echo "Warm run (no changes)..."
run_pytest "ezmon" "warm"

echo ""
echo "============================================================"
echo "Testing: nocov (this experiment)"
echo "============================================================"
uninstall_all
echo "Installing nocov from local repo..."
$PIP install -e "$EZMON_REPO" --quiet
echo "First run (cold)..."
run_pytest "nocov" "first"
echo ""
echo "Warm run (no changes)..."
run_pytest "nocov" "warm"

echo ""
echo "============================================================"
echo "COMPLETE"
echo "============================================================"

# Cleanup
uninstall_all
