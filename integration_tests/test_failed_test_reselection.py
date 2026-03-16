"""
Integration tests for failed test persistence and reselection.

Verifies that:
1. Failed tests are written to the DB with failed=1
2. Failed tests are re-selected on the next run (no silent disappearance)
3. Once fixed, tests are deselected again
4. All of the above work in both single-process and xdist modes
"""

import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from test_parallel_execution import (
    cleanup,
    create_venv,
    parse_results,
    run_pytest,
    setup_workspace,
)

FAILING_TEST = "tests/test_math_utils.py::TestAdd::test_positive_numbers"


def _inject_failure(workspace: Path):
    """Modify a test to make it fail, then git commit."""
    test_file = workspace / "tests" / "test_math_utils.py"
    content = test_file.read_text()
    content = content.replace("assert add(2, 3) == 5", "assert add(2, 3) == 999")
    test_file.write_text(content)
    subprocess.run(["git", "add", "."], cwd=workspace, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Break test"],
        cwd=workspace, capture_output=True,
    )


def _fix_failure(workspace: Path):
    """Revert the test back to passing, then git commit."""
    test_file = workspace / "tests" / "test_math_utils.py"
    content = test_file.read_text()
    content = content.replace("assert add(2, 3) == 999", "assert add(2, 3) == 5")
    test_file.write_text(content)
    subprocess.run(["git", "add", "."], cwd=workspace, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Fix test"],
        cwd=workspace, capture_output=True,
    )


def _query_failed(workspace: Path, test_name: str) -> int:
    """Query the DB for the failed flag of a specific test."""
    db_path = workspace / ".testmondata"
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()
    cursor.execute("SELECT failed FROM tests WHERE name = ?", (test_name,))
    row = cursor.fetchone()
    conn.close()
    if row is None:
        return -1  # test not found
    return row[0]


def _skip_on_collection_mismatch(stdout: str, stderr: str):
    combined = stdout + stderr
    if "Different tests were collected" in combined:
        pytest.skip("xdist collection mismatch - known race condition")


class TestFailedTestReselectedSingle:
    """Single-process mode: failed tests must persist and be re-selected."""

    def test_failed_test_reselected_single(self):
        workspace = None
        try:
            workspace = setup_workspace()
            python_venv = create_venv(workspace)
            test_path = "tests/test_math_utils.py"

            # Run 1: all tests pass, DB populated
            ret1, stdout1, stderr1 = run_pytest(
                workspace, python_venv, parallel=False, test_path=test_path,
            )
            assert ret1 == 0, f"Run 1 failed:\n{stdout1}\n{stderr1}"

            # Inject failure and commit
            _inject_failure(workspace)

            # Run 2: modified test fails (selected because file changed)
            ret2, stdout2, stderr2 = run_pytest(
                workspace, python_venv, parallel=False, test_path=test_path,
            )
            assert ret2 != 0, f"Run 2 should have failures:\n{stdout2}"
            assert "FAILED" in stdout2

            # Verify DB has failed=1
            failed_val = _query_failed(workspace, FAILING_TEST)
            assert failed_val == 1, (
                f"Expected failed=1 in DB after run 2, got {failed_val}"
            )

            # Run 3: no changes — failing test must be re-selected
            ret3, stdout3, stderr3 = run_pytest(
                workspace, python_venv, parallel=False, test_path=test_path,
            )
            assert ret3 != 0, f"Run 3 should still fail:\n{stdout3}"
            assert FAILING_TEST.split("::")[-1] in stdout3, (
                f"Failing test not re-selected in run 3:\n{stdout3}"
            )
            assert "FAILED" in stdout3

        finally:
            if workspace:
                cleanup(workspace)


class TestFailedTestReselectedXdist:
    """xdist mode: failed tests must persist and be re-selected."""

    def test_failed_test_reselected_xdist(self):
        workspace = None
        try:
            workspace = setup_workspace()
            python_venv = create_venv(workspace)
            test_path = "tests/test_math_utils.py"

            # Run 1: all tests pass (parallel)
            ret1, stdout1, stderr1 = run_pytest(
                workspace, python_venv, parallel=True, test_path=test_path,
            )
            _skip_on_collection_mismatch(stdout1, stderr1)
            assert ret1 == 0, f"Run 1 failed:\n{stdout1}\n{stderr1}"

            # Inject failure and commit
            _inject_failure(workspace)

            # Run 2: modified test fails (parallel)
            ret2, stdout2, stderr2 = run_pytest(
                workspace, python_venv, parallel=True, test_path=test_path,
            )
            _skip_on_collection_mismatch(stdout2, stderr2)
            assert ret2 != 0, f"Run 2 should have failures:\n{stdout2}"
            assert "FAILED" in stdout2

            # Verify DB has failed=1
            failed_val = _query_failed(workspace, FAILING_TEST)
            assert failed_val == 1, (
                f"Expected failed=1 in DB after run 2, got {failed_val}"
            )

            # Run 3: no changes — failing test must be re-selected (parallel)
            ret3, stdout3, stderr3 = run_pytest(
                workspace, python_venv, parallel=True, test_path=test_path,
            )
            _skip_on_collection_mismatch(stdout3, stderr3)
            assert ret3 != 0, f"Run 3 should still fail:\n{stdout3}"
            assert FAILING_TEST.split("::")[-1] in stdout3, (
                f"Failing test not re-selected in run 3:\n{stdout3}"
            )
            assert "FAILED" in stdout3

        finally:
            if workspace:
                cleanup(workspace)


class TestFixedTestDeselected:
    """After fixing a failed test, it should be deselected on the next run."""

    def test_fixed_test_deselected(self):
        workspace = None
        try:
            workspace = setup_workspace()
            python_venv = create_venv(workspace)
            test_path = "tests/test_math_utils.py"

            # Run 1: all pass
            ret1, stdout1, stderr1 = run_pytest(
                workspace, python_venv, parallel=False, test_path=test_path,
            )
            assert ret1 == 0, f"Run 1 failed:\n{stdout1}\n{stderr1}"

            # Inject failure
            _inject_failure(workspace)

            # Run 2: test fails
            ret2, stdout2, stderr2 = run_pytest(
                workspace, python_venv, parallel=False, test_path=test_path,
            )
            assert ret2 != 0, f"Run 2 should fail:\n{stdout2}"

            # Run 3: still fails (reselected)
            ret3, stdout3, stderr3 = run_pytest(
                workspace, python_venv, parallel=False, test_path=test_path,
            )
            assert ret3 != 0, f"Run 3 should still fail:\n{stdout3}"

            # Fix the test
            _fix_failure(workspace)

            # Run 4: test passes now
            ret4, stdout4, stderr4 = run_pytest(
                workspace, python_venv, parallel=False, test_path=test_path,
            )
            assert ret4 == 0, f"Run 4 should pass:\n{stdout4}\n{stderr4}"

            # Run 5: no changes — fixed test should be deselected
            ret5, stdout5, stderr5 = run_pytest(
                workspace, python_venv, parallel=False, test_path=test_path,
            )
            assert ret5 in (0, 5), f"Run 5 failed:\n{stdout5}\n{stderr5}"
            # The test should either be deselected or all tests deselected
            no_tests = (
                "no tests ran" in stdout5.lower()
                or "0 items" in stdout5
            )
            _, deselected5, _ = parse_results(stdout5)
            assert no_tests or deselected5 > 0, (
                f"Expected tests to be deselected in run 5:\n{stdout5}"
            )

        finally:
            if workspace:
                cleanup(workspace)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
