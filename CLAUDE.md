# CLAUDE.md — pytest-testmon (ezmon fork)

## Project Overview

**pytest-testmon** (internal name: `ezmon`) is a pytest plugin that selects and runs only the tests affected by recent code changes. It replaces coverage-based tracking with a faster import-hook approach, combined with AST fingerprinting and roaring bitmap dependency storage.

Version: `3.0.0+refactor` — a fork of upstream pytest-testmon with a fundamentally different tracking engine.

---

## Repository Layout

```
pytest-testmon/
├── ezmon/                  # Core plugin (Python package)
├── ez-viz/                 # Visualization server (Flask + React)
│   ├── app.py              # Flask backend
│   └── client/             # React frontend (Vite)
├── tests/                  # Unit tests
├── integration_tests/      # End-to-end scenario tests
├── testmon_data/           # SQLite databases (per-job)
├── demos/                  # Example projects
├── docs/                   # Documentation
├── ARCHITECTURE.md         # Detailed architecture reference
└── pyproject.toml          # Build config + dependencies
```

---

## Core Plugin — `ezmon/`

### Module Responsibilities

| Module | Responsibility |
|--------|----------------|
| `pytest_ezmon.py` | Pytest plugin entry point: registers hooks, drives test selection/deselection, coordinates xdist workers |
| `testmon_core.py` | Orchestrates fingerprinting, change detection, stability analysis, and test selection logic |
| `dependency_tracker.py` | Installs import hooks (`builtins.__import__`), records per-test file dependencies, manages scope transitions (global → file → test) |
| `file_cache.py` | Git-aware file metadata cache; batches git SHA lookups and checksum computation |
| `db.py` | SQLite schema (v20), all read/write operations for files, tests, runs, and bitmap blobs |
| `bitmap_deps.py` | Roaring bitmap serialization/deserialization; `find_affected_tests()` via bitmap intersection |
| `process_code.py` | AST parsing, docstring stripping, CRC32 checksum computation per file |
| `configure.py` | Decision logic: whether to collect, select, or run all tests based on config/environment |
| `common.py` | Shared types, logging setup, package detection utilities |
| `net_db.py` | HTTP client for uploading/fetching run data from the visualization server |
| `server_sync.py` | Sync utilities for server-side storage |
| `dep_store.py` | Dependency store abstraction over db.py |
| `trie.py` | Trie data structure used for module path matching |
| `impact.py` | Impact analysis helpers |
| `query.py` | Read-only database query helpers |
| `tox_testmon.py` | Tox plugin integration |

---

## How It Works — Data Flow

### Test Run (collection + selection)

```
pytest invocation
  └─ pytest_configure()
       └─ loads SQLite DB, reads last commit, decides mode (select / collect / both)

  └─ pytest_collection_modifyitems()
       └─ [select mode] deselects tests whose dependencies haven't changed

  └─ per test: pytest_runtest_setup → pytest_runtest_teardown
       └─ DependencyTracker records (module_name, fromlist) for every import

  └─ pytest_sessionfinish()
       ├─ reconcile raw import hooks → file paths
       ├─ checkpoint deltas → per-test unique deps
       ├─ FileInfoCache.batch_get_checksums() for changed files
       ├─ compare AST checksums vs stored → identify truly modified files
       ├─ save FileRecord, TestDeps (bitmap), run stats to DB
       └─ optionally upload to server (NetDB mode)
```

### Test Selection Logic

```
1. Load DB → last_run_commit_id
2. git diff HEAD ↔ last_run_commit → changed file paths
3. Recompute AST checksum for each changed .py file
4. Compare with stored checksum → git_affected file IDs
5. Deserialize all TestDeps bitmaps from DB
6. Bitmap intersection: files_affected ∩ test_dep_bitmaps → affected test IDs
7. Run only those tests
```

---

## Key Architectural Decisions

### Import-Hook Tracking (not coverage.py)
- Hooks `builtins.__import__` to record every import during test execution.
- ~3x faster than coverage-based tracking; no `.coverage` files.
- Captures transitive imports automatically.

### AST Fingerprinting
- Strips docstrings, serializes AST, computes CRC32.
- Result: comments and docstrings do NOT trigger re-runs.
- Only structural/logic changes cause affected tests to be re-selected.

### Roaring Bitmap Dependency Storage
- Each test's file dependencies stored as a compressed roaring bitmap blob.
- `pyroaring` library; zstd compression (gzip fallback).
- `find_affected_tests(file_ids)` = single bitmap OR + intersection operation.
- Space: ~4× smaller than row-per-dependency storage.

