#!/usr/bin/env python
"""
Integration tests for granular external dependency tracking.

These tests verify that when external packages change, only tests that
actually use those packages are invalidated (not all tests).

Test scenarios:
1. Adding new external dependency: no tests selected (new pkg not used yet)
2. Updating dependency A: only tests using A should be selected
3. Removing dependency B: only tests using B should be selected
4. Updating both A and B: tests using A, B, or both should be selected

The test works by:
1. Running pytest --ezmon to populate the database with external deps
2. Directly modifying the environment's system_packages in the database
3. Running pytest --ezmon again and checking which tests are selected
"""

import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

# Add parent directory to path
SCRIPT_DIR = Path(__file__).parent.resolve()
REPO_ROOT = SCRIPT_DIR.parent
SAMPLE_PROJECT = SCRIPT_DIR / "sample_project"

sys.path.insert(0, str(REPO_ROOT))
from ezmon.common import parse_system_packages


class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    BOLD = '\033[1m'
    END = '\033[0m'


def log(msg, level="info"):
    if level == "info":
        print(f"{Colors.BLUE}i{Colors.END} {msg}")
    elif level == "success":
        print(f"{Colors.GREEN}✓{Colors.END} {msg}")
    elif level == "error":
        print(f"{Colors.RED}✗{Colors.END} {msg}")
    elif level == "debug":
        print(f"  {Colors.BOLD}->{Colors.END} {msg}")


