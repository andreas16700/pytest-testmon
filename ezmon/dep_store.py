"""
Unified in-memory cache for controller batch processing.

Pre-loads files, tests, and test_deps tables at session start.
All lookups are dict access. New entries INSERT immediately.
Dirty metadata flushes in batch.

Versioning capture (opt-in via EZMON_VERSIONING=1): every mutation
method optionally queues a history row into an in-memory buffer keyed
by (table, primary_key). At flush time the buffer is written to the
files_history / tests_failed_history / test_deps_history tables in
the same transaction as the current-state writes. When versioning is
off, the buffer stays empty and every emission guard short-circuits
immediately — no runtime cost on the hot path.
"""

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from ezmon.common import get_logger

logger = get_logger(__name__)


@dataclass
class FileEntry:
    id: int
    checksum: Optional[int]
    fsha: Optional[str]
    file_type: str
    run_id: Optional[int]
    # Session baseline, captured in _preload. Used by the versioning
    # capture to decide whether the current values differ from what
    # the DB contained when this session started.
    _base_checksum: Optional[int] = None
    _base_fsha: Optional[str] = None


@dataclass
class TestEntry:
    id: int
    test_file: Optional[str]
    duration: Optional[float]
    failed: bool
    run_id: Optional[int]
    forced: Optional[int] = None
    dirty: bool = False
    # Session baseline for versioning capture. ``None`` on entries
    # created during this session (no previous state) so the first
    # observed ``failed`` value always produces a history row.
    _base_failed: Optional[bool] = None


