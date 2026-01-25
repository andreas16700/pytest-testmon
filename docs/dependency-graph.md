# Dependency Graph Feature

This document describes the dependency graph feature in pytest-ezmon, which tracks and visualizes file-to-file import relationships discovered during test execution.

## Overview

The dependency graph captures actual runtime import relationships between files, providing a more accurate view of dependencies than static analysis. This data is collected during test execution and can be visualized in the ez-viz UI.

## How It Works

### Data Collection

During test execution, ezmon's `TestmonCollector` tracks which files import which other files:

1. When a test file runs, ezmon records which Python modules it imports
2. For each imported module, transitive imports are also recorded
3. External package dependencies (numpy, pytest, etc.) are tracked separately
4. All edges are deduplicated to avoid redundant data

**Edge Types:**
- `local`: File-to-file import (e.g., `test_foo.py` → `src/foo.py`)
- `external`: File-to-package import (e.g., `src/foo.py` → `numpy`)

### Storage

Graph data is stored in the `dependency_graph` table:

```sql
CREATE TABLE dependency_graph (
    id INTEGER PRIMARY KEY,
    source_file TEXT NOT NULL,     -- The file doing the importing
    target_file TEXT,               -- For local imports: the imported file
    target_package TEXT,            -- For external imports: the package name
    edge_type TEXT NOT NULL,        -- 'local' or 'external'
    run_uid INTEGER,                -- Links to run_uid table
    FOREIGN KEY(run_uid) REFERENCES run_uid(id) ON DELETE CASCADE
);
```

**Indexes:**
- `dg_source`: Fast lookup by source file
- `dg_target`: Fast lookup by target file
- `dg_run_uid`: Fast lookup by run
- `dg_unique_edge`: Prevents duplicate edges within a run

### When Edges Are Recorded

Edges are only collected for tests that **actually execute**. If ezmon deselects tests (because no relevant files changed), no new edges are recorded for those tests in that run.

**Example scenario:**
```
Run 1 (initial):     100 tests execute → edges for all 100 tests recorded
Run 2 (1 file change): 5 tests execute → edges for 5 tests recorded
Run 3 (no changes):    0 tests execute → 0 edges recorded
```

For a complete dependency graph, you need a run where all tests executed (like the initial run).

## Backward Compatibility

### Database Schema

The `dependency_graph` table uses `CREATE TABLE IF NOT EXISTS`, ensuring smooth upgrades:
- Existing databases get the new table automatically on first access
- No need to bump `DATA_VERSION` or trigger full test re-runs
- Old databases without the table fall back to legacy visualization

### API Compatibility

The `/api/data/{repo}/{job}/{run}/fileDependencies` endpoint:
1. First checks if `dependency_graph` table exists
2. If yes: returns data from the new table
3. If no: falls back to legacy "co-files" heuristic approach

### Plugin Version Compatibility

- **New plugin + New server**: Full dependency graph support
- **New plugin + Old server**: Plugin collects data, but server ignores it (graceful degradation)
- **Old plugin + New server**: Server uses legacy fallback for visualization
- **Old plugin + Old server**: Works as before

## NetDB Support

When using NetDB (network database) mode, dependency graph data is:
1. Collected locally by the plugin during test execution
2. Sent to the server via the `/api/rpc/dependency_graph/batch_insert` endpoint
3. Stored server-side in the same `dependency_graph` table structure

**Environment Variables for NetDB:**
```bash
TESTMON_NET_ENABLED=true
TESTMON_SERVER=https://your-server.com
TESTMON_AUTH_TOKEN=your-token
REPO_ID=owner/repo
JOB_ID=test-variant
```

## Visualization

The ez-viz UI displays the dependency graph using ReactFlow with:
- **Hierarchical layout**: Files arranged by dependency level
- **Color coding**: Test files (blue) vs source files (orange)
- **Search**: Filter files by name
- **Click-through**: Click a file to view its details
- **External deps**: Listed separately (not shown in graph)

### Data Format

The API returns data in this format:
```json
{
  "run_id": 123,
  "run_uid": 1,
  "files": [
    {
      "filename": "lib/mypackage/foo.py",
      "dependencies": ["lib/mypackage/bar.py", "lib/mypackage/utils.py"],
      "external_dependencies": ["numpy", "pytest"]
    }
  ]
}
```

## Storage Overhead

For a typical project, the dependency graph adds approximately 2-3% storage overhead:

| Project Size | Tests | Estimated Graph Size |
|-------------|-------|---------------------|
| Small       | 100   | ~10 KB              |
| Medium      | 1,000 | ~100 KB             |
| Large       | 10,000| ~1-2 MB             |

The unique index prevents duplicate edges, keeping storage efficient even after many runs.

## Data Retention

- Graph data is **preserved** across runs (run_uid entries are never deleted)
- Each run's graph is queryable by its `run_uid`
- If you need a "complete" graph, query the earliest/initial run or aggregate across runs
- The `ON DELETE CASCADE` foreign key ensures graph data is automatically cleaned up if a run is deleted

## Implementation Details

### Key Files

- `ezmon/db.py`: `dependency_graph` table schema and `insert_dependency_graph_edges()` method
- `ezmon/testmon_core.py`: Graph edge collection in `_merge_tracked_deps()` and `save_dependency_graph()`
- `ezmon/pytest_ezmon.py`: Graph saving in `pytest_sessionfinish` hook
- `ezmon/net_db.py`: NetDB RPC support for graph data
- `ez-viz/app.py`: Server endpoints for graph storage and retrieval
- `ez-viz/client/src/components/FilesDependencyGraphView.jsx`: React UI component

### Changes from Previous Version

The previous version used a static AST-based approach (`ezmon/graph.py`) that:
- Analyzed import statements statically
- Generated graphs without running tests
- Required separate upload step

The new approach:
- Collects **runtime** import relationships during actual test execution
- Automatically integrates with the existing data flow
- No separate commands needed
- More accurate (reflects actual runtime behavior, not just static imports)

## Troubleshooting

### No graph data appears
1. Ensure tests actually executed (check if ezmon deselected them)
2. For initial runs, all tests should execute
3. Check server logs for errors during graph insertion

### Incomplete graph
- Graph only contains edges for tests that ran
- Run with `--testmon-nocollect` disabled to force full collection
- Or modify a core file to trigger all dependent tests

### Large graph performance
- Use the search filter to focus on specific files
- The hierarchical layout handles up to ~500 nodes well
- For very large projects, consider filtering by subdirectory
