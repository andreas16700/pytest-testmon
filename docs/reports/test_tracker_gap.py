#!/usr/bin/env python3
"""
Test that our DependencyTracker misses np.load() file dependencies.

This script uses our actual DependencyTracker to show the gap.
"""

import sys
import tempfile
from pathlib import Path

# Add ezmon to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from ezmon.dependency_tracker import DependencyTracker
import numpy as np


def main():
    print("=" * 70)
    print("TESTING: DependencyTracker vs np.load()")
    print("=" * 70)
    print()

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)

        # Create a test data file
        data_file = tmpdir / "test_data.npy"
        np.save(data_file, np.array([1, 2, 3]))

        # Create a regular text file for comparison
        text_file = tmpdir / "test_data.txt"
        text_file.write_text("hello world")

        # Initialize tracker
        tracker = DependencyTracker(str(tmpdir))

        print("Starting dependency tracking...")
        tracker.start("test_context", test_file="test.py")

        # Test 1: Regular file open (should be tracked)
        print("\n1. Reading file via builtins.open():")
        with open(text_file, 'r') as f:
            content = f.read()
        print(f"   Read: {content}")

        # Test 2: np.load (will NOT be tracked)
        print("\n2. Reading file via np.load():")
        data = np.load(data_file)
        print(f"   Loaded: {data}")

        # Stop tracking and get results
        tracked_files, local_imports, external_imports, test_file = tracker.stop()

        print("\n" + "=" * 70)
        print("TRACKING RESULTS")
        print("=" * 70)

        print(f"\nTracked files (via builtins.open):")
        if tracked_files:
            for tf in tracked_files:
                print(f"  ✓ {tf.path}")
        else:
            print("  (none)")

        print(f"\nLocal imports:")
        if local_imports:
            for imp in local_imports:
                print(f"  ✓ {imp}")
        else:
            print("  (none)")

        print(f"\nExternal imports:")
        if external_imports:
            for imp in external_imports:
                print(f"  ✓ {imp}")
        else:
            print("  (none)")

        print("\n" + "=" * 70)
        print("ANALYSIS")
        print("=" * 70)

        # Check what was tracked
        tracked_paths = {tf.path for tf in tracked_files}
        text_file_rel = text_file.name
        data_file_rel = data_file.name

        text_tracked = any(text_file_rel in p for p in tracked_paths)
        data_tracked = any(data_file_rel in p for p in tracked_paths)

        print(f"""
File access via builtins.open():
  test_data.txt: {'✓ TRACKED' if text_tracked else '✗ NOT TRACKED'}

File access via np.load():
  test_data.npy: {'✓ TRACKED' if data_tracked else '✗ NOT TRACKED (EXPECTED GAP!)'}

Conclusion:
  The DependencyTracker correctly hooks builtins.open() but CANNOT
  track np.load() because numpy uses C-level file I/O internally.

  This means scipy tests that use np.load() to read reference data
  have UNTRACKED dependencies on those .npy/.npz files!
""")

        tracker.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
