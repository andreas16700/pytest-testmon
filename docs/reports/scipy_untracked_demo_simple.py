#!/usr/bin/env python3
"""
Simple demonstration: np.load() bypasses import tracking.

This demonstrates the core issue without any test framework dependencies.
"""

import tempfile
import sys
from pathlib import Path
import numpy as np


def main():
    print("=" * 70)
    print("DEMONSTRATION: np.load() BYPASSES IMPORT TRACKING")
    print("=" * 70)
    print()

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        data_file = tmpdir / "reference_data.npy"

        # Create reference data (like scipy's jf_skew_t_gamlss_pdf_data.npy)
        x_values = np.linspace(-5, 5, 21)
        expected_results = np.sin(x_values)
        original_data = np.array([x_values, expected_results])
        np.save(data_file, original_data)

        print(f"Created data file: {data_file}")
        print(f"Original expected values (first 5): {original_data[1, :5]}")
        print()

        # Simulate test function
        def run_test():
            """Simulates scipy's test_compare_with_gamlss_r"""
            data = np.load(data_file)  # <-- THIS IS THE UNTRACKED DEPENDENCY
            x = data[0]
            expected = data[1]
            computed = np.sin(x)  # Simulates stats.jf_skew_t(a,b).pdf(x)

            try:
                np.testing.assert_allclose(computed, expected, rtol=1e-12)
                return True, "PASSED"
            except AssertionError as e:
                return False, f"FAILED: {str(e)[:100]}"

        # Step 1: Run with original data
        print("STEP 1: Run test with ORIGINAL reference data")
        print("-" * 50)
        passed, msg = run_test()
        print(f"Result: {msg}")
        assert passed, "Test should pass with original data!"
        print()

        # Step 2: Modify data file
        print("STEP 2: Modify reference data file")
        print("-" * 50)
        modified_data = np.load(data_file)
        modified_data[1] = modified_data[1] * 2.0  # Double the expected values
        np.save(data_file, modified_data)
        print(f"Modified expected values (first 5): {modified_data[1, :5]}")
        print()

        # Step 3: Run test again - should FAIL
        print("STEP 3: Run test with MODIFIED reference data")
        print("-" * 50)
        passed, msg = run_test()
        print(f"Result: {msg}")
        assert not passed, "Test should fail with modified data!"
        print()

        # Step 4: Restore
        print("STEP 4: Restore and verify")
        print("-" * 50)
        np.save(data_file, original_data)
        passed, msg = run_test()
        print(f"Result: {msg}")
        print()

        # Analysis
        print("=" * 70)
        print("KEY INSIGHT: WHAT THE IMPORT TRACKER SEES vs MISSES")
        print("=" * 70)
        print("""
When tracking this test file:

TRACKED (via builtins.__import__):
  └── import numpy

NOT TRACKED (np.load uses C-level I/O):
  └── np.load("reference_data.npy")

Result:
  - Tracker records: test depends on numpy
  - Tracker MISSES: test depends on reference_data.npy
  - If .npy file changes → test transitions → UNDETECTED!
""")

        print("=" * 70)
        print("SCIPY FILES AFFECTED BY THIS PATTERN")
        print("=" * 70)
        print("""
The following scipy test files have untracked .npy/.npz dependencies:

scipy/stats/tests/test_distributions.py:
  → data/jf_skew_t_gamlss_pdf_data.npy
  → data/levy_stable/stable-Z1-pdf-sample-data.npy
  → data/levy_stable/stable-Z1-cdf-sample-data.npy
  → data/levy_stable/stable-loc-scale-sample-data.npy
  → data/rel_breitwigner_pdf_sample_data_ROOT.npy

scipy/special/tests/test_data.py:
  → data/boost.npz (400+ parametrized tests!)
  → data/gsl.npz
  → data/local.npz

scipy/fft/_pocketfft/tests/test_real_transforms.py:
  → ../fftpack/tests/test.npz
  → ../fftpack/tests/fftw_double_ref.npz
  → ../fftpack/tests/fftw_single_ref.npz

scipy/interpolate/tests/test_fitpack.py:
  → data/bug-1310.npz

scipy/interpolate/tests/test_bsplines.py:
  → data/gcvspl.npz

scipy/interpolate/tests/test_interpnd.py:
  → data/estimate_gradients_hang.npy

scipy/linalg/tests/test_solvers.py:
  → data/carex_15_data.npz

scipy/spatial/tests/test_qhull.py:
  → data/random-*.npy (loaded via np.load)

TOTAL: 15+ data files affecting 500+ test cases
""")

    print("Demonstration complete!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