### Checkpoint Scope Model
```
idle → global (after conftest) → file (after module collect) → test → file → test → ...

global_deps  = imports before any test file loads
file_deps    = imports during test file collection
test_deps    = imports during test body execution

stored_deps  = global_deps ∪ file_deps ∪ test_deps
```

### Git-Aware File Cache
- Batches `git ls-tree` and `git diff` calls for efficiency.
- Caches: git SHAs (fsha), normalized paths, tracked-file status.
- Only recomputes checksums for files that git reports as changed.

### Parallel Execution (xdist)
- Controller computes selection once, broadcasts to workers.
- Workers track deps locally, write to WAL-isolated DB snapshots.
- Wire format: compact JSON with prefix encoding.

---

## Database Schema (v20)

Stored in `.testmondata` (SQLite) per-job directory under `testmon_data/`.

| Table | Columns | Purpose |
|-------|---------|---------|
| `files` | id, path, checksum, fsha, file_type | Unified file registry |
| `tests` | id, name, duration, failed | Test records |
| `test_deps` | test_id, file_bitmap, external_packages | Roaring bitmap + package deps |
| `runs` | id, commit_id, packages, python_version, … | Run metadata |

Legacy tables (`file_fp`, `test_execution`, `test_execution_file_fp`) kept for compatibility.

---

## Visualization Layer — `ez-viz/`

### Flask Backend (`app.py`)
- Serves REST API for repos, jobs, runs, tests, files, summaries.
- GitHub OAuth authentication and multi-tenancy.
- Proxies GitHub API for workflow file access.
- Optional OpenAI integration for workflow optimization.
- Reads `testmon_data/metadata.json` to discover all available jobs/runs.

### React Frontend (`ez-viz/client/src/`)

| Component | Purpose |
|-----------|---------|
| `App.jsx` | Root state, repo/job/run selection, data loading |
| `Header.jsx` | Navigation, user profile, login |
| `SelectorBar.jsx` | Repo/job/run dropdowns and filter controls |
| `SummaryTab.jsx` | Dashboard: run stats, pass rates, affected test/file counts |
| `TestsTab.jsx` | Test list with search and filter |
| `FilesTab.jsx` | File list with metadata |
| `TestDetails.jsx` | Per-test details: deps, history |
| `FileDetails.jsx` | Per-file details: affected tests, change history |
| `TestManagementTab.jsx` | Manual test selection/deselection UI |
| `FilesDependencyGraphView.jsx` | Interactive dependency graph (networkx/pyvis data) |
| `WorkflowFilePopup.jsx` | GitHub workflow file viewer/editor |

Build: Vite + React. Dev server proxies API to Flask.

---

## Tests

### Unit Tests (`tests/`)

| File | What it tests |
|------|---------------|
| `test_db.py` | SQLite schema, read/write operations |
| `test_dep_store.py` | Dependency store logic |
| `test_file_cache_checksum.py` | File cache, git SHA batching, checksum computation |
| `test_import_hook_approach.py` | Import hook recording and reconciliation |
| `test_process_code.py` | AST parsing and CRC32 fingerprinting |

### Integration Tests (`integration_tests/`)
- 24 named scenarios under `scenarios/` — each defines a code change and which tests should be affected.
- Runs against `sample_project/` which contains math utils, calculators, decorators, generators, nested classes, config files, and transitive import chains.
- `run_integration_tests.py` drives all scenarios end-to-end.
- `test_all_versions.py` validates across Python versions.

---

## Key Dependencies

| Package | Purpose |
|---------|---------|
| `pytest>=5,<10` | Core test framework |
| `pyroaring>=0.4.0` | Roaring bitmaps |
| `zstandard>=0.18.0` | zstd compression |
| `requests>=2.20` | HTTP client (NetDB mode) |
| `networkx`, `pyvis` | Dependency graph (optional, viz layer) |

---

## Development Notes

- Entry point registered in `pyproject.toml` as `pytest11 = { ezmon = "ezmon.pytest_ezmon" }`.
- Plugin auto-activates when installed; `--no-testmon` disables it.
- `ARCHITECTURE.md` contains deeper implementation notes (550+ lines).
- `testmon_data/metadata.json` is the central registry for the viz server — edit with care.
- Database schema migrations live in `db.py`; bump version constant when changing schema.