class DepStore:
    """Unified in-memory cache backed by a local SQLite DB.

    Pre-loads files, tests, and test_deps tables into memory at session start.
    All lookups are dict access. New entries INSERT immediately.
    Dirty test metadata flushes in batch via save_batch().
    """

    def __init__(
        self,
        db,
        run_id: Optional[int] = None,
        versioning_enabled: Optional[bool] = None,
    ):
        self._db = db
        self._files: Dict[str, FileEntry] = {}  # path -> FileEntry
        self._tests: Dict[str, TestEntry] = {}  # name -> TestEntry
        # Reverse index so save_batch can look up test entries by id
        # when building test_deps_history rows.
        self._test_ids_to_names: Dict[int, str] = {}
        self._blobs: Dict[int, bytes] = {}       # test_id -> file_bitmap blob
        self._packages: Dict[int, str] = {}      # test_id -> external_packages (normalized "")
        # Session baselines for test_deps. Populated once in _preload
        # and never mutated after. Used by the versioning capture to
        # detect "reverted to baseline" within a session and drop
        # already-queued history rows.
        self._base_blobs: Dict[int, bytes] = {}
        self._base_packages: Dict[int, str] = {}

        # Versioning state. Disabled by default unless EZMON_VERSIONING
        # is set or the caller passes versioning_enabled=True.
        if versioning_enabled is None:
            versioning_enabled = os.environ.get("EZMON_VERSIONING", "") == "1"
        self._versioning_enabled: bool = versioning_enabled
        self._run_id: Optional[int] = run_id
        # Buffer keyed by (table_name, primary_key_tuple). Later writes
        # to the same key overwrite earlier ones in the same session so
        # rerun/retry scenarios produce exactly one history row per
        # (entity, run). Flushed by save_batch inside the outer txn.
        self._history_ops: Dict[Tuple[str, tuple], tuple] = {}

        self._preload()

    def set_run_id(self, run_id: int) -> None:
        """Set the active run id for history captures.

        Normally passed to __init__, but testmon_core can also set it
        late if DepStore was constructed before create_run(). History
        emission is a no-op until this is set (or passed to __init__).
        """
        self._run_id = run_id

    def _preload(self):
        """Load all files, tests, and test_deps into memory (3 queries)."""
        con = self._db.con

        for row in con.execute(
            "SELECT id, path, checksum, fsha, file_type, run_id FROM files"
        ):
            self._files[row["path"]] = FileEntry(
                id=row["id"],
                checksum=row["checksum"],
                fsha=row["fsha"],
                file_type=row["file_type"],
                run_id=row["run_id"],
                _base_checksum=row["checksum"],
                _base_fsha=row["fsha"],
            )

        for row in con.execute(
            "SELECT id, name, test_file, duration, failed, forced, run_id FROM tests"
        ):
            entry = TestEntry(
                id=row["id"],
                test_file=row["test_file"],
                duration=row["duration"],
                failed=bool(row["failed"]),
                forced=row["forced"],
                run_id=row["run_id"],
                _base_failed=bool(row["failed"]),
            )
            self._tests[row["name"]] = entry
            self._test_ids_to_names[row["id"]] = row["name"]

        for row in con.execute(
            "SELECT test_id, file_bitmap, external_packages FROM test_deps"
        ):
            test_id = row["test_id"]
            blob = bytes(row["file_bitmap"])
            pkgs = row["external_packages"] or ""
            self._blobs[test_id] = blob
            # Normalize NULL -> "" so skip-unchanged comparisons don't
            # treat "no packages cached" and "empty packages list"
            # differently.
            self._packages[test_id] = pkgs
            # Baseline snapshot for versioning revert detection.
            self._base_blobs[test_id] = blob
            self._base_packages[test_id] = pkgs

        logger.info(
            "DepStore pre-loaded: %d files, %d tests, %d blobs (versioning=%s)",
            len(self._files),
            len(self._tests),
            len(self._blobs),
            "on" if self._versioning_enabled else "off",
        )

    # ---- Versioning capture (all no-ops when disabled) ----

    def _queue_history(self, table: str, pk: tuple, values: tuple) -> None:
        """Queue a history row. Later writes to the same PK overwrite."""
        self._history_ops[(table, pk)] = values

    def _maybe_emit_file_history(self, path: str) -> None:
        """Queue a files_history row if this file's content changed.

        Skips emission when:
        - versioning is disabled or no run_id is set
        - the cache entry for ``path`` does not exist
        - both checksum and fsha are None (guardrail 2: don't emit
          before the content is actually known — NULL values in
          files_history are reserved for git-deletion tombstones)

        If current values now match the session baseline (the caller
        just reverted an earlier change within this session), any
        previously-queued row for the same (entity, run) is dropped so
        the final flush reflects the net effect of the session.
        """
        if not self._versioning_enabled or self._run_id is None:
            return
        entry = self._files.get(path)
        if entry is None:
            return
        if entry.checksum is None and entry.fsha is None:
            return
        key = ("files_history", (entry.id, self._run_id))
        if (entry.checksum == entry._base_checksum
                and entry.fsha == entry._base_fsha):
            # Back to baseline — drop any already-queued emit.
            self._history_ops.pop(key, None)
            return
        self._history_ops[key] = (
            entry.id,
            self._run_id,
            path,
            entry.file_type,
            entry.checksum,
            entry.fsha,
        )

    def emit_file_tombstone(self, path: str) -> None:
        """Queue a NULL-valued files_history row to mark a deletion.

        Called from testmon_core when a git diff reports the file is
        deleted from the working tree. NULL checksum/fsha is the only
        way deletion markers get into files_history — the regular
        emit path skips NULL values to avoid noisy tombstone-lookalike
        rows during first-insert.
        """
        if not self._versioning_enabled or self._run_id is None:
            return
        entry = self._files.get(path)
        if entry is None:
            return
        self._queue_history(
            "files_history",
            pk=(entry.id, self._run_id),
            values=(
                entry.id,
                self._run_id,
                path,
                entry.file_type,
                None,  # checksum
                None,  # fsha
            ),
        )

    def emit_test_tombstone(self, name: str) -> None:
        """Queue a tombstone row (-1) to mark a test as deleted."""
        if not self._versioning_enabled or self._run_id is None:
            return
        entry = self._tests.get(name)
        if entry is None:
            return

        self._queue_history(
            "tests_failed_history",
            pk=(entry.id, self._run_id),
            values=(
                entry.id,
                self._run_id,
                name,
                entry.test_file,
                -1,
                entry.duration,
                entry.forced
            ),
        )

    def _maybe_emit_test_failed_history(self, name: str) -> None:
        """Queue a tests_failed_history row if the failed flag flipped.

        For new tests (_base_failed is None) always emit the initial
        state. For existing tests emit only when failed != _base_failed.

        If a prior emit in this session is reverted (failed flips back
        to the baseline value), the queued row is dropped so the
        session's net effect is zero history rows.
        """
        if not self._versioning_enabled or self._run_id is None:
            return
        entry = self._tests.get(name)
        if entry is None:
            return
        key = ("tests_failed_history", (entry.id, self._run_id))
        if entry._base_failed is not None and entry.failed == entry._base_failed:
            self._history_ops.pop(key, None)
            return
        self._history_ops[key] = (
            entry.id,
            self._run_id,
            name,
            entry.test_file,
            1 if entry.failed else 0,
            entry.duration,
            entry.forced
        )

    def _maybe_emit_test_deps_history(
        self, test_id: int, blob: bytes, packages: str
    ) -> None:
        """Queue a test_deps_history row if bitmap or packages changed.

        Called just before a (test_id, blob, packages) row is written
        to test_deps in save_batch. Comparison is against the SESSION
        BASELINE (captured in _preload) rather than the running cache,
        so reverts-to-baseline within a single session correctly cancel
        any earlier queued row and leave zero net history.
        """
        if not self._versioning_enabled or self._run_id is None:
            return
        name = self._test_ids_to_names.get(test_id)
        if name is None:
            # Unknown test_id — defensive. Skipping keeps the invariant
            # that every history row has a resolvable name.
            return
        key = ("test_deps_history", (test_id, self._run_id))
        base_blob = self._base_blobs.get(test_id)
        base_pkgs = self._base_packages.get(test_id, "")
        new_pkgs = packages or ""
        if base_blob == blob and base_pkgs == new_pkgs:
            # Net effect matches baseline — drop any queued emit.
            self._history_ops.pop(key, None)
            return
        entry = self._tests.get(name)
        test_file = entry.test_file if entry is not None else None
        self._history_ops[key] = (
            test_id,
            self._run_id,
            name,
            test_file,
            blob,
            new_pkgs,
        )

    def _flush_history_ops(self) -> None:
        """Write the buffered history rows. Called from save_batch."""
        if not self._history_ops:
            return
        con = self._db.con
        by_table: Dict[str, List[tuple]] = {}
        for (table, _pk), values in self._history_ops.items():
            by_table.setdefault(table, []).append(values)
        if "files_history" in by_table:
            con.executemany(
                "INSERT OR REPLACE INTO files_history "
                "(file_id, run_id, path, file_type, checksum, fsha) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                by_table["files_history"],
            )
        if "tests_failed_history" in by_table:
            con.executemany(
                "INSERT OR REPLACE INTO tests_failed_history "
                "(test_id, run_id, name, test_file, failed, duration, forced) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                by_table["tests_failed_history"],
            )
        if "test_deps_history" in by_table:
            con.executemany(
                "INSERT OR REPLACE INTO test_deps_history "
                "(test_id, run_id, name, test_file, file_bitmap, "
                "external_packages) VALUES (?, ?, ?, ?, ?, ?)",
                by_table["test_deps_history"],
            )
        self._history_ops.clear()

    # ---- Core mutation methods ----

    def get_file_id(
        self,
        path: str,
        checksum: int = None,
        fsha: str = None,
        file_type: str = "python",
        run_id: int = None,
    ) -> int:
        """Dict lookup. If new file, INSERT into DB and add to cache. O(1) for known files."""
        entry = self._files.get(path)
        if entry is not None:
            return entry.id

        # New file -- INSERT into DB immediately
        cursor = self._db.con.cursor()
        cursor.execute(
            "INSERT INTO files (path, checksum, fsha, file_type, run_id) VALUES (?, ?, ?, ?, ?)",
            (path, checksum, fsha, file_type, run_id),
        )
        file_id = cursor.lastrowid
        # New files have no session baseline; _base_* stays None so the
        # first update_file_checksum (or this call if checksum/fsha
        # are already provided) will trigger an emission.
        self._files[path] = FileEntry(
            id=file_id,
            checksum=checksum,
            fsha=fsha,
            file_type=file_type,
            run_id=run_id,
            _base_checksum=None,
            _base_fsha=None,
        )
        # Queue initial history row only if content is already known.
        # If the caller passed checksum=None, fsha=None (common in the
        # raw-deps path that just needs a file id for the bitmap), the
        # helper will no-op and wait until update_file_checksum fills
        # the real values.
        self._maybe_emit_file_history(path)
        return file_id

    def ensure_tests_batch(
        self, run_id: int, tests: list
    ) -> Dict[str, int]:
        """For cached tests: update metadata, mark dirty. For new tests: bulk INSERT.

        Args:
            run_id: Current run ID
            tests: List of (test_name, test_file, duration, failed) tuples

        Returns:
            Dict mapping test_name to test_id
        """
        result = {}
        new_tests = []
        # Track names whose state changed in this call so we can emit
        # history after the in-memory updates (or bulk inserts) settle.
        changed_names: List[str] = []

        for name, test_file, duration, failed, forced in tests:
            entry = self._tests.get(name)
            if entry is not None:
                entry.test_file = test_file or entry.test_file
                entry.duration = duration if duration is not None else entry.duration
                entry.failed = failed
                entry.forced = forced
                entry.run_id = run_id
                entry.dirty = True
                result[name] = entry.id
                changed_names.append(name)
            else:
                new_tests.append((name, test_file, duration, failed, forced))

        if new_tests:
            cursor = self._db.con.cursor()
            cursor.executemany(
                "INSERT OR IGNORE INTO tests (name, test_file, duration, failed, forced, run_id)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (n, tf, d, 1 if f else 0, forced, run_id)
                    for n, tf, d, f, forced in new_tests
                ],
            )

            # Fetch IDs for newly inserted tests
            new_names = [t[0] for t in new_tests]
            chunk_size = 500
            for i in range(0, len(new_names), chunk_size):
                chunk = new_names[i : i + chunk_size]
                placeholders = ",".join("?" * len(chunk))
                rows = cursor.execute(
                    f"SELECT id, name, test_file, duration, failed, forced, run_id"
                    f" FROM tests WHERE name IN ({placeholders})",
                    chunk,
                ).fetchall()
                for row in rows:
                    # Brand-new entries have _base_failed=None so the
                    # first emit always produces a history row regardless
                    # of the initial failed value.
                    entry = TestEntry(
                        id=row["id"],
                        test_file=row["test_file"],
                        duration=row["duration"],
                        failed=bool(row["failed"]),
                        forced=row["forced"],
                        run_id=row["run_id"],
                        _base_failed=None,
                    )
                    self._tests[row["name"]] = entry
                    self._test_ids_to_names[row["id"]] = row["name"]
                    result[row["name"]] = row["id"]
                    changed_names.append(row["name"])

        # Emit history for any test whose state changed in this call.
        # For existing tests: emit only if `failed` flipped from base.
        # For new tests: _base_failed is None so the helper always emits.
        for name in changed_names:
            self._maybe_emit_test_failed_history(name)

        return result

    def get_existing_blob(self, test_id: int) -> Optional[bytes]:
        """Dict lookup for skip-unchanged check. No DB read."""
        return self._blobs.get(test_id)

    def get_existing_packages(self, test_id: int) -> str:
        """Dict lookup for skip-unchanged check on external packages.

        Returns the normalized packages string ("" for no packages or
        unknown test_id). Paired with ``get_existing_blob`` to form a
        complete test_deps equality check — skipping a write based on
        bitmap alone drops package-only changes on the floor.
        """
        return self._packages.get(test_id, "")

    def save_batch(self, pending: List[Tuple[int, bytes, str]]) -> None:
        """Flush dirty tests and write changed test_deps.

        Caller should wrap this in a ``with db.con:`` transaction.

        Args:
            pending: List of (test_id, blob, external_packages_str) tuples
        """
        con = self._db.con

        # Flush dirty tests
        dirty_tests = [
            (e.duration, 1 if e.failed else 0, e.forced, e.test_file, e.run_id, e.id)
            for e in self._tests.values()
            if e.dirty
        ]
        if dirty_tests:
            con.executemany(
                "UPDATE tests SET duration = COALESCE(?, duration), failed = ?, forced = ?,"
                " test_file = COALESCE(?, test_file),"
                " run_id = COALESCE(?, run_id) WHERE id = ?",
                dirty_tests,
            )
            for e in self._tests.values():
                if e.dirty:
                    e.dirty = False

        # Emit test_deps_history BEFORE updating the blob/packages
        # caches, so the emit helpers can compare incoming values
        # against the pre-write state. The history flush itself runs
        # at the end of this method, inside the caller's transaction.
        if pending and self._versioning_enabled and self._run_id is not None:
            for test_id, blob, pkgs in pending:
                self._maybe_emit_test_deps_history(test_id, blob, pkgs)

        # Write changed test_deps
        if pending:
            con.executemany(
                "INSERT OR REPLACE INTO test_deps"
                " (test_id, file_bitmap, external_packages) VALUES (?, ?, ?)",
                pending,
            )
            # Update blob and packages caches so subsequent skip-unchanged
            # checks in the same session see the just-written state.
            for test_id, blob, pkgs in pending:
                self._blobs[test_id] = blob
                self._packages[test_id] = pkgs or ""

        # Flush queued history rows. No-op when versioning is off.
        self._flush_history_ops()

    # ---- Read-only accessors (all from cache) ----

    def get_file_checksums(self) -> Dict[str, Optional[int]]:
        """Return {path: checksum} from cache."""
        return {path: e.checksum for path, e in self._files.items()}

    def get_file_id_map(self) -> Dict[str, int]:
        """Return {path: id} from cache."""
        return {path: e.id for path, e in self._files.items()}

    def get_file_ids_for_paths(self, paths: Set[str]) -> Set[int]:
        """Return file IDs for known paths. O(len(paths))."""
        result = set()
        for path in paths:
            entry = self._files.get(path)
            if entry is not None:
                result.add(entry.id)
        return result

    def update_file_checksum(self, path: str, checksum: int, fsha: str = None) -> None:
        """Update checksum in cache and DB immediately."""
        entry = self._files.get(path)
        if entry is not None:
            entry.checksum = checksum
            if fsha is not None:
                entry.fsha = fsha
        # Write to DB immediately (determine_stable needs this persisted)
        self._db.con.execute(
            "UPDATE files SET checksum = ?, fsha = COALESCE(?, fsha) WHERE path = ?",
            (checksum, fsha, path),
        )
        # Emit history if the values differ from the session baseline.
        # No-op when the file isn't cached, versioning is disabled, or
        # the values are unchanged.
        self._maybe_emit_file_history(path)

    def all_filenames(self) -> List[str]:
        """Return all Python filenames from cache."""
        return [path for path, e in self._files.items() if e.file_type == "python"]

    def all_test_executions(self) -> Dict[str, Dict]:
        """Return {name: {duration, failed, forced}} from cache."""
        return {
            name: {
                "duration": e.duration,
                "failed": e.failed,
                "forced": e.forced,
            }
            for name, e in self._tests.items()
        }

    def get_failing_tests(self) -> List[str]:
        """Return names of tests that failed in their last run."""
        return [name for name, e in self._tests.items() if e.failed]

    def get_test_files_for_tests(self, test_names: Set[str]) -> Set[str]:
        """Return test files for a set of test names."""
        result = set()
        for name in test_names:
            entry = self._tests.get(name)
            if entry is not None and entry.test_file:
                result.add(entry.test_file)
        return result

    def get_all_test_files(self) -> Set[str]:
        """Return all known test files."""
        return {e.test_file for e in self._tests.values() if e.test_file}
