#!/usr/bin/env python
"""
One-off integration test: File dependency tracking through module init.

This test verifies that file dependencies are tracked even when the file
is read at MODULE INITIALIZATION time (in __init__.py), not during the test.

This is made possible by collection-time file dependency tracking, which
installs hooks early (before test collection) and tracks which test file
caused each import.

Project structure:
    mylib/
        config.rs         # Config file read at module init
        __init__.py       # Reads config.rs at module level
        lib/
            __init__.py
            core.py
            crunch.py     # Uses config values loaded at init
        requirements.txt
    tests/
        test_a.py         # Tests that import mylib

Scenario:
1. Install mylib as a package
2. Run pytest --ezmon (base run)
3. Check: Is config.rs recorded as a file dependency?
4. Modify config.rs (change the value that crunch.py uses)
5. Run pytest --ezmon again
6. Check: Is the test selected to re-run?

Expected: config.rs change should trigger test re-run because mylib/__init__.py
reads it at import time, and the collection-time tracking captures this.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import sqlite3
from pathlib import Path


class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    BOLD = '\033[1m'
    END = '\033[0m'


def log(msg: str, level: str = "info"):
    if level == "info":
        print(f"{Colors.BLUE}[INFO]{Colors.END} {msg}")
    elif level == "success":
        print(f"{Colors.GREEN}[PASS]{Colors.END} {msg}")
    elif level == "error":
        print(f"{Colors.RED}[FAIL]{Colors.END} {msg}")
    elif level == "warning":
        print(f"{Colors.YELLOW}[WARN]{Colors.END} {msg}")
    elif level == "debug":
        print(f"  -> {msg}")


def create_project_structure(workspace: Path):
    """Create the mylib project structure."""

    # mylib/config.rs - config file read AT MODULE INIT TIME
    config_rs = workspace / "mylib" / "config.rs"
    config_rs.parent.mkdir(parents=True, exist_ok=True)
    config_rs.write_text("""\
# Configuration file
multiplier = 42
app_name = "TestApp"
""")

    # mylib/__init__.py - reads config.rs at module level (import time)
    init_py = workspace / "mylib" / "__init__.py"
    init_py.write_text("""\
\"\"\"mylib package - reads config at module init.\"\"\"
import os

# Read config.rs at module load time (module-level statement)
# This happens during test collection when the test file imports mylib
_config_path = os.path.join(os.path.dirname(__file__), "config.rs")
with open(_config_path, "r") as f:
    _config_content = f.read()

# Parse simple key=value config
CONFIG = {}
for line in _config_content.strip().split("\\n"):
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"')
        try:
            value = int(value)
        except ValueError:
            pass
        CONFIG[key] = value

# Export config values
MULTIPLIER = CONFIG.get("multiplier", 1)
APP_NAME = CONFIG.get("app_name", "Unknown")
""")

    # mylib/lib/__init__.py
    lib_init = workspace / "mylib" / "lib" / "__init__.py"
    lib_init.parent.mkdir(parents=True, exist_ok=True)
    lib_init.write_text('"""mylib.lib package."""\n')

    # mylib/lib/core.py
    core_py = workspace / "mylib" / "lib" / "core.py"
    core_py.write_text("""\
\"\"\"Core utilities.\"\"\"

def add(a, b):
    return a + b
""")

    # mylib/lib/crunch.py - uses config values loaded at mylib init
    crunch_py = workspace / "mylib" / "lib" / "crunch.py"
    crunch_py.write_text("""\
\"\"\"Crunch module - uses config loaded at mylib init.\"\"\"
from mylib import MULTIPLIER, APP_NAME


def crunch_number(x):
    \"\"\"Multiply x by the configured multiplier.

    Config was loaded at mylib import time (from config.rs).
    \"\"\"
    result = x * MULTIPLIER
    print(f"[{APP_NAME}] {x} * {MULTIPLIER} = {result}")
    return result


def get_app_info():
    \"\"\"Return app info from config.

    Config was loaded at mylib import time (from config.rs).
    \"\"\"
    return f"{APP_NAME} (multiplier={MULTIPLIER})"
""")

    # mylib/requirements.txt
    requirements = workspace / "mylib" / "requirements.txt"
    requirements.write_text("# No external dependencies for this test\n")

    # pyproject.toml for mylib (to make it installable)
    pyproject = workspace / "pyproject.toml"
    pyproject.write_text("""\
