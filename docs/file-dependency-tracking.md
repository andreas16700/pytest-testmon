# Dependency Tracking

## Summary

pytest-ezmon tracks dependencies at three levels using checkpoint deltas:

1. Global (before first test-file collection starts)
2. Test-file (new during collection of that file)
3. Test (new during execution of that test)

Two dependency families are tracked:
- module checkpoints (`sys.modules` keys)
- non-Python file reads (`open`/`io.open`)

## Tracker events

The tracker is driven by three events:

1. collect-file-start
- lazily sets global checkpoint once
- switches file collection context
- restores `sys.modules` to global checkpoint for subsequent files

2. test-start
- lazily sets file checkpoint once for that file
- restores `sys.modules` to `global + file` for subsequent tests

3. test-end
- computes test-unique module keys and read deps as deltas over `global + file`

No import-time dependency attribution is used.

## Stdlib handling

From each checkpoint delta, stdlib keys are identified first and moved into a persistent stdlib keep-set.
Stdlib keys remain in `sys.modules` and are always included in restore keep-sets.

## Restore algorithm

Restore uses set differences:
- `keep = checkpoint_keys ∪ stdlib_keep_keys`
- remove `current - keep` from `sys.modules`
- rehydrate `keep - current` from the real module cache

## Deferred module resolution

The tracker stores module checkpoints as raw module keys.
Module-key -> dependency resolution (local file / external package / file dep) is done only when payloads are assembled, using a cache.

## File reads

Non-Python reads are tracked as `TrackedFile(path, sha)` and checkpointed as:
- global reads
- per-file reads
- per-test unique reads (`test_reads - global_reads - file_reads[test_file]`)

## Output assembly

Per-test persisted dependencies are:
- global deps
- file deps for that test file
- test unique deps

where each component includes both resolved module dependencies and read dependencies.
