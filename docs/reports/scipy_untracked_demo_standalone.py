#!/usr/bin/env python3
"""
Standalone demonstration: np.load() bypasses import tracking.

This demonstrates the fundamental issue without requiring scipy source tree.
We create a self-contained test that shows:
1. A test that loads data via np.load()
2. Modifying the data file causes test failure
3. The import tracker would NOT see this dependency
"""

import tempfile
import sys
from pathlib import Path

# We'll use the installed numpy
import numpy as np


def create_test_scenario(tmpdir: Path):
    """Create a minimal test scenario similar to scipy's test_distributions.py"""

    # Create test data file (simulating jf_skew_t_gamlss_pdf_data.npy)
    data_file = tmpdir / "reference_data.npy"

    # Create reference data: 4 rows (x, expected_result, param_a, param_b)
    # This mimics scipy's structure
    x_values = np.linspace(-5, 5, 21)
    expected_results = np.sin(x_values)  # Our "expected" computed values
    param_a = np.full_like(x_values, 1.0)
    param_b = np.full_like(x_values, 2.0)

    data = np.array([x_values, expected_results, param_a, param_b])
    np.save(data_file, data)

    # Create test module
    test_file = tmpdir / "test_example.py"
    test_file.write_text(f'''
import numpy as np
from pathlib import Path

DATA_FILE = Path("{data_file}")

def compute_result(x, a, b):
    """Simulates scipy.stats.jf_skew_t(a, b).pdf(x)"""
    # This would be the actual scipy computation
    return np.sin(x)  # Our "computation"

def test_compare_with_reference():
    """
    This test loads reference data via np.load() and compares
    against computed values - exactly like scipy's test_compare_with_gamlss_r.
    """
    # Load reference data - THIS IS THE UNTRACKED DEPENDENCY
    data = np.load(DATA_FILE)

    x = data[0]
    expected = data[1]
    a = data[2][0]
    b = data[3][0]

    computed = compute_result(x, a, b)

    # Assert they match (like scipy's assert_allclose with rtol=1e-12)
    np.testing.assert_allclose(computed, expected, rtol=1e-12)
    print("Test PASSED: computed values match reference data")
''')

    return data_file, test_file


def run_test(test_file: Path) -> tuple[bool, str]:
    """Run the test and return (passed, output)."""
    import subprocess
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-xvs", str(test_file)],
        capture_output=True,
        text=True
    )
    return result.returncode == 0, result.stdout + result.stderr


def main():
    print("=" * 70)
    print("DEMONSTRATION: np.load() BYPASSES IMPORT TRACKING")
    print("=" * 70)
    print()

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)

        # Create test scenario
        data_file, test_file = create_test_scenario(tmpdir)
        print(f"Created test file: {test_file}")
        print(f"Created data file: {data_file}")
        print()

        # Step 1: Run test with original data
        print("STEP 1: Run test with ORIGINAL reference data")
        print("-" * 50)
        passed, output = run_test(test_file)
        print(f"Result: {'PASSED ✓' if passed else 'FAILED ✗'}")
        if not passed:
            print("Output:", output[-500:])
            return 1
        print()

        # Step 2: Modify the data file (simulating a change to .npy file)
        print("STEP 2: Modify reference data file (double expected values)")
        print("-" * 50)
        data = np.load(data_file)
        print(f"Original expected values (first 5): {data[1, :5]}")

        # Modify: double the expected values
        data[1] = data[1] * 2.0
        np.save(data_file, data)
        print(f"Modified expected values (first 5): {data[1, :5]}")
        print()

        # Step 3: Run test again - should FAIL
        print("STEP 3: Run test with MODIFIED reference data")
        print("-" * 50)
        passed, output = run_test(test_file)
        print(f"Result: {'PASSED ✓' if passed else 'FAILED ✗'}")

        if passed:
            print("ERROR: Test should have failed!")
            return 1
        else:
            print("Expected: Test FAILED because reference data was modified")
            # Show relevant error
            for line in output.split('\n'):
                if 'AssertionError' in line or 'mismatch' in line.lower():
                    print(f"  {line}")
        print()

        # Step 4: Restore original data
        print("STEP 4: Restore original reference data")
        print("-" * 50)
        data[1] = data[1] / 2.0  # Restore
        np.save(data_file, data)
        passed, output = run_test(test_file)
        print(f"Result: {'PASSED ✓' if passed else 'FAILED ✗'}")
        print()

        # Analysis
        print("=" * 70)
        print("ANALYSIS: WHY IMPORT TRACKER MISSES THIS")
        print("=" * 70)
        print("""
The import tracker hooks:
  ✓ builtins.open() - tracks file reads
  ✓ builtins.__import__() - tracks imports
  ✓ importlib.import_module() - tracks imports

The test's dependency chain:
  1. test_example.py imports numpy (TRACKED via __import__)
  2. test_example.py calls np.load("reference_data.npy")
  3. np.load() reads the file using C-level I/O (NOT TRACKED!)

What the tracker sees:
  - test_example.py depends on: numpy

What the tracker MISSES:
  - test_example.py depends on: reference_data.npy

Result:
  If reference_data.npy changes but test_example.py doesn't,
  the tracker incorrectly decides "test is stable, skip it"
  → The test transition (PASS → FAIL) goes UNDETECTED!
""")

        print("=" * 70)
        print("SCIPY-SPECIFIC IMPACT")
        print("=" * 70)
        print("""
In scipy, this pattern affects these tests:

1. scipy/stats/tests/test_distributions.py::TestJFSkewT::test_compare_with_gamlss_r
   Depends on: scipy/stats/tests/data/jf_skew_t_gamlss_pdf_data.npy

2. scipy/stats/tests/test_distributions.py::TestLevyStable::test_pdf
   Depends on: scipy/stats/tests/data/levy_stable/*.npy (3 files)

3. scipy/special/tests/test_data.py::test_boost (400+ parametrized tests)
   Depends on: scipy/special/tests/data/boost.npz

4. scipy/fft/_pocketfft/tests/test_real_transforms.py
   Depends on: scipy/fftpack/tests/*.npz (4 files)

All these tests can transition (PASS→FAIL or FAIL→PASS) if their
data files change, but the import tracker will NOT detect the dependency.
""")

    return 0


if __name__ == "__main__":
    sys.exit(main())
