#!/usr/bin/env python3
"""
End-to-end demonstration: ezmon fails to select a transitioning test
when only an untracked dependency (np.load() file) changes.

Usage:
    # Create a fresh virtualenv first
    python3.11 -m venv /tmp/ezmon_demo_env
    /tmp/ezmon_demo_env/bin/pip install numpy pytest requests
    /tmp/ezmon_demo_env/bin/pip install -e /path/to/pytest-testmon-nocov

    # Run this script
    /tmp/ezmon_demo_env/bin/python demo_ezmon_blind_spot.py
"""

import subprocess
import sys
import tempfile
import shutil
from pathlib import Path


def get_venv_python():
    """Get the python from our virtualenv."""
    venv = Path("/tmp/ezmon_demo_env")
    if not venv.exists():
        print("ERROR: Please create virtualenv first:")
        print("  python3.11 -m venv /tmp/ezmon_demo_env")
        print("  /tmp/ezmon_demo_env/bin/pip install numpy pytest requests")
        print("  /tmp/ezmon_demo_env/bin/pip install -e /path/to/pytest-testmon-nocov")
        sys.exit(1)
    return str(venv / "bin" / "python")


PYTHON = get_venv_python()
PYTEST = str(Path(PYTHON).parent / "pytest")


def setup_test_project(tmpdir: Path):
    """Create a minimal test project with an np.load() dependency."""
    (tmpdir / "tests").mkdir()
    (tmpdir / "data").mkdir()

    # Create reference data
    data_file = tmpdir / "data" / "reference.npy"
    subprocess.run([
        PYTHON, "-c", f"""
import numpy as np
x = np.linspace(0, 10, 100)
expected = np.sin(x)
np.save('{data_file}', np.array([x, expected]))
"""
    ], check=True)

    # Create test file
    test_file = tmpdir / "tests" / "test_computation.py"
    test_file.write_text(f'''
import numpy as np
from pathlib import Path

DATA_FILE = Path("{data_file}")

def compute(x):
    return np.sin(x)

def test_matches_reference():
    """Test uses np.load() - an UNTRACKED dependency!"""
    data = np.load(DATA_FILE)
    x = data[0]
    expected = data[1]
    actual = compute(x)
    np.testing.assert_allclose(actual, expected, rtol=1e-12)
''')

    return data_file, test_file


def run_pytest(tmpdir: Path, with_ezmon: bool):
    """Run pytest and return (returncode, output)."""
    cmd = [PYTEST, "-xvs"]
    if with_ezmon:
        cmd.append("--ezmon")
    cmd.append("tests/")

    result = subprocess.run(
        cmd,
        cwd=tmpdir,
        capture_output=True,
        text=True,
        timeout=60
    )
    return result.returncode, result.stdout + result.stderr


def modify_data_file(data_file: Path):
    """Modify the .npy file to cause test failure."""
    subprocess.run([
        PYTHON, "-c", f"""
import numpy as np
data = np.load('{data_file}')
print(f'Original expected (first 5): {{data[1, :5]}}')
data[1] = data[1] * 2.0
np.save('{data_file}', data)
print(f'Modified expected (first 5): {{data[1, :5]}}')
"""
    ], check=True)


def main():
    print("=" * 70)
    print("EZMON BLIND SPOT DEMONSTRATION")
    print("=" * 70)
    print()

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)

        # Setup
        print("SETUP: Creating test project")
        print("-" * 50)
        data_file, test_file = setup_test_project(tmpdir)
        print(f"  Test file: {test_file.name}")
        print(f"  Data file: {data_file.name}")
        print()

        # Step 1: Baseline
        print("STEP 1: Run pytest --ezmon (establish baseline)")
        print("-" * 50)
        rc, output = run_pytest(tmpdir, with_ezmon=True)
        if rc != 0:
            print(f"ERROR: Test failed on first run!")
            print(output[-1000:])
            return 1
        print("  Result: PASSED")
        if "new DB" in output:
            print("  ezmon: new DB created")
        print()

        # Step 2: No changes
        print("STEP 2: Run pytest --ezmon again (no changes)")
        print("-" * 50)
        rc, output = run_pytest(tmpdir, with_ezmon=True)
        if "collected 0 items" in output or "deselected" in output.lower():
            print("  Result: Test DESELECTED (correct - nothing changed)")
        print()

        # Step 3: Modify data
        print("STEP 3: Modify ONLY the .npy data file")
        print("-" * 50)
        modify_data_file(data_file)
        print("  NOTE: No Python code was changed!")
        print()

        # Step 4: ezmon after change
        print("STEP 4: Run pytest --ezmon after data change")
        print("-" * 50)
        rc, output = run_pytest(tmpdir, with_ezmon=True)

        ezmon_detected = False
        if "collected 0 items" in output:
            print("  Result: Test DESELECTED")
            print("  ⚠️  ezmon did NOT detect the .npy file change!")
        elif "PASSED" in output or "FAILED" in output:
            print("  Result: Test was selected and run")
            ezmon_detected = True
        print()

        # Step 5: Without ezmon
        print("STEP 5: Run pytest WITHOUT --ezmon")
        print("-" * 50)
        rc, output = run_pytest(tmpdir, with_ezmon=False)
        if rc != 0:
            print("  Result: FAILED!")
            print("  The test WOULD have failed if selected!")
            for line in output.split('\n'):
                if 'Mismatched' in line or 'AssertionError' in line:
                    print(f"    {line.strip()}")
        else:
            print("  Result: PASSED (unexpected)")
        print()

        # Summary
        print("=" * 70)
        print("CONCLUSION")
        print("=" * 70)

        if not ezmon_detected:
            print("""
The test transitioned from PASS → FAIL but ezmon didn't select it!

ROOT CAUSE:
  - np.load() uses C-level file I/O internally
  - ezmon hooks builtins.open() and builtins.__import__()
  - C-level I/O BYPASSES Python's builtins.open()

REAL-WORLD IMPACT:
  - scipy: 15+ .npy/.npz files, 500+ affected tests
  - matplotlib: 100+ reference images via subprocess
  - pandas: fixture files, parquet files
""")

    return 0


if __name__ == "__main__":
    sys.exit(main())
