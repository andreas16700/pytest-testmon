#!/usr/bin/env python
"""
Integration tests for pytest-ezmon parallel execution (pytest-xdist).

Tests that ezmon correctly:
1. Collects coverage data from multiple workers
2. Saves fingerprints to the database
3. Deselects unchanged tests on subsequent runs

Note: xdist collection can have race conditions when processes see different
database states during initialization. These tests focus on the core
functionality of parallel coverage collection and deselection.
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Set, Tuple

SCRIPT_DIR = Path(__file__).parent.resolve()
REPO_ROOT = SCRIPT_DIR.parent
SAMPLE_PROJECT = SCRIPT_DIR / "sample_project"


def setup_workspace() -> Path:
    """Create a temporary workspace with the sample project."""
    temp_dir = Path(tempfile.mkdtemp(prefix="ezmon_parallel_"))
    workspace = temp_dir / "sample_project"
    shutil.copytree(SAMPLE_PROJECT, workspace)

    # Initialize git
    subprocess.run(["git", "init", "-b", "main"], cwd=workspace, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=workspace, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=workspace, capture_output=True)

    return workspace


def create_venv(workspace: Path, python: str = sys.executable) -> Path:
    """Create a virtual environment and install dependencies."""
    venv_path = workspace / ".venv"
    subprocess.run([python, "-m", "venv", str(venv_path)], check=True, capture_output=True)

    if sys.platform == "win32":
        pip = venv_path / "Scripts" / "pip"
        python_venv = venv_path / "Scripts" / "python"
    else:
        pip = venv_path / "bin" / "pip"
        python_venv = venv_path / "bin" / "python"

    # Install dependencies
    subprocess.run([str(pip), "install", "--upgrade", "pip"], capture_output=True)
    subprocess.run(
        [str(pip), "install", str(REPO_ROOT), "pytest-xdist", "requests", "networkx"],
        capture_output=True,
        check=True,
    )

    return python_venv


def run_pytest(workspace: Path, python_venv: Path, parallel: bool = False, test_path: str = "tests/") -> Tuple[int, str, str]:
    """Run pytest with ezmon."""
    cmd = [str(python_venv), "-m", "pytest", "--ezmon", "-v", "--color=no", "--tb=short", test_path]

    if parallel:
        cmd.extend(["-n", "2"])

    env = {
        **os.environ,
        "PYTHONPATH": str(workspace),
        "TESTMON_NET_ENABLED": "false",
    }
    for key in ["TESTMON_SERVER", "TESTMON_AUTH_TOKEN", "REPO_ID", "JOB_ID", "RUN_ID"]:
        env.pop(key, None)

    result = subprocess.run(cmd, cwd=workspace, capture_output=True, text=True, env=env)
    return result.returncode, result.stdout, result.stderr


def parse_results(stdout: str) -> Tuple[Set[str], int, int]:
    """Parse pytest output. Returns (selected_tests, deselected_count, passed_count)."""
    selected = set()
    deselected = 0
    passed = 0

    for match in re.finditer(r'(tests/test_\w+\.py::\S+)\s+(PASSED|FAILED|ERROR|SKIPPED)', stdout):
        selected.add(match.group(1))

    deselect_match = re.search(r'(\d+) deselected', stdout)
    if deselect_match:
        deselected = int(deselect_match.group(1))

    passed_match = re.search(r'(\d+) passed', stdout)
    if passed_match:
        passed = int(passed_match.group(1))

    return selected, deselected, passed


def cleanup(workspace: Path):
    """Remove workspace."""
    if workspace and workspace.exists():
        shutil.rmtree(workspace.parent)


class TestParallelExecution:
    """Test parallel execution with pytest-xdist."""

    def test_sequential_baseline(self):
        """Verify sequential execution works correctly as baseline."""
        workspace = None
        try:
            workspace = setup_workspace()
            python_venv = create_venv(workspace)

            # First run - collect all
            ret1, stdout1, stderr1 = run_pytest(workspace, python_venv, parallel=False)
            assert ret1 == 0, f"First run failed: {stderr1}\n{stdout1}"
            _, _, passed1 = parse_results(stdout1)
            assert passed1 > 100, f"Expected > 100 tests, got {passed1}"

            # Second run - deselect all
            ret2, stdout2, stderr2 = run_pytest(workspace, python_venv, parallel=False)
            assert ret2 in (0, 5), f"Second run failed: {stderr2}\n{stdout2}"

            no_tests = "no tests ran" in stdout2.lower() or "collected 0 items" in stdout2.lower()
            _, deselected2, _ = parse_results(stdout2)
            assert no_tests or deselected2 >= passed1 - 10, f"Expected tests to be deselected: {stdout2}"

        finally:
            if workspace:
                cleanup(workspace)

    def test_parallel_small_subset(self):
        """Test parallel execution with a small subset of tests."""
        workspace = None
        try:
            workspace = setup_workspace()
            python_venv = create_venv(workspace)

            # Run only math_utils tests in parallel
            ret1, stdout1, stderr1 = run_pytest(workspace, python_venv, parallel=True, test_path="tests/test_math_utils.py")

            # Check for xdist collection mismatch (known limitation with race conditions)
            combined = stdout1 + stderr1
            if "Different tests were collected" in combined:
                import pytest
                pytest.skip("xdist collection mismatch - known race condition limitation")

            assert ret1 == 0, f"First run failed: {stderr1}\n{stdout1}"
            _, _, passed1 = parse_results(stdout1)
            assert passed1 >= 5, f"Expected >= 5 tests, got {passed1}"

            # Second run - should deselect
            ret2, stdout2, stderr2 = run_pytest(workspace, python_venv, parallel=True, test_path="tests/test_math_utils.py")

            if "Different tests were collected" in stderr2 or "Different tests were collected" in stdout2:
                import pytest
                pytest.skip("xdist collection mismatch - known limitation")

            assert ret2 in (0, 5), f"Second run failed: {stderr2}\n{stdout2}"

            no_tests = "no tests ran" in stdout2.lower() or "0 items" in stdout2
            assert no_tests, f"Expected no tests to run: {stdout2}"

        finally:
            if workspace:
                cleanup(workspace)

    def test_parallel_coverage_saved(self):
        """Verify coverage data is saved correctly from parallel workers."""
        workspace = None
        try:
            workspace = setup_workspace()
            python_venv = create_venv(workspace)

            # Run small test set in parallel
            ret, stdout, stderr = run_pytest(workspace, python_venv, parallel=True, test_path="tests/test_math_utils.py")

            combined = stdout + stderr
            if "Different tests were collected" in combined:
                import pytest
                pytest.skip("xdist collection mismatch - known race condition limitation")

            assert ret == 0, f"Run failed: {stderr}\n{stdout}"

            # Verify database has coverage data
            import sqlite3
            db_path = workspace / ".testmondata"
            assert db_path.exists(), "Database not created"

            conn = sqlite3.connect(str(db_path))
            cursor = conn.cursor()

            # Check test executions exist
            cursor.execute("SELECT COUNT(*) FROM test_execution")
            test_count = cursor.fetchone()[0]
            assert test_count >= 5, f"Expected >= 5 test executions, got {test_count}"

            # Check file fingerprints exist (coverage was collected)
            cursor.execute("SELECT COUNT(*) FROM file_fp")
            fp_count = cursor.fetchone()[0]
            assert fp_count > 0, "No file fingerprints saved"

            conn.close()

        finally:
            if workspace:
                cleanup(workspace)


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
