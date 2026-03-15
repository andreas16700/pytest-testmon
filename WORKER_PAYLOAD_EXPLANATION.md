For each test file, workers send a compressed payload with:
- common deps shared by all tests in that file
- unique deps only for tests that differ from the common set
- a compact test-name encoding via prefix map

Example (JSON-ish):

{
  "__format__": "file_common_unique_v2",
  "batches": [
    {
      "files": {
        "pandas/tests/api/test_api.py": {
          "com": {"f": ["pandas/core/api.py", "pandas/__init__.py", "pandas/io/formats/format.py"], "p": ["pandas.core.api", "pandas.io"], "e": ["numpy", "pytz"]},
          "pm": ["TestPDApi"],
          "t_names": ["1|test_api", "1|someother_test", "1|test_with_no_unique_deps", "1|another_test_with_no_unique_deps"],
          "dur": [0.12, 0.03, 0.01, 0.01],
          "fail": [1],
          "0": {"f": ["pandas/core/strings/accessor.py"], "p": ["pandas.core.strings"], "e": ["re"]},
          "1": {"f": ["pandas/core/reshape/merge.py"]},
          "etc": [2, 3]
        }
      }
    }
  ]
}

Key points:
- "com" holds dependencies common to all tests in the file.
- For tests with unique deps, the key is the **index** in `t_names`.
- "etc" is the list of test indices that have no unique deps.
- "dur" is a parallel list to `t_names` with per-test durations.
- "fail" lists indices of failed tests (durations come from `dur`).
- "pm" is the per-file prefix map for test names.

Dependency shorthands:
- "f": file dependencies (plain relative paths — shas are omitted, resolved on controller via file cache)
- "p": python module imports
- "e": external (non-tracked) dependencies

Omit empty kinds (no empty "f"/"p"/"e" lists).

Test name encoding:
- The full test nodeid is the test file + "::" + suffix.
- The suffix is split by "::".
- All parts except the last are encoded via the prefix map "pm".
- Encoding format is "prefix_id|last_part".
  - Prefix IDs are 1-based indexes into `pm` (pm[0] == id 1).
  - "1|test_api" means prefix id 1 (pm[0] == "TestPDApi") and last part "test_api".
  - If there is no prefix, the id is 0 and pm is empty.

Batching:
- Workers send batches of up to 5 test files per payload to the controller.
