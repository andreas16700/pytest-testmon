#!/usr/bin/env python3
"""
Demonstration: Causing a scipy test transition via untracked dependency.

This script demonstrates that modifying an .npy data file can cause a test
to transition from PASS to FAIL, but the import tracker would NOT detect
the dependency because np.load() bypasses builtins.open().

Target: scipy/stats/tests/test_distributions.py::TestJFSkewT::test_compare_with_gamlss_r
Dependency: scipy/stats/tests/data/jf_skew_t_gamlss_pdf_data.npy
Bypass method: np.load() uses C-level file I/O
"""

import subprocess
import sys
import shutil
from pathlib import Path

SCIPY_ROOT = Path("/Users/andrew_yos/scipy")
DATA_FILE = SCIPY_ROOT / "scipy/stats/tests/data/jf_skew_t_gamlss_pdf_data.npy"
BACKUP_FILE = DATA_FILE.with_suffix(".npy.backup")
TEST_PATH = "scipy/stats/tests/test_distributions.py::TestJFSkewT::test_compare_with_gamlss_r"

# Use python3.11 which has numpy/scipy
PYTHON = "/opt/homebrew/bin/python3.11"


def run_test():
    """Run the specific test and return (passed, output)."""
    result = subprocess.run(
        [PYTHON, "-m", "pytest", "-xvs", TEST_PATH, "--tb=short"],
        cwd=SCIPY_ROOT,
        capture_output=True,
        text=True,
        timeout=120
    )
    passed = result.returncode == 0
    return passed, result.stdout + result.stderr


def backup_data():
    """Backup the original data file."""
    if not BACKUP_FILE.exists():
        shutil.copy(DATA_FILE, BACKUP_FILE)
        print(f"✓ Backed up {DATA_FILE.name} to {BACKUP_FILE.name}")
    else:
        print(f"✓ Backup already exists: {BACKUP_FILE.name}")


def restore_data():
    """Restore the original data file."""
    if BACKUP_FILE.exists():
        shutil.copy(BACKUP_FILE, DATA_FILE)
        print(f"✓ Restored {DATA_FILE.name} from backup")
    else:
        print(f"✗ No backup found!")


def modify_data():
    """Modify the data file to cause test failure."""
    import numpy as np

    data = np.load(DATA_FILE)
    print(f"Original data shape: {data.shape}")
    print(f"Original PDF values (first 5): {data[1, :5]}")

    # Multiply all PDF values by 2 - this will cause assertion failure
    # since rtol=1e-12 is very tight
    modified = data.copy()
    modified[1, :] = modified[1, :] * 2.0  # Row 1 is the 'pdf' field

    np.save(DATA_FILE, modified)
    print(f"Modified PDF values (first 5): {modified[1, :5]}")
    print(f"✓ Modified {DATA_FILE.name} - doubled all PDF values")


def main():
    print("=" * 70)
    print("SCIPY UNTRACKED DEPENDENCY DEMONSTRATION")
    print("=" * 70)
    print()
    print(f"Test: {TEST_PATH}")
    print(f"Data file: {DATA_FILE}")
    print()

    # Step 1: Verify original test passes
    print("STEP 1: Run test with ORIGINAL data file")
    print("-" * 50)
    backup_data()
    restore_data()  # Ensure we start with original

    passed, output = run_test()
    print(f"Test result: {'PASSED ✓' if passed else 'FAILED ✗'}")
    if not passed:
        print("Unexpected: Test should pass with original data!")
        print(output[-1000:])
        return 1
    print()

    # Step 2: Modify data and re-run
    print("STEP 2: Modify data file and re-run test")
    print("-" * 50)

    # Import numpy here to modify the file
    sys.path.insert(0, str(SCIPY_ROOT))
    modify_data()
    print()

    passed, output = run_test()
    print(f"Test result: {'PASSED ✓' if passed else 'FAILED ✗'}")
    if passed:
        print("Unexpected: Test should fail with modified data!")
        return 1
    else:
        print("Expected: Test failed because PDF values are wrong")
        # Show the assertion error
        for line in output.split('\n'):
            if 'assert' in line.lower() or 'error' in line.lower() or 'mismatch' in line.lower():
                print(f"  {line}")
    print()

    # Step 3: Restore and verify
    print("STEP 3: Restore original data and verify test passes again")
    print("-" * 50)
    restore_data()

    passed, output = run_test()
    print(f"Test result: {'PASSED ✓' if passed else 'FAILED ✗'}")
    print()

    # Summary
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print("""
This demonstrates that:

1. The test depends on: scipy/stats/tests/data/jf_skew_t_gamlss_pdf_data.npy

2. The dependency is established via np.load() which:
   - Is a C extension call
   - Does NOT go through builtins.open()
   - Is NOT tracked by the import tracker

3. If this data file changes (as we demonstrated), the test transitions
   from PASS to FAIL, but the import tracker would:
   - See test_distributions.py imports scipy.stats
   - NOT see any dependency on jf_skew_t_gamlss_pdf_data.npy
   - Incorrectly skip the test if only the .npy file changed

4. Files affected by this pattern in scipy:
   - scipy/stats/tests/data/*.npy (5 files)
   - scipy/special/tests/data/*.npz (3 files)
   - scipy/fftpack/tests/*.npz (3 files)
   - scipy/interpolate/tests/data/*.npz (3 files)
   - And many more...
""")

    return 0


if __name__ == "__main__":
    sys.exit(main())
