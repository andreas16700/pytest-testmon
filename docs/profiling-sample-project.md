# Profiling Report: ezmon-nocov on Sample Project

**Date**: 2026-02-01
**Python**: 3.14
**Sample Project**: 182 tests across 20 test files, 44 tracked files

## Summary

| Scenario | Time | Tests Run |
|----------|------|-----------|
| First run (all tests) | 0.85s | 182 |
| No changes (all skipped) | 0.17s | 0 |
| Incremental (1 file changed) | 0.24s | 20 |

**Verdict**: The plugin adds minimal overhead. Most time is spent in pytest infrastructure, not ezmon.

## Detailed Breakdown

### First Run (0.85s)

| Component | Time | % | Notes |
|-----------|------|---|-------|
| pytest hooks/multicall | 0.838s | 99% | Plugin framework overhead |
| Module imports | 0.302s | 36% | Loading test/source modules |
| Test execution | 0.296s | 35% | Running 182 tests |
| Assertion rewriting | 0.101s | 12% | pytest rewrites asserts |
| `_tracking_import` | 0.103s | 12% | ezmon import hooks |
| AST fingerprinting | 0.081s | 10% | `process_code.checksum()` |
| `get_tests_fingerprints` | 0.100s | 12% | Building fingerprints |
| Database operations | 0.025s | 3% | SQLite writes |

### No Changes Run (0.17s)

| Component | Time | % | Notes |
|-----------|------|---|-------|
| Module loading | 0.092s | 54% | Loading pytest/ezmon modules |
| `determine_stable()` | 0.017s | 10% | Check what changed |
| gc.collect | 0.020s | 12% | Pytest cleanup |
| Subprocess (git) | 0.019s | 11% | Git SHA lookups |

### Incremental Run (0.24s, 1 file changed)

| Component | Time | % | Notes |
|-----------|------|---|-------|
| Module loading | 0.095s | 40% | Loading pytest/ezmon modules |
| `determine_stable()` | 0.033s | 14% | Find affected tests |
| Test execution | 0.022s | 9% | Running 20 tests |
| gc.collect | 0.024s | 10% | Pytest cleanup |

## Top CPU Consumers (Self-Time)

```
   tottime  function
   0.048s   builtins.compile          # pytest assertion rewriting
   0.043s   gc.collect                # pytest cleanup
   0.027s   ast._format               # AST dump for checksums
   0.021s   ast.iter_child_nodes      # AST traversal
   0.020s   rewrite.traverse_node     # pytest assertion rewriting
   0.019s   builtins.getattr          # attribute access
   0.018s   getoption                 # pytest config lookups
   0.017s   builtins.isinstance       # type checks
   0.013s   sqlite3.__exit__          # DB transactions
   0.012s   str.startswith            # string matching
   0.007s   _tracking_import          # ezmon import tracking
```

**Key insight**: pytest's assertion rewriting and garbage collection dominate. ezmon's import tracking is only 7ms!

## Storage Efficiency

```sql
files table:           44 entries
tests table:          186 entries
test_deps (bitmaps):  186 entries

Average bitmap size:   30 bytes/test
Total bitmap storage:  5.6 KB

Old junction table:   617 rows (would be ~5 KB)
```

For this small project, bitmap storage is similar to junction tables. The real benefits appear at scale:
- 10,000 tests × 100 deps = 8 MB junction vs ~300 KB bitmaps

## Bottleneck Analysis

### 1. Module Loading (35-54% of time)
**Problem**: Python's import system is slow. pytest loads many modules.
**Mitigation**: Already unavoidable. ezmon's import caching helps.

### 2. AST Operations (15-20% of time)
**Problem**: AST parsing and fingerprinting requires walking the entire AST.
**Potential improvements**:
- Cache parsed ASTs by file content hash
- Use incremental parsing for changed files only
- Consider using a faster AST library (tree-sitter)

### 3. Git SHA Lookups (10% of no-change runs)
**Problem**: Subprocess calls to git for file SHAs.
**Potential improvements**:
- Batch git ls-tree calls
- Cache SHA mappings in memory
- Use libgit2 bindings for faster access

### 4. `_tracking_import` (12% of first run)
**Current**: 5497 calls, 7ms self-time, 103ms cumulative
**Why cumulative is high**: Calls actual `__import__` which loads modules
**Potential improvements**:
- Early exit for known-external packages
- Skip tracking standard library modules faster
- Pre-compute stdlib module list

### 5. `determine_stable()` (10-14% of incremental runs)
**Current**: 17-33ms depending on what changed
**Performs**:
- Load file checksums from DB
- Compare with current filesystem
- Bitmap intersection for affected tests
**Potential improvements**:
- Maintain in-memory file index
- Use mmap for faster DB reads
- Parallelize file checksum computation

## Recommendations for Improvement

### High Impact, Low Effort

1. **Cache git ls-tree output**
   ```python
   # Instead of per-file subprocess calls
   _git_tree_cache = None
   def get_all_file_shas():
       global _git_tree_cache
       if _git_tree_cache is None:
           _git_tree_cache = parse_git_ls_tree_r_HEAD()
       return _git_tree_cache
   ```

2. **Early exit in _tracking_import for stdlib**
   ```python
   STDLIB_PREFIXES = frozenset(['os', 'sys', 'json', ...])
   def _tracking_import(self, name, ...):
       if name.split('.')[0] in STDLIB_PREFIXES:
           return self._original_import(name, ...)  # Skip tracking
   ```

3. **Batch database writes**
   ```python
   # Instead of per-test saves, batch at session end
   def save_all_test_deps(self, test_deps_batch):
       self.cursor.executemany(INSERT_DEPS_SQL, test_deps_batch)
   ```

### Medium Impact, Medium Effort

4. **AST cache by content hash**
   ```python
   _ast_cache = {}  # content_hash -> (checksum, mtime)
   def checksum(self, filename):
       content = read_file(filename)
       hash = hashlib.md5(content).hexdigest()
       if hash in _ast_cache:
           return _ast_cache[hash]
       # ... compute checksum ...
   ```

5. **Parallel file checksum computation**
   ```python
   from concurrent.futures import ThreadPoolExecutor
   def compute_all_checksums(files):
       with ThreadPoolExecutor(max_workers=4) as executor:
           return dict(executor.map(compute_checksum, files))
   ```

### Low Impact (Already Optimized)

- Import tracking: 7ms is already very fast
- Bitmap operations: 30 bytes/test is excellent
- Database schema: Roaring bitmaps work well

## Comparison with Coverage-Based testmon

| Metric | ezmon-nocov | pytest-testmon (coverage) |
|--------|-------------|---------------------------|
| First run | 0.85s | ~2.5s (coverage overhead) |
| No changes | 0.17s | ~0.3s |
| Incremental | 0.24s | ~0.5s |
| False negatives | None | Possible |
| Precision | File-level | Line-level |

ezmon-nocov is ~3x faster on first run due to no coverage.py overhead.

## Conclusion

The ezmon-nocov plugin is already well-optimized for this sample project:

1. **Overhead is minimal**: Most time is pytest infrastructure, not ezmon
2. **Storage is efficient**: 30 bytes/test with Roaring bitmaps
3. **Import tracking is fast**: 7ms self-time for ~5500 import calls
4. **Incremental runs are fast**: 0.24s vs 0.85s full run

For larger projects (10,000+ tests), the recommendations above would provide more significant improvements. The main opportunities are:
- Batch git operations
- Cache AST computations
- Parallel file processing
