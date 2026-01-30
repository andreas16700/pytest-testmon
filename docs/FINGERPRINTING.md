# Ezmon Fingerprinting System

This document explains how ezmon tracks test dependencies and determines which tests to run when code changes.

## Overview

Ezmon uses a **block-based fingerprinting system** to track dependencies at a granular level. Instead of just tracking which files a test depends on, it tracks which **code blocks** within those files are relevant to each test.

## Two Phases of Tracking

### 1. Collection Phase

When pytest collects test files, ezmon tracks:
- **Coverage**: Lines executed during module import (module-level code)
- **Imports**: Which modules are imported (via dependency tracker)
- **File reads**: Which non-Python files are read

This data becomes the **baseline** for ALL tests in that test file.

### 2. Execution Phase

When each test runs, ezmon tracks:
- **Coverage**: Additional lines executed during the test
- **Imports**: Any dynamic imports during test execution
- **File reads**: Any files read during test execution

This data is **merged** with the collection baseline to form the complete dependency data for each test.

## Code Blocks

For each Python module, ezmon identifies two types of code blocks:

### Module-Level Block
- Contains the **AST of the entire module** with function/method bodies **replaced** by placeholders
- Represents: imports, class definitions, function signatures, module-level code
- Checksum changes when: function signatures change, imports change, module-level code changes
- Checksum does NOT change when: only function body implementations change

### Function Body Blocks
- Each function/method body is a separate block
- Contains the **AST of the function body** (the code inside the function)
- Checksum changes when: the implementation inside the function changes

## Fingerprint Creation

A test's **fingerprint** for a module is the collection of checksums for blocks that contain covered lines:

1. Coverage reports which lines were executed
2. Each line maps to one or more blocks
3. Fingerprint = checksums of all blocks containing covered lines

### Example

```python
# src/math_utils.py
def add(a, b):      # line 1 - module-level (function definition)
    return a + b    # line 2 - add() body

def subtract(a, b): # line 4 - module-level (function definition)
    return a - b    # line 5 - subtract() body
```

**Blocks:**
- Module-level block (lines 1-5): Contains `FunctionDef('add', ..., transformed_into_block), FunctionDef('subtract', ..., transformed_into_block)`
- `add()` body block (line 2): Contains `Return(BinOp(...))`
- `subtract()` body block (line 5): Contains `Return(BinOp(...))`

**Scenario: test_add calls add()**

Coverage lines: {1, 2} (function definition + body)
- Line 1 → module-level block ✓
- Line 2 → add() body block ✓
- Fingerprint: [module_checksum, add_body_checksum]

**Scenario: test_subtract calls subtract()**

Coverage lines: {1, 4, 5} (imports module, calls subtract)
- Line 1 → module-level block ✓
- Line 4 → module-level block ✓
- Line 5 → subtract() body block ✓
- Fingerprint: [module_checksum, subtract_body_checksum]

## When Code Changes

### Function Body Changes

When we change `add()` from `return a + b` to `return a + b + 0`:
- Module-level block: **UNCHANGED** (function signature is the same)
- `add()` body block: **CHANGED** (different AST)
- `subtract()` body block: **UNCHANGED**

**Tests affected:**
- `test_add`: Fingerprint includes `add_body_checksum` → DOESN'T MATCH → SELECTED
- `test_subtract`: Fingerprint doesn't include `add_body_checksum` → MATCHES → NOT SELECTED

### Function Signature Changes

When we change `def add(a, b)` to `def add(a, b, c=0)`:
- Module-level block: **CHANGED** (function signature changed)
- `add()` body block: May or may not change

**Tests affected:**
- ALL tests that import this module are affected (fingerprints include module-level checksum)

### Module-Level Code Changes

When we add a new import or constant:
- Module-level block: **CHANGED**
- Function body blocks: **UNCHANGED**

**Tests affected:**
- ALL tests that import this module are affected

## Key Principle

> **A test is only affected by changes to code blocks it actually executes.**

If a test imports a module but doesn't call a specific function:
- It has coverage for module-level lines (function definitions)
- It does NOT have coverage for that function's body
- Its fingerprint includes the module-level block but NOT that function's body block
- When that function's body changes, the test is NOT affected

This is the correct behavior because:
1. The test doesn't execute that function
2. If it doesn't execute the function, changes to the function implementation cannot affect the test's outcome
3. The only way for the function to affect the test is if the module-level code changes (e.g., the function's interface changes)

## Batch Size and Coverage Contexts

Coverage.py has a limitation where each line is only attributed to the first context (test) that executes it within a coverage session. To ensure accurate per-test coverage:

- Ezmon uses `TEST_BATCH_SIZE = 1` to process coverage after each test
- Coverage is erased after each test, giving each test fresh coverage data
- This ensures that if multiple tests call the same function, they ALL get the dependency recorded

## Summary

| Change Type | Module-Level Block | Function Body Block | Tests Affected |
|-------------|-------------------|---------------------|----------------|
| Function body only | Unchanged | Changed | Only tests calling that function |
| Function signature | Changed | May change | All tests importing the module |
| New import/constant | Changed | Unchanged | All tests importing the module |
| Comment/docstring | Unchanged | Unchanged | No tests (AST unchanged) |