[build-system]
requires = ["setuptools>=45", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "mylib"
version = "0.1.0"
description = "Test library for file dependency tracking"

[tool.setuptools.packages.find]
where = ["."]
include = ["mylib*"]

[tool.setuptools.package-data]
mylib = ["config.rs"]
""")

    # tests/test_a.py - tests that import mylib (config read at import time)
    tests_dir = workspace / "tests"
    tests_dir.mkdir(exist_ok=True)

    test_a = tests_dir / "test_a.py"
    test_a.write_text("""\
\"\"\"Tests for mylib that trigger crunch.py functions.

These tests import mylib, which reads config.rs at MODULE INIT TIME.
The file read happens during test COLLECTION (import time), not execution.
Collection-time file dependency tracking should capture this.
\"\"\"
import mylib
from mylib.lib.crunch import crunch_number, get_app_info


def test_crunch_number():
    \"\"\"Test crunch_number uses the multiplier from config.\"\"\"
    result = crunch_number(10)
    # multiplier is 42 in config.rs, so result should be 420
    assert result == 10 * mylib.MULTIPLIER


def test_get_app_info():
    \"\"\"Test get_app_info returns config values.\"\"\"
    info = get_app_info()
    assert mylib.APP_NAME in info
    assert str(mylib.MULTIPLIER) in info


def test_config_loaded():
    \"\"\"Test that config was loaded at module init.\"\"\"
    assert mylib.MULTIPLIER == 42
    assert mylib.APP_NAME == "TestApp"
""")


def setup_workspace() -> Path:
    """Create a temporary workspace with the project."""
    temp_dir = Path(tempfile.mkdtemp(prefix="ezmon_file_dep_test_"))
    workspace = temp_dir / "project"
    workspace.mkdir()

    log(f"Created workspace: {workspace}", "debug")
    create_project_structure(workspace)

    # Initialize git (ezmon uses git for file dependency tracking)
    subprocess.run(["git", "init", "-b", "main"], cwd=workspace, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=workspace, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=workspace, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=workspace, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=workspace, capture_output=True)

    return workspace


def create_venv_and_install(workspace: Path) -> Path:
    """Create venv and install dependencies."""
    venv_path = workspace / ".venv"

    log("Creating virtual environment...")
    subprocess.run([sys.executable, "-m", "venv", str(venv_path)], check=True, capture_output=True)

    if sys.platform == "win32":
        pip = venv_path / "Scripts" / "pip"
        python_venv = venv_path / "Scripts" / "python"
    else:
        pip = venv_path / "bin" / "pip"
        python_venv = venv_path / "bin" / "python"

    # Upgrade pip
    subprocess.run([str(pip), "install", "--upgrade", "pip"], capture_output=True)

    # Install pytest-ezmon from the parent repo
    repo_root = Path(__file__).parent.parent
    log(f"Installing pytest-ezmon from {repo_root}...")
    result = subprocess.run(
        [str(pip), "install", str(repo_root)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        log(f"Failed to install pytest-ezmon: {result.stderr}", "error")
        raise RuntimeError(f"pip install failed: {result.stderr}")

    # Install mylib as editable package
    log("Installing mylib...")
    result = subprocess.run(
        [str(pip), "install", "-e", str(workspace)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        log(f"Failed to install mylib: {result.stderr}", "error")
        raise RuntimeError(f"pip install failed: {result.stderr}")

    return python_venv


def run_pytest_ezmon(workspace: Path, python_venv: Path) -> tuple:
    """Run pytest with ezmon."""
    cmd = [
        str(python_venv), "-m", "pytest",
        "--ezmon",
        "-v",
        "--color=no",
        "tests/",
    ]

    env = {
        **os.environ,
        "PYTHONPATH": str(workspace),
        "TESTMON_NET_ENABLED": "false",
    }

    result = subprocess.run(
        cmd,
        cwd=workspace,
        capture_output=True,
        text=True,
        env=env,
    )

    return result.returncode, result.stdout, result.stderr


def check_file_dependency_recorded(workspace: Path) -> bool:
    """Check if config.rs is recorded as a file dependency in the database."""
    db_path = workspace / ".testmondata"
    if not db_path.exists():
        log("Database not found!", "error")
        return False

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Debug: show all tables
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [row['name'] for row in cursor.fetchall()]
    log(f"Database tables: {tables}", "debug")

    # Check file_dependency table for config.rs
    cursor.execute("SELECT * FROM file_dependency WHERE filename LIKE '%config.rs%'")
    rows = cursor.fetchall()

    if rows:
        log(f"Found file dependency records for config.rs: {len(rows)}", "success")
        for row in rows:
            log(f"  filename={row['filename']}, sha={row['sha'][:12]}...", "debug")
        conn.close()
        return True
    else:
        log("No file dependency record for config.rs found", "error")

        # Debug: show what file dependencies ARE recorded
        cursor.execute("SELECT * FROM file_dependency")
        all_deps = cursor.fetchall()
        if all_deps:
            log("File dependencies found:", "debug")
            for row in all_deps:
                log(f"  {row['filename']}", "debug")
        else:
            log("No file dependencies recorded at all", "debug")

        # Debug: check test_execution table
        cursor.execute("SELECT test_name FROM test_execution LIMIT 10")
        tests = cursor.fetchall()
        log(f"Test executions recorded: {[row['test_name'] for row in tests]}", "debug")

        conn.close()
        return False


def modify_config_rs(workspace: Path):
    """Modify config.rs to change the multiplier value."""
    config_path = workspace / "mylib" / "config.rs"
    content = config_path.read_text()

    # Change multiplier from 42 to 100
    new_content = content.replace("multiplier = 42", "multiplier = 100")
    config_path.write_text(new_content)

    log("Modified config.rs: multiplier 42 -> 100", "debug")

    # Commit the change (needed for git-based SHA tracking)
    subprocess.run(["git", "add", "."], cwd=workspace, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Change multiplier to 100"],
        cwd=workspace,
        capture_output=True,
    )


def parse_test_results(stdout: str) -> tuple:
    """Parse pytest output to find selected/deselected tests."""
    import re

    selected = set()
    deselected = 0

    for match in re.finditer(r'(tests/test_\w+\.py::\S+)\s+(PASSED|FAILED|ERROR)', stdout):
        selected.add(match.group(1))

    deselect_match = re.search(r'(\d+) deselected', stdout)
    if deselect_match:
        deselected = int(deselect_match.group(1))

    return selected, deselected


def run_test():
    """Run the file dependency tracking test."""
    print(f"\n{Colors.BOLD}{'='*60}{Colors.END}")
    print(f"{Colors.BOLD}File Dependency Tracking Test: Import-Time Config Read{Colors.END}")
    print(f"{Colors.BOLD}{'='*60}{Colors.END}\n")

    workspace = None
    try:
        # Setup
        log("Setting up test project...")
        workspace = setup_workspace()
        python_venv = create_venv_and_install(workspace)

        # Step 1: Initial run
        print(f"\n{Colors.BOLD}Step 1: Initial pytest --ezmon run{Colors.END}")
        returncode, stdout, stderr = run_pytest_ezmon(workspace, python_venv)

        if returncode != 0:
            log(f"Initial run failed (exit code {returncode})", "error")
            print("STDOUT:", stdout)
            print("STDERR:", stderr)
            return False

        log("Initial run completed successfully", "success")
        # Show any warnings or collection info
        if "warning" in stdout.lower() or "error" in stderr.lower():
            log("Pytest output:", "debug")
            print(stdout[:2000] if len(stdout) > 2000 else stdout)
        selected, _ = parse_test_results(stdout)
        log(f"Tests run: {len(selected)}", "debug")
        for test in sorted(selected):
            log(f"  {test}", "debug")

        # Step 2: Check if config.rs is recorded as file dependency
        print(f"\n{Colors.BOLD}Step 2: Check file dependency recording{Colors.END}")
        if not check_file_dependency_recorded(workspace):
            log("FAILED: config.rs not recorded as file dependency", "error")
            return False

        # Step 3: Modify config.rs
        print(f"\n{Colors.BOLD}Step 3: Modify config.rs{Colors.END}")
        modify_config_rs(workspace)

        # Also update the test assertion to match new multiplier value
        test_file = workspace / "tests" / "test_a.py"
        test_content = test_file.read_text()
        test_content = test_content.replace("mylib.MULTIPLIER == 42", "mylib.MULTIPLIER == 100")
        test_file.write_text(test_content)
        subprocess.run(["git", "add", "."], cwd=workspace, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "Update test for new multiplier"],
            cwd=workspace,
            capture_output=True,
        )

        # Step 4: Run again and check if tests are re-selected
        print(f"\n{Colors.BOLD}Step 4: Run pytest --ezmon after config change{Colors.END}")
        returncode, stdout, stderr = run_pytest_ezmon(workspace, python_venv)

        selected, deselected = parse_test_results(stdout)

        log(f"Tests selected: {len(selected)}", "debug")
        log(f"Tests deselected: {deselected}", "debug")

        if selected:
            log("Tests were re-selected after config.rs change:", "success")
            for test in sorted(selected):
                log(f"  {test}", "debug")
            return True
        else:
            log("FAILED: No tests selected after config.rs change", "error")
            log("This means file dependency tracking did not work as expected", "error")
            return False

    finally:
        # Cleanup
        if workspace and workspace.parent.exists():
            log(f"\nCleaning up {workspace.parent}...", "debug")
            shutil.rmtree(workspace.parent)


def main():
    success = run_test()

    print(f"\n{Colors.BOLD}{'='*60}{Colors.END}")
    if success:
        print(f"{Colors.GREEN}{Colors.BOLD}TEST PASSED{Colors.END}")
        print("File dependency tracking works correctly for import-time reads!")
        print("Files read during module init (__init__.py) are now tracked.")
    else:
        print(f"{Colors.RED}{Colors.BOLD}TEST FAILED{Colors.END}")
        print("File dependency tracking needs investigation.")
    print(f"{Colors.BOLD}{'='*60}{Colors.END}\n")

    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
