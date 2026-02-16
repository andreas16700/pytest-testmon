#!/bin/bash
# Profile initial run overhead for ezmon-nocov
#
# This script compares:
# 1. Vanilla pytest (no ezmon) - baseline
# 2. Ezmon initial run (empty database) - full overhead
#
# It also captures component-level timing for:
# - Import tracking setup
# - Checksum computation
# - Database writes
#
# Prerequisites:
# - Run profile_setup.sh first
# - micromamba/conda with pandas-dev environment

set -e

PROFILE_DIR="$(pwd)/profile"
PANDAS_DIR="$PROFILE_DIR/pandas"
PLUGIN_DIR="/Users/andrew_yos/pytest-super/nocov-refactor"
PYTHON="/Users/andrew_yos/mamba/envs/pandas-dev/bin/python"

# Parse arguments
TEST_SUBSET=""
NUM_WORKERS="auto"
SKIP_VANILLA=0

for arg in "$@"; do
    case "$arg" in
        --subset=*)
            TEST_SUBSET="${arg#*=}"
            ;;
        --workers=*)
            NUM_WORKERS="${arg#*=}"
            ;;
        --skip-vanilla)
            SKIP_VANILLA=1
            ;;
    esac
done

if [ ! -d "$PANDAS_DIR" ]; then
    echo "Error: Run profile_setup.sh first"
    exit 1
fi

cd "$PANDAS_DIR"

# Set up environment
export PATH="/Users/andrew_yos/mamba/envs/pandas-dev/bin:$PATH"
export PYTHONNOUSERSITE=1
export PIP_USER=0
export PYTEST_DISABLE_PLUGIN_AUTOLOAD=1

# Default test subset for faster profiling
if [ -z "$TEST_SUBSET" ]; then
    TEST_SUBSET="pandas/tests/frame/methods/test_sort_values.py"
fi

echo "=== Initial Run Profiling ==="
echo "Test subset: $TEST_SUBSET"
echo "Workers: $NUM_WORKERS"
echo ""

# Create output directory for this run
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
OUTPUT_DIR="$PROFILE_DIR/initial_run_$TIMESTAMP"
mkdir -p "$OUTPUT_DIR"

# Clean up any existing database
rm -f "$PANDAS_DIR/.testmondata"

# ============================================================
# 1. Vanilla pytest baseline (no ezmon)
# ============================================================
if [ "$SKIP_VANILLA" -eq 0 ]; then
    echo "=== 1. Vanilla pytest (baseline) ==="

    VANILLA_PROFILE="$OUTPUT_DIR/vanilla.prof"
    VANILLA_OUTPUT="$OUTPUT_DIR/vanilla_output.txt"
    VANILLA_TIME="$OUTPUT_DIR/vanilla_time.txt"

    /usr/bin/time -l $PYTHON -m cProfile -o "$VANILLA_PROFILE" \
        -m pytest --collect-only -q \
        -m "not slow and not db and not network and not single_cpu" \
        "$TEST_SUBSET" 2>&1 | tee "$VANILLA_OUTPUT"

    echo ""
    echo "Vanilla collection complete."
    echo ""
fi

# ============================================================
# 2. Ezmon initial run (empty database)
# ============================================================
echo "=== 2. Ezmon initial run (full collection + dependency tracking) ==="

# Ensure clean state
rm -f "$PANDAS_DIR/.testmondata"

export PYTHONPATH="$PLUGIN_DIR:${PYTHONPATH:-}"

EZMON_PROFILE="$OUTPUT_DIR/ezmon_initial.prof"
EZMON_OUTPUT="$OUTPUT_DIR/ezmon_initial_output.txt"

# Run with cProfile
$PYTHON -m cProfile -o "$EZMON_PROFILE" \
    -m pytest --collect-only -q \
    -m "not slow and not db and not network and not single_cpu" \
    -p ezmon.pytest_ezmon --ezmon \
    "$TEST_SUBSET" 2>&1 | tee "$EZMON_OUTPUT"

echo ""
echo "Ezmon initial run complete."
echo ""

# ============================================================
# 3. Component-level breakdown
# ============================================================
echo "=== 3. Component Breakdown ==="

$PYTHON - <<'ANALYSIS'
import pstats
import os
import sys

output_dir = os.environ.get("OUTPUT_DIR", ".")

def analyze_profile(name, prof_path):
    if not os.path.exists(prof_path):
        print(f"  {name}: profile not found")
        return

    stats = pstats.Stats(prof_path)
    stats.strip_dirs()

    # Get total time
    total_time = 0
    for func, (cc, nc, tt, ct, callers) in stats.stats.items():
        if func[2] == '<module>' and 'pytest' in str(func):
            total_time = max(total_time, ct)

    print(f"\n{'='*60}")
    print(f"{name}")
    print(f"{'='*60}")

    # Top functions by cumulative time
    print(f"\nTop 20 functions by cumulative time:")
    stats.sort_stats('cumtime')
    stats.print_stats(20)

# Analyze profiles
vanilla_prof = os.path.join(output_dir, "vanilla.prof")
ezmon_prof = os.path.join(output_dir, "ezmon_initial.prof")

if os.path.exists(vanilla_prof):
    analyze_profile("Vanilla pytest", vanilla_prof)

if os.path.exists(ezmon_prof):
    analyze_profile("Ezmon initial run", ezmon_prof)

    # Component breakdown for ezmon
    print(f"\n{'='*60}")
    print("Ezmon Component Breakdown")
    print(f"{'='*60}")

    stats = pstats.Stats(ezmon_prof)
    stats.strip_dirs()

    components = {
        "Import tracking": ["_tracking_import", "start_tracking", "stop_tracking"],
        "Checksum computation": ["compute_file_checksum", "get_file_checksum", "batch_get_checksums"],
        "AST parsing": ["parse", "ast.parse", "strip_docstrings"],
        "File I/O": ["_read_file", "open", "read"],
        "Database": ["execute", "commit", "sqlite"],
        "Git operations": ["_run_git", "subprocess"],
        "FileInfoCache": ["refresh", "get_source_and_fsha", "batch_get_fshas", "_normalize_path", "is_tracked"],
    }

    for component, patterns in components.items():
        total_ct = 0
        for func, (cc, nc, tt, ct, callers) in stats.stats.items():
            func_name = func[2]
            for pattern in patterns:
                if pattern.lower() in func_name.lower():
                    total_ct += ct
                    break
        if total_ct > 0.01:  # Only show if > 10ms
            print(f"  {component}: {total_ct:.3f}s")

ANALYSIS

echo ""
echo "=== Profile files saved to: $OUTPUT_DIR ==="
echo ""
echo "To analyze interactively:"
echo "  $PYTHON -c \"import pstats; p = pstats.Stats('$OUTPUT_DIR/ezmon_initial.prof'); p.strip_dirs(); p.sort_stats('cumtime'); p.print_stats(50)\""
