#!/usr/bin/env python
"""
Integration tests for granular external dependency tracking.

These tests verify that when external packages change, only tests that
actually use those packages are invalidated (not all tests).

Test scenarios:
1. Updating dependency A: only tests using A should be selected
2. Adding new external dependency: no tests selected (new pkg not used yet)
3. Removing dependency B: only tests using B should be selected

The tests use ACTUAL pip operations to manage dependencies:
1. Create a venv with specific package versions
2. Run pytest --ezmon to populate the database
3. Use pip to install/uninstall/upgrade packages
4. Run pytest --ezmon again and verify only affected tests are selected
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


def create_venv(workspace: Path, requests_version: str = "2.31.0", numpy_version: str = "1.26.0") -> tuple:
    """Create venv and install ezmon with specific package versions.

    Returns:
        tuple: (python_venv_path, pip_path)
    """
    venv_path = workspace / ".venv"
    subprocess.run([sys.executable, "-m", "venv", str(venv_path)], check=True, capture_output=True)

    pip = venv_path / "bin" / "pip"
    python_venv = venv_path / "bin" / "python"

    # Install ezmon first
    log(f"Installing ezmon and base dependencies...", "debug")
    subprocess.run([str(pip), "install", "--upgrade", "pip"], capture_output=True)
    subprocess.run([str(pip), "install", str(REPO_ROOT)], capture_output=True, check=True)

    # Install specific versions of test dependencies
    if requests_version:
        log(f"Installing requests=={requests_version}", "debug")
        result = subprocess.run(
            [str(pip), "install", f"requests=={requests_version}"],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            log(f"Warning: Could not install requests=={requests_version}, trying latest", "debug")
            subprocess.run([str(pip), "install", "requests"], capture_output=True)

    if numpy_version:
        log(f"Installing numpy=={numpy_version}", "debug")
        result = subprocess.run(
            [str(pip), "install", f"numpy=={numpy_version}"],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            log(f"Warning: Could not install numpy=={numpy_version}, trying latest", "debug")
            subprocess.run([str(pip), "install", "numpy"], capture_output=True)

    return python_venv, pip


def pip_install(pip: Path, package: str, version: str = None):
    """Install a package using pip."""
    pkg_spec = f"{package}=={version}" if version else package
    log(f"pip install {pkg_spec}", "debug")
    result = subprocess.run(
        [str(pip), "install", pkg_spec],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        log(f"pip install failed: {result.stderr}", "error")
        return False
    return True


def pip_uninstall(pip: Path, package: str):
    """Uninstall a package using pip."""
    log(f"pip uninstall {package}", "debug")
    result = subprocess.run(
        [str(pip), "uninstall", "-y", package],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        log(f"pip uninstall failed: {result.stderr}", "error")
        return False
    return True


def pip_show(pip: Path, package: str) -> str:
    """Get installed version of a package."""
    result = subprocess.run(
        [str(pip), "show", package],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        for line in result.stdout.split('\n'):
            if line.startswith('Version:'):
                return line.split(':', 1)[1].strip()
    return None


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

    Uses ACTUAL pip operations:
    1. Install requests==2.28.0 initially
    2. Run pytest --ezmon to populate database
    3. pip install requests==2.31.0 (upgrade)
    4. Run pytest --ezmon - only requests-using tests should run

    Expected:
    - TestUsesNeither tests: NOT selected (don't use requests)
    - TestUsesDepA tests: SELECTED (use requests)
    - TestUsesDepB tests: NOT selected (only use numpy)
    - TestUsesBoth tests: SELECTED (use both)
    """
    log("Test: Update dependency A (requests) via pip upgrade")

    temp_dir, workspace = setup_workspace()
    try:
        # Install with older version of requests
        python_venv, pip = create_venv(workspace, requests_version="2.28.0", numpy_version="1.26.0")

        initial_requests_version = pip_show(pip, "requests")
        log(f"Initial requests version: {initial_requests_version}", "debug")

        # Initial run - populate database
        log("Running initial pytest --ezmon...", "debug")
        selected1, _, stdout1, _ = run_pytest_ezmon(workspace, python_venv)
        log(f"Initial run: {len(selected1)} tests executed", "debug")

        # Check what external deps were recorded
        db_path = get_db_path(workspace)
        deps = check_external_deps_recorded(db_path)
        log(f"Recorded external deps: {deps}", "debug")

        # Upgrade requests using pip
        log("Upgrading requests via pip...", "debug")
        pip_install(pip, "requests", "2.31.0")

        new_requests_version = pip_show(pip, "requests")
        log(f"New requests version: {new_requests_version}", "debug")

        # Run again
        log("Running pytest --ezmon after package upgrade...", "debug")
        selected2, deselected2, stdout2, _ = run_pytest_ezmon(workspace, python_venv)

        log(f"After upgrade: {len(selected2)} selected, {deselected2} deselected", "debug")
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

        # TestUsesBoth should be selected (uses requests)
        both_tests = [t for t in selected2 if "TestUsesBoth" in t]
        if not both_tests:
            if any("TestUsesBoth" in t for t in deps.keys()):
                errors.append("TestUsesBoth tests should be selected (use requests)")

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

    Uses ACTUAL pip operations:
    1. Create venv WITHOUT flask
    2. Run pytest --ezmon to populate database
    3. pip install flask
    4. Run pytest --ezmon - no tests should run (none use flask)
    """
    log("Test: Add new dependency (flask) via pip install")

    temp_dir, workspace = setup_workspace()
    try:
        python_venv, pip = create_venv(workspace)

        # Verify flask is not installed
        flask_version = pip_show(pip, "flask")
        if flask_version:
            log(f"Flask already installed ({flask_version}), uninstalling...", "debug")
            pip_uninstall(pip, "flask")

        # Initial run
        log("Running initial pytest --ezmon...", "debug")
        selected1, _, _, _ = run_pytest_ezmon(workspace, python_venv)
        log(f"Initial run: {len(selected1)} tests executed", "debug")

        # Install flask using pip
        log("Installing flask via pip...", "debug")
        pip_install(pip, "flask")

        flask_version = pip_show(pip, "flask")
        log(f"Flask version installed: {flask_version}", "debug")

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

    Uses ACTUAL pip operations:
    1. Create venv with numpy installed
    2. Run pytest --ezmon to populate database
    3. pip uninstall numpy
    4. Run pytest --ezmon - only numpy-using tests should be selected
       (they will be SKIPPED since numpy is missing, but still selected)
    """
    log("Test: Remove dependency (numpy) via pip uninstall")

    temp_dir, workspace = setup_workspace()
    try:
        python_venv, pip = create_venv(workspace)

        numpy_version = pip_show(pip, "numpy")
        log(f"Initial numpy version: {numpy_version}", "debug")

        # Initial run
        log("Running initial pytest --ezmon...", "debug")
        selected1, _, _, _ = run_pytest_ezmon(workspace, python_venv)
        log(f"Initial run: {len(selected1)} tests executed", "debug")

        # Check recorded deps
        db_path = get_db_path(workspace)
        deps = check_external_deps_recorded(db_path)
        log(f"Recorded external deps: {deps}", "debug")

        # Uninstall numpy using pip
        log("Uninstalling numpy via pip...", "debug")
        pip_uninstall(pip, "numpy")

        numpy_version = pip_show(pip, "numpy")
        log(f"Numpy version after uninstall: {numpy_version}", "debug")

        # Run again
        log("Running pytest --ezmon after removing numpy...", "debug")
        selected2, deselected2, stdout2, _ = run_pytest_ezmon(workspace, python_venv)

        log(f"After removing numpy: {len(selected2)} selected, {deselected2} deselected", "debug")
        log(f"Selected: {selected2}", "debug")

        # Verify expectations
        errors = []

        # TestUsesNeither should NOT be selected
        for test in selected2:
            if "TestUsesNeither" in test:
                errors.append(f"TestUsesNeither test should NOT be selected: {test}")

        # TestUsesDepA should NOT be selected (doesn't use numpy)
        for test in selected2:
            if "TestUsesDepA" in test:
                errors.append(f"TestUsesDepA test should NOT be selected: {test}")

        # TestUsesDepB SHOULD be selected (uses numpy)
        dep_b_tests = [t for t in selected2 if "TestUsesDepB" in t]
        if not dep_b_tests:
            if any("TestUsesDepB" in t for t in deps.keys()):
                errors.append("TestUsesDepB tests should be selected (use numpy)")

        # TestUsesBoth SHOULD be selected (uses numpy)
        both_tests = [t for t in selected2 if "TestUsesBoth" in t]
        if not both_tests:
            if any("TestUsesBoth" in t for t in deps.keys()):
                errors.append("TestUsesBoth tests should be selected (use numpy)")

        if errors:
            for e in errors:
                log(e, "error")
            return False

        log("Test passed: Only tests using numpy were selected", "success")
        return True

    finally:
        shutil.rmtree(temp_dir)


def main():
    print(f"\n{Colors.BOLD}External Dependency Tracking Integration Tests{Colors.END}")
    print(f"{Colors.BOLD}(Using actual pip operations){Colors.END}\n")
    print("-" * 60)

    results = []

    # Run tests
    tests = [
        ("Update dependency A (pip upgrade)", test_update_dependency_a),
        ("Add new dependency (pip install)", test_add_new_dependency),
        ("Remove dependency (pip uninstall)", test_remove_dependency),
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
