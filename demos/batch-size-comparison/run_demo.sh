#!/bin/bash
#
# Batch Size Comparison Demo
# ==========================
# This script demonstrates the coverage context limitation in pytest-testmon
# and how ezmon's batch size of 1 fixes it.
#
# Usage: ./run_demo.sh
#
# The script will:
# 1. Create isolated virtual environments
# 2. Install original pytest-testmon (from PyPI) and ezmon (from GitHub)
# 3. Run the same test scenario with both plugins
# 4. Show the difference in behavior
#

set -e

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SAMPLE_PROJECT="$SCRIPT_DIR/sample_project"
WORK_DIR=$(mktemp -d)

echo "============================================================"
echo "BATCH SIZE COMPARISON DEMO"
echo "============================================================"
echo ""
echo "This demo shows how TEST_BATCH_SIZE affects dependency tracking."
echo ""
echo "Scenario:"
echo "  - src/math_utils.py has an add(a, b) function"
echo "  - tests/test_math.py has TWO tests that BOTH call add()"
echo "  - We modify add() and see which tests get re-run"
echo ""
echo "Expected:"
echo "  - Both tests should be re-run when add() changes"
echo "  - Original testmon (batch 250): Only first test is re-run (BUG)"
echo "  - Ezmon (batch 1): Both tests are re-run (CORRECT)"
echo ""
echo "Working directory: $WORK_DIR"
echo "============================================================"

# Copy sample project to work directory
cp -r "$SAMPLE_PROJECT"/* "$WORK_DIR/"
cd "$WORK_DIR"

# Function to reset the source file to original state
reset_source() {
    cat > src/math_utils.py << 'EOF'
"""Simple math utilities."""


def add(a, b):
    return a + b
EOF
}

# Function to modify the source file (change function body)
modify_source() {
    cat > src/math_utils.py << 'EOF'
"""Simple math utilities."""


def add(a, b):
    result = a + b  # Changed implementation!
    return result
EOF
}

# Function to clean testmon database
clean_testmon() {
    rm -f .testmondata .testmondata-wal .testmondata-shm 2>/dev/null || true
}

# ============================================================
# PART 1: Original pytest-testmon (PyPI, batch size 250)
# ============================================================
echo ""
echo "============================================================"
echo "PART 1: Original pytest-testmon (from PyPI)"
echo "         TEST_BATCH_SIZE = 250"
echo "============================================================"

# Create and activate venv for original testmon
python3 -m venv venv-original
source venv-original/bin/activate

pip install --quiet --upgrade pip
pip install --quiet pytest pytest-testmon

TESTMON_VERSION=$(pip show pytest-testmon | grep "^Version:" | cut -d' ' -f2)
echo ""
echo "Installed pytest-testmon version: $TESTMON_VERSION"
echo ""

reset_source
clean_testmon

echo "--- Step 1: First run (collecting coverage) ---"
echo ""
python -m pytest tests/ --testmon -v 2>&1 | grep -E "(PASSED|FAILED|collected|selected|deselected|test_)" || true

echo ""
echo "--- Step 2: Modify add() function body ---"
modify_source
echo "Changed: 'return a + b' → 'result = a + b; return result'"

echo ""
echo "--- Step 3: Second run (after modification) ---"
echo ""
python -m pytest tests/ --testmon -v 2>&1 | grep -E "(PASSED|FAILED|collected|selected|deselected|test_|changed)" || true

deactivate

# ============================================================
# PART 2: Ezmon (GitHub fork, batch size 1)
# ============================================================
echo ""
echo "============================================================"
echo "PART 2: Ezmon (from GitHub fork)"
echo "         TEST_BATCH_SIZE = 1"
echo "============================================================"

# Create and activate venv for ezmon
python3 -m venv venv-ezmon
source venv-ezmon/bin/activate

pip install --quiet --upgrade pip
pip install --quiet pytest
pip install --quiet "git+https://github.com/andreas16700/pytest-testmon.git"

EZMON_VERSION=$(pip show pytest-ezmon 2>/dev/null | grep "^Version:" | cut -d' ' -f2 || echo "installed from git")
echo ""
echo "Installed ezmon version: $EZMON_VERSION"
echo ""

reset_source
clean_testmon

echo "--- Step 1: First run (collecting coverage) ---"
echo ""
python -m pytest tests/ --ezmon -v 2>&1 | grep -E "(PASSED|FAILED|collected|selected|deselected|test_)" || true

echo ""
echo "--- Step 2: Modify add() function body ---"
modify_source
echo "Changed: 'return a + b' → 'result = a + b; return result'"

echo ""
echo "--- Step 3: Second run (after modification) ---"
echo ""
python -m pytest tests/ --ezmon -v 2>&1 | grep -E "(PASSED|FAILED|collected|selected|deselected|test_|changed)" || true

deactivate

# ============================================================
# SUMMARY
# ============================================================
echo ""
echo "============================================================"
echo "SUMMARY"
echo "============================================================"
echo ""
echo "Original pytest-testmon (batch size 250):"
echo "  → Second run shows: '1 deselected / 1 selected'"
echo "  → Only test_add_positive was re-run"
echo "  → test_add_negative was INCORRECTLY skipped!"
echo "  → BUG: Both tests call add(), but only first gets dependency"
echo ""
echo "Ezmon (batch size 1):"
echo "  → Second run shows: both tests run (no deselected)"
echo "  → Both test_add_positive and test_add_negative were re-run"
echo "  → CORRECT: Each test gets its own coverage session"
echo ""
echo "============================================================"
echo "WHY THIS HAPPENS"
echo "============================================================"
echo ""
echo "Coverage.py's dynamic contexts only attribute each line to the"
echo "FIRST test that executes it within a coverage session."
echo ""
echo "With batch size 250:"
echo "  - Multiple tests share one coverage session"
echo "  - test_add_positive runs first, gets add() line recorded"
echo "  - test_add_negative runs second, add() line NOT re-recorded"
echo ""
echo "With batch size 1:"
echo "  - Coverage is erased after EACH test"
echo "  - Each test starts with fresh coverage"
echo "  - Both tests get add() line recorded independently"
echo ""
echo "============================================================"

# Cleanup
cd /
rm -rf "$WORK_DIR"
echo "Cleaned up temporary directory."
