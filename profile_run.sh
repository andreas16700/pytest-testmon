#!/bin/bash
# Run script for profiling nocov-refactor "0 tests selected" case
#
# Prerequisites:
# - Run profile_setup.sh first
# - micromamba/conda with pandas-dev environment

set -e

PROFILE_DIR="$(pwd)/profile"
PANDAS_DIR="$PROFILE_DIR/pandas"
PLUGIN_DIR="/Users/andrew_yos/pytest-super/nocov-refactor"
PYTHON="/Users/andrew_yos/mamba/envs/pandas-dev/bin/python"
COMMENT_TARGET="pandas/core/frame.py"
COMMENT_MARK="# ezmon-nocov comment-only change"
COMMENT_MARK_MASS="# ezmon-nocov mass comment-only change"
SKIP_BASELINE=0

for arg in "$@"; do
    if [ "$arg" = "--skip-baseline" ]; then
        SKIP_BASELINE=1
    fi
done

if [ ! -d "$PANDAS_DIR" ]; then
    echo "Error: Run profile_setup.sh first"
    exit 1
fi

cd "$PANDAS_DIR"

export PYTHONPATH="$PANDAS_DIR:$PLUGIN_DIR"

echo "=== Setting up environment ==="

# Ensure correct meson is used
export PATH="/Users/andrew_yos/mamba/envs/pandas-dev/bin:$PATH"
export PYTHONNOUSERSITE=1
export PIP_USER=0
export CCACHE_DISABLE=1

export PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
export PYTHONPATH="$PLUGIN_DIR:${PYTHONPATH:-}"

# Build pandas
echo "Building pandas..."
rm -rf build/
$PYTHON -m pip install -e . --no-build-isolation -q

echo "Ensuring pytest-xdist is installed..."
if ! $PYTHON - <<'PY'
try:
    import xdist  # noqa: F401
except Exception:
    raise SystemExit(1)
PY
then
    $PYTHON -m pip install pytest-xdist -q
fi

if [ "$SKIP_BASELINE" -eq 0 ]; then
    echo "Resetting .testmondata for a clean initial run..."
    rm -f "$PANDAS_DIR/.testmondata"
fi

if [ "$SKIP_BASELINE" -eq 0 ]; then
    echo ""
    echo "=== Running pytest with cProfile (baseline) ==="
    echo "This should select 0 tests and take ~65s"
    echo ""

    # Run with cProfile
    BASELINE_PROFILE="$PROFILE_DIR/nocov_profile_baseline.prof"
    BASELINE_OUTPUT="$PROFILE_DIR/output_baseline.txt"
    $PYTHON -m cProfile -s cumtime -o "$BASELINE_PROFILE" \
        -m pytest -r fE -n auto --dist=worksteal \
        -m "not slow and not db and not network and not single_cpu" \
        -p xdist -p ezmon.pytest_ezmon --ezmon --ezmon-forceselect --ezmon-no-reorder \
        pandas 2>&1 | tee "$BASELINE_OUTPUT"
fi

echo ""
echo "=== Applying comment-only changes to dependency Python files ==="
echo "$COMMENT_MARK" >> "$PANDAS_DIR/$COMMENT_TARGET"

DEPENDENCY_PY_FILES="$PROFILE_DIR/dependency_python_files.txt"

$PYTHON - <<'PY'
import os
import sqlite3
from ezmon.bitmap_deps import TestDeps

profile_dir = os.path.join(os.getcwd(), "..")
db_path = os.path.join(os.getcwd(), ".testmondata")

conn = sqlite3.connect(db_path)
cur = conn.cursor()

cur.execute("SELECT test_id, file_bitmap, external_packages FROM test_deps")
file_ids = set()
for test_id, blob, external_pkgs in cur.fetchall():
    deps = TestDeps.deserialize(test_id, blob, external_pkgs)
    file_ids.update(int(fid) for fid in deps.file_ids)

if not file_ids:
    raise SystemExit("No dependency files found in test_deps")

placeholders = ",".join("?" for _ in file_ids)
cur.execute(
    f"SELECT path FROM files WHERE id IN ({placeholders}) AND file_type='python'",
    tuple(file_ids),
)
paths = sorted({row[0] for row in cur.fetchall()})
conn.close()

out_path = os.path.join(profile_dir, "dependency_python_files.txt")
with open(out_path, "w", encoding="utf-8") as handle:
    for path in paths:
        handle.write(path + "\n")

print(f"Wrote {len(paths)} dependency python files to {out_path}")
PY

if [ -f "$DEPENDENCY_PY_FILES" ]; then
    while IFS= read -r relpath; do
        [ -z "$relpath" ] && continue
        target="$PANDAS_DIR/$relpath"
        if [ -f "$target" ]; then
            echo "$COMMENT_MARK_MASS" >> "$target"
        fi
    done < "$DEPENDENCY_PY_FILES"
fi

cleanup_comment_change() {
    echo "Reverting comment-only changes..."
    git checkout -- . >/dev/null 2>&1 || true
}

trap cleanup_comment_change EXIT

echo ""
echo "=== Running pytest with cProfile (comment-only change) ==="
echo "This should still select 0 tests and take ~65s"
echo ""

COMMENT_PROFILE="$PROFILE_DIR/nocov_profile_comment.prof"
COMMENT_OUTPUT="$PROFILE_DIR/output_comment.txt"
$PYTHON -m cProfile -s cumtime -o "$COMMENT_PROFILE" \
    -m pytest -r fE -n auto --dist=worksteal \
    -m "not slow and not db and not network and not single_cpu" \
    -p xdist -p ezmon.pytest_ezmon --ezmon --ezmon-forceselect --ezmon-no-reorder \
    pandas 2>&1 | tee "$COMMENT_OUTPUT"

echo ""
echo "=== Profile complete ==="
echo "Profiles saved to:"
echo "  $BASELINE_PROFILE"
echo "  $COMMENT_PROFILE"
echo "Outputs saved to:"
echo "  $BASELINE_OUTPUT"
echo "  $COMMENT_OUTPUT"
echo ""
echo "To analyze:"
echo "  $PYTHON -c \"import pstats; p = pstats.Stats('$BASELINE_PROFILE'); p.strip_dirs(); p.sort_stats('cumtime'); p.print_stats(30)\""
echo "  $PYTHON -c \"import pstats; p = pstats.Stats('$COMMENT_PROFILE'); p.strip_dirs(); p.sort_stats('cumtime'); p.print_stats(30)\""