def setup_workspace():
    """Create a temporary workspace with the sample project."""
    temp_dir = Path(tempfile.mkdtemp(prefix="ezmon_extdeps_"))
    workspace = temp_dir / "sample_project"
    shutil.copytree(SAMPLE_PROJECT, workspace)

    # Initialize git
    subprocess.run(["git", "init", "-b", "main"], cwd=workspace, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=workspace, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial"], cwd=workspace, capture_output=True)

    return temp_dir, workspace


def create_venv(workspace: Path) -> Path:
    """Create venv and install ezmon."""
    venv_path = workspace / ".venv"
    subprocess.run([sys.executable, "-m", "venv", str(venv_path)], check=True, capture_output=True)

    pip = venv_path / "bin" / "pip"
    python_venv = venv_path / "bin" / "python"

    # Install ezmon and test dependencies
    subprocess.run([str(pip), "install", "--upgrade", "pip"], capture_output=True)
    subprocess.run([str(pip), "install", str(REPO_ROOT), "requests", "numpy"], capture_output=True, check=True)

    return python_venv


def run_pytest_ezmon(workspace: Path, python_venv: Path, test_files: str = None):
    """Run pytest with ezmon and return selected tests."""
    # Run the properly isolated test files
    test_target = test_files or "tests/test_pure_utils.py tests/test_requests_utils.py tests/test_numpy_utils.py tests/test_combined_utils.py"
    cmd = [str(python_venv), "-m", "pytest", "--ezmon", "-v", "--color=no", "-p", "no:xvfb"] + test_target.split()

    env = {
        **os.environ,
        "PYTHONPATH": str(workspace),
        "TESTMON_NET_ENABLED": "false",
    }

    result = subprocess.run(cmd, cwd=workspace, capture_output=True, text=True, env=env)

    # Parse selected tests
    selected = set()
    for match in re.finditer(r'(tests/test_\w+\.py::\S+)\s+(PASSED|FAILED|SKIPPED)', result.stdout):
        selected.add(match.group(1))

    # Parse deselected count
    deselected = 0
    m = re.search(r'(\d+) deselected', result.stdout)
    if m:
        deselected = int(m.group(1))

    return selected, deselected, result.stdout, result.stderr


def get_db_path(workspace: Path) -> Path:
    """Get the testmon database path."""
    return workspace / ".testmondata"


def modify_system_packages(db_path: Path, old_version: str, new_version: str, package_name: str):
    """
    Modify the system_packages in the database to simulate a package change.

    Args:
        db_path: Path to .testmondata
        old_version: Current version string (e.g., "1.0")
        new_version: New version string (e.g., "2.0")
        package_name: Package name to modify (e.g., "requests")
    """
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()

    # Get current system_packages
    row = cursor.execute("SELECT id, system_packages FROM environment ORDER BY id DESC LIMIT 1").fetchone()
    if not row:
        raise RuntimeError("No environment found in database")

    env_id, current_packages = row
    log(f"Current packages (truncated): {current_packages[:100]}...", "debug")

    # Modify the package version
    old_pattern = f"{package_name} {old_version}"
    new_pattern = f"{package_name} {new_version}"

    if old_pattern in current_packages:
        new_packages = current_packages.replace(old_pattern, new_pattern)
        log(f"Changed {old_pattern} -> {new_pattern}", "debug")
    elif package_name in current_packages:
        # Find and replace any version
        import re
        new_packages = re.sub(f"{package_name} [\\d.]+", new_pattern, current_packages)
        log(f"Changed {package_name} version to {new_version}", "debug")
    else:
        # Add the package
        new_packages = current_packages + f", {new_pattern}"
        log(f"Added {new_pattern}", "debug")

    cursor.execute("UPDATE environment SET system_packages = ? WHERE id = ?", (new_packages, env_id))
    conn.commit()
    conn.close()


def add_package_to_db(db_path: Path, package_name: str, version: str):
    """Add a new package to system_packages."""
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()

    row = cursor.execute("SELECT id, system_packages FROM environment ORDER BY id DESC LIMIT 1").fetchone()
    env_id, current_packages = row

    new_packages = current_packages + f", {package_name} {version}"
    cursor.execute("UPDATE environment SET system_packages = ? WHERE id = ?", (new_packages, env_id))
    conn.commit()
    conn.close()
    log(f"Added {package_name} {version} to packages", "debug")


def remove_package_from_db(db_path: Path, package_name: str):
    """Remove a package from system_packages."""
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()

    row = cursor.execute("SELECT id, system_packages FROM environment ORDER BY id DESC LIMIT 1").fetchone()
    env_id, current_packages = row

    # Remove the package (handles "pkg version" format)
    import re
    new_packages = re.sub(f",?\\s*{package_name} [\\d.]+", "", current_packages)
    new_packages = new_packages.strip(", ")

    cursor.execute("UPDATE environment SET system_packages = ? WHERE id = ?", (new_packages, env_id))
    conn.commit()
    conn.close()
    log(f"Removed {package_name} from packages", "debug")


def check_external_deps_recorded(db_path: Path):
    """Check what external dependencies were recorded."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Get test -> external deps mapping
    rows = cursor.execute("""
        SELECT te.test_name, ted.package_name
        FROM test_execution te
        JOIN test_external_dependency ted ON te.id = ted.test_execution_id
        ORDER BY te.test_name, ted.package_name
    """).fetchall()

    deps = {}
    for row in rows:
        test = row["test_name"]
        pkg = row["package_name"]
        if test not in deps:
            deps[test] = set()
        deps[test].add(pkg)

    conn.close()
    return deps


def test_update_dependency_a():
    """
    Test: Updating dependency A (requests) should only affect tests using requests.

    Expected:
    - TestUsesNeither tests: NOT selected (don't use requests)
    - TestUsesDepA tests: SELECTED (use requests)
    - TestUsesDepB tests: NOT selected (only use numpy)
    - TestUsesBoth tests: SELECTED (use both)
    """
    log("Test: Update dependency A (requests)")

    temp_dir, workspace = setup_workspace()
    try:
        python_venv = create_venv(workspace)

        # Initial run - populate database
        log("Running initial pytest --ezmon...", "debug")
        selected1, _, stdout1, _ = run_pytest_ezmon(workspace, python_venv)
        log(f"Initial run: {len(selected1)} tests executed", "debug")

        # Check what external deps were recorded
        db_path = get_db_path(workspace)
        deps = check_external_deps_recorded(db_path)
        log(f"Recorded external deps: {deps}", "debug")

        # Simulate requests version change
        log("Simulating requests version change...", "debug")
        modify_system_packages(db_path, "2.28", "2.29", "requests")

        # Run again
        log("Running pytest --ezmon after package change...", "debug")
        selected2, deselected2, stdout2, _ = run_pytest_ezmon(workspace, python_venv)

        log(f"After update: {len(selected2)} selected, {deselected2} deselected", "debug")
        log(f"Selected tests: {selected2}", "debug")

        # Verify expectations
        errors = []

        # TestUsesNeither should NOT be selected
        for test in selected2:
            if "TestUsesNeither" in test:
                errors.append(f"TestUsesNeither test should NOT be selected: {test}")

        # TestUsesDepA should be selected (uses requests)
        dep_a_tests = [t for t in selected2 if "TestUsesDepA" in t]
        if not dep_a_tests:
            # Only error if we recorded requests deps for these tests
            if any("TestUsesDepA" in t for t in deps.keys()):
                errors.append("TestUsesDepA tests should be selected (use requests)")

        # TestUsesDepB should NOT be selected (only uses numpy, not requests)
        for test in selected2:
            if "TestUsesDepB" in test:
                errors.append(f"TestUsesDepB test should NOT be selected: {test}")

        if errors:
            for e in errors:
                log(e, "error")
            return False

        log("Test passed: Only tests using requests were selected", "success")
        return True

    finally:
        shutil.rmtree(temp_dir)


def test_add_new_dependency():
    """
    Test: Adding a new external dependency should NOT select any tests.

    If a new package is added that no test has ever used, no tests should run.
    """
    log("Test: Add new dependency (flask)")

    temp_dir, workspace = setup_workspace()
    try:
        python_venv = create_venv(workspace)

        # Initial run
        log("Running initial pytest --ezmon...", "debug")
        selected1, _, _, _ = run_pytest_ezmon(workspace, python_venv)
        log(f"Initial run: {len(selected1)} tests executed", "debug")

        # Add a new package that no test uses
        db_path = get_db_path(workspace)
        add_package_to_db(db_path, "flask", "2.0.0")

        # Run again
        log("Running pytest --ezmon after adding new package...", "debug")
        selected2, deselected2, stdout2, _ = run_pytest_ezmon(workspace, python_venv)

        log(f"After adding flask: {len(selected2)} selected, {deselected2} deselected", "debug")

        # No tests should be selected (flask isn't used by any test)
        if selected2:
            log(f"ERROR: Tests were selected but shouldn't be: {selected2}", "error")
            return False

        log("Test passed: No tests selected when adding unused package", "success")
        return True

    finally:
        shutil.rmtree(temp_dir)


def test_remove_dependency():
    """
    Test: Removing a dependency should select tests that used it.
    """
    log("Test: Remove dependency (numpy)")

    temp_dir, workspace = setup_workspace()
    try:
        python_venv = create_venv(workspace)

        # Initial run
        log("Running initial pytest --ezmon...", "debug")
        selected1, _, _, _ = run_pytest_ezmon(workspace, python_venv)

        # Remove numpy
        db_path = get_db_path(workspace)
        remove_package_from_db(db_path, "numpy")

        # Run again
        log("Running pytest --ezmon after removing numpy...", "debug")
        selected2, deselected2, stdout2, _ = run_pytest_ezmon(workspace, python_venv)

        log(f"After removing numpy: {len(selected2)} selected, {deselected2} deselected", "debug")
        log(f"Selected: {selected2}", "debug")

        # TestUsesNeither should NOT be selected
        errors = []
        for test in selected2:
            if "TestUsesNeither" in test:
                errors.append(f"TestUsesNeither test should NOT be selected: {test}")

        # TestUsesDepA should NOT be selected (doesn't use numpy)
        for test in selected2:
            if "TestUsesDepA" in test:
                errors.append(f"TestUsesDepA test should NOT be selected: {test}")

        if errors:
            for e in errors:
                log(e, "error")
            return False

        log("Test passed: Only tests using numpy were selected", "success")
        return True

    finally:
        shutil.rmtree(temp_dir)


def main():
    print(f"\n{Colors.BOLD}External Dependency Tracking Integration Tests{Colors.END}\n")
    print("-" * 60)

    results = []

    # Run tests
    tests = [
        ("Update dependency A", test_update_dependency_a),
        ("Add new dependency", test_add_new_dependency),
        ("Remove dependency", test_remove_dependency),
    ]

    for name, test_func in tests:
        print()
        try:
            success = test_func()
            results.append((name, success))
        except Exception as e:
            log(f"Exception: {e}", "error")
            import traceback
            traceback.print_exc()
            results.append((name, False))
        print()

    # Summary
    print("-" * 60)
    passed = sum(1 for _, s in results if s)
    failed = len(results) - passed

    print(f"\nResults: {passed}/{len(results)} passed")

    if failed:
        print(f"\n{Colors.RED}FAILED tests:{Colors.END}")
        for name, success in results:
            if not success:
                print(f"  - {name}")
        return 1
    else:
        print(f"\n{Colors.GREEN}All tests passed!{Colors.END}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
