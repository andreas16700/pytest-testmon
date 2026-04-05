"""
Unified in-memory cache for controller batch processing.

Pre-loads files, tests, and test_deps tables at session start.
All lookups are dict access. New entries INSERT immediately.
Dirty metadata flushes in batch.
"""

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


@dataclass
class TestEntry:
    id: int
    test_file: Optional[str]
    duration: Optional[float]
    failed: bool
    run_id: Optional[int]
    dirty: bool = False


class DepStore:
    """Unified in-memory cache backed by a local SQLite DB.

    Pre-loads files, tests, and test_deps tables into memory at session start.
    All lookups are dict access. New entries INSERT immediately.
    Dirty test metadata flushes in batch via save_batch().
    """

    def __init__(self, db):
        self._db = db
        self._files: Dict[str, FileEntry] = {}  # path -> FileEntry
        self._tests: Dict[str, TestEntry] = {}  # name -> TestEntry
        self._blobs: Dict[int, bytes] = {}       # test_id -> file_bitmap blob
        self._packages: Dict[int, str] = {}      # test_id -> external_packages (normalized "")
        self._preload()

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
            )

        for row in con.execute(
            "SELECT id, name, test_file, duration, failed, run_id FROM tests"
        ):
            self._tests[row["name"]] = TestEntry(
                id=row["id"],
                test_file=row["test_file"],
                duration=row["duration"],
                failed=bool(row["failed"]),
                run_id=row["run_id"],
            )

        for row in con.execute(
            "SELECT test_id, file_bitmap, external_packages FROM test_deps"
        ):
            test_id = row["test_id"]
            self._blobs[test_id] = bytes(row["file_bitmap"])
            # Normalize NULL -> "" so skip-unchanged comparisons don't
            # treat "no packages cached" and "empty packages list"
            # differently.
            self._packages[test_id] = row["external_packages"] or ""

        logger.info(
            "DepStore pre-loaded: %d files, %d tests, %d blobs",
            len(self._files),
            len(self._tests),
            len(self._blobs),
        )

    # ---- Core mutation methods ----

    def get_file_id(
        self,
        path: str,
        checksum: int = None,
        fsha: str = None,
        file_type: str = "python",
    ) -> int:
        """Dict lookup. If new file, INSERT into DB and add to cache. O(1) for known files."""
        entry = self._files.get(path)
        if entry is not None:
            return entry.id

        # New file -- INSERT into DB immediately
        cursor = self._db.con.cursor()
        cursor.execute(
            "INSERT INTO files (path, checksum, fsha, file_type) VALUES (?, ?, ?, ?)",
            (path, checksum, fsha, file_type),
        )
        file_id = cursor.lastrowid
        self._files[path] = FileEntry(
            id=file_id,
            checksum=checksum,
            fsha=fsha,
            file_type=file_type,
            run_id=None,
        )
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

        for name, test_file, duration, failed in tests:
            entry = self._tests.get(name)
            if entry is not None:
                entry.test_file = test_file or entry.test_file
                entry.duration = duration if duration is not None else entry.duration
                entry.failed = failed
                entry.run_id = run_id
                entry.dirty = True
                result[name] = entry.id
            else:
                new_tests.append((name, test_file, duration, failed))

        if new_tests:
            cursor = self._db.con.cursor()
            cursor.executemany(
                "INSERT OR IGNORE INTO tests (name, test_file, duration, failed, run_id)"
                " VALUES (?, ?, ?, ?, ?)",
                [
                    (n, tf, d, 1 if f else 0, run_id)
                    for n, tf, d, f in new_tests
                ],
            )

            # Fetch IDs for newly inserted tests
            new_names = [t[0] for t in new_tests]
            chunk_size = 500
            for i in range(0, len(new_names), chunk_size):
                chunk = new_names[i : i + chunk_size]
                placeholders = ",".join("?" * len(chunk))
                rows = cursor.execute(
                    f"SELECT id, name, test_file, duration, failed, run_id"
                    f" FROM tests WHERE name IN ({placeholders})",
                    chunk,
                ).fetchall()
                for row in rows:
                    entry = TestEntry(
                        id=row["id"],
                        test_file=row["test_file"],
                        duration=row["duration"],
                        failed=bool(row["failed"]),
                        run_id=row["run_id"],
                    )
                    self._tests[row["name"]] = entry
                    result[row["name"]] = row["id"]

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
            (e.duration, 1 if e.failed else 0, e.test_file, e.run_id, e.id)
            for e in self._tests.values()
            if e.dirty
        ]
        if dirty_tests:
            con.executemany(
                "UPDATE tests SET duration = COALESCE(?, duration), failed = ?,"
                " test_file = COALESCE(?, test_file),"
                " run_id = COALESCE(?, run_id) WHERE id = ?",
                dirty_tests,
            )
            for e in self._tests.values():
                if e.dirty:
                    e.dirty = False

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

    def all_filenames(self) -> List[str]:
        """Return all Python filenames from cache."""
        return [path for path, e in self._files.items() if e.file_type == "python"]

    def all_test_executions(self) -> Dict[str, Dict]:
        """Return {name: {duration, failed, forced}} from cache."""
        return {
            name: {
                "duration": e.duration,
                "failed": e.failed,
                "forced": None,
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
