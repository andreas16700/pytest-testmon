import json
import os
import sqlite3

from typing import Dict, List, Optional, Set

from ezmon.common import TestExecutions
from ezmon.common import get_logger
from ezmon.bitmap_deps import TestDeps, find_affected_tests


DATA_VERSION = 20  # Add tests_failed column to runs table


class TestmonDbException(Exception):
    pass


def connect(datafile, readonly=False):
    # In xdist worker mode (readonly=True), wait for controller to create the DB
    if readonly:
        import time
        for _ in range(50):  # Wait up to 5 seconds
            if os.path.exists(datafile):
                break
            time.sleep(0.1)
    return sqlite3.connect(
        f"file:{datafile}{'?mode=ro' if readonly else ''}", uri=True, timeout=60
    )


def connection_options(connection):
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = OFF")
    connection.execute("PRAGMA foreign_keys = TRUE ")
    connection.execute("PRAGMA recursive_triggers = TRUE ")
    connection.row_factory = sqlite3.Row
    return connection


def check_data_version(connection, datafile, data_version):
    stored_data_version = connection.execute("PRAGMA user_version").fetchone()[0]

    if int(stored_data_version) == data_version:
        return connection, False

    connection.close()
    os.remove(datafile)
    connection = connect(datafile)
    connection = connection_options(connection)
    return connection, True


class DB:  # pylint: disable=too-many-public-methods
    def __init__(self, datafile, readonly=False):
        self._readonly = readonly
        self._closed = False
        file_exists = os.path.exists(datafile)
        self._logger = get_logger(__name__)

        connection = connect(datafile, readonly)
        connection, old_format = check_data_version(
            connection, datafile, self.version_compatibility()
        )
        self.con = connection_options(connection)
        if not readonly:
            try:
                self.con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except sqlite3.DatabaseError:
                pass

        if (not file_exists) or old_format:
            self.init_tables()
            self.file_created = True
        else:
            self.file_created = False

    def version_compatibility(self):
        return DATA_VERSION

    def __enter__(self):
        self.con = self.con.__enter__()
        return self

    def __exit__(self, *args, **kwargs):
        self.con.__exit__(*args, **kwargs)

    def close(self) -> None:
        if self._closed or self.con is None:
            return
        try:
            if not self._readonly:
                self.con.commit()
                # Merge WAL into the main DB so copied .testmondata is complete.
                self.con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            try:
                self.con.close()
            finally:
                self.con = None
                self._closed = True

    def finish_execution(self, duration=None, select=True, commit_id: Optional[str] = None):
        """Legacy finish_execution — delegates to finish_run if a run exists."""
        with self.con as con:
            self.vacuum_files(con)

    def vacuum_files(self, con):
        """Clean up orphaned data.

        With bitmap-based storage, we can't easily determine which file IDs
        are still referenced without deserializing all bitmaps. For now,
        we just clean up tests without any dependencies.
        """
        con.execute(
            """DELETE FROM tests WHERE id NOT IN (SELECT test_id FROM test_deps)"""
        )

    def fetch_current_run_stats(self, run_id=None):
        """Fetch run statistics from the tests table."""
        with self.con as con:
            cursor = con.cursor()
            run_all_tests, run_all_time = cursor.execute(
                "SELECT count(*), sum(duration) FROM tests"
            ).fetchone()
            if run_id is not None:
                run_saved_tests, run_saved_time = cursor.execute(
                    "SELECT count(*), sum(duration) FROM tests WHERE run_id = ?",
                    (run_id,)
                ).fetchone()
            else:
                run_saved_tests, run_saved_time = run_all_tests, run_all_time

        return (
            run_saved_time,
            run_all_time,
            run_saved_tests,
            run_all_tests,
        )

    def fetch_saving_stats(self, select):
        (
            run_saved_time,
            run_all_time,
            run_saved_tests,
            run_all_tests,
        ) = self.fetch_current_run_stats()
        attribute_prefix = "" if select else "potential_"
        total_saved_time = self.fetch_attribute(
            attribute=f"{attribute_prefix}time_saved", default=0
        )
        total_all_time = self.fetch_attribute(
            attribute=f"{attribute_prefix}time_all", default=0
        )
        total_saved_tests = self.fetch_attribute(
            attribute=f"{attribute_prefix}tests_saved", default=0
        )
        total_all_tests = self.fetch_attribute(
            attribute=f"{attribute_prefix}tests_all", default=0
        )

        return (
            run_saved_time,
            run_all_time,
            run_saved_tests,
            run_all_tests,
            total_saved_time,
            total_all_time,
            total_saved_tests,
            total_all_tests,
        )

    # ====== Run management ======

    def create_run(self, commit_id, packages, python_version):
        """Create a new run record. Called at session start."""
        cursor = self.con.execute(
            "INSERT INTO runs (commit_id, packages, python_version) VALUES (?, ?, ?)",
            (commit_id, packages, python_version),
        )
        return cursor.lastrowid

    def finish_run(self, run_id, duration, tests_selected, tests_deselected,
                   tests_failed, tests_all, time_saved, time_all):
        """Update a run with final stats. Called at session end."""
        self.con.execute(
            """UPDATE runs SET duration=?, tests_selected=?, tests_deselected=?,
               tests_failed=?, tests_all=?, time_saved=?, time_all=? WHERE id=?""",
            (duration, tests_selected, tests_deselected, tests_failed,
             tests_all, time_saved, time_all, run_id),
        )

    def get_latest_run_commit_id(self) -> Optional[str]:
        """Return the commit_id for the latest run."""
        row = self.con.execute(
            "SELECT commit_id FROM runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return row["commit_id"] if row else None

    def get_previous_run_info(self):
        """Get packages and python_version from the most recent run."""
        row = self.con.execute(
            "SELECT commit_id, packages, python_version FROM runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None

    # ====== Metadata ======

    def write_attribute(self, attribute, data):
        dataid = attribute
        with self.con as con:
            con.execute(
                "INSERT OR REPLACE INTO metadata VALUES (?, ?)",
                [dataid, json.dumps(data)],
            )

    def fetch_attribute(self, attribute, default=None):
        cursor = self.con.execute(
            "SELECT value FROM metadata WHERE key=?",
            [attribute],
        )
        result = cursor.fetchone()
        if result:
            return json.loads(result[0])
        return default

    def increment_attributes(self, attributes_to_increment):
        def sum_with_none(*to_sum):
            return sum(filter(None, to_sum))

        for attribute_name in attributes_to_increment:
            old_value = self.fetch_attribute(
                attribute=attribute_name, default=0
            )
            with self.con as con:
                con.execute(
                    "INSERT OR REPLACE INTO metadata VALUES (?, ?)",
                    [
                        attribute_name,
                        sum_with_none(
                            old_value, attributes_to_increment[attribute_name]
                        ),
                    ],
                )

    def _create_metadata_statement(self) -> str:
        return """CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT);"""

    def _create_runs_statement(self) -> str:
        return """CREATE TABLE runs (
            id INTEGER PRIMARY KEY,
            commit_id TEXT,
            packages TEXT,
            python_version TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            duration REAL,
            tests_selected INTEGER DEFAULT 0,
            tests_deselected INTEGER DEFAULT 0,
            tests_failed INTEGER DEFAULT 0,
            tests_all INTEGER DEFAULT 0,
            time_saved REAL DEFAULT 0,
            time_all REAL DEFAULT 0
        );"""
            
    def _create_files_table_statement(self) -> str:
        """Table for unified file registry with stable integer IDs."""
        return """
            CREATE TABLE IF NOT EXISTS files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER REFERENCES runs(id),
                path TEXT NOT NULL UNIQUE,
                file_type TEXT DEFAULT 'python' CHECK (file_type IN ('python', 'data')),
                checksum INTEGER,
                fsha TEXT
            );
            CREATE INDEX IF NOT EXISTS files_path ON files (path);
        """

    def _create_tests_table_statement(self) -> str:
        """Table for simplified test storage."""
        return """
            CREATE TABLE IF NOT EXISTS tests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER REFERENCES runs(id),
                name TEXT NOT NULL UNIQUE,
                test_file TEXT,
                duration REAL,
                failed INTEGER DEFAULT 0,
                forced INTEGER DEFAULT NULL
            );
            CREATE INDEX IF NOT EXISTS tests_name ON tests (name);
        """

    def _create_test_deps_table_statement(self) -> str:
        """Table for test dependencies stored as Roaring bitmap blobs.

        Each test has a single row with:
        - file_bitmap: Compressed Roaring bitmap of file IDs (zstd compressed)
        - external_packages: Comma-separated list of external package names
        """
        return """
            CREATE TABLE IF NOT EXISTS test_deps (
                test_id INTEGER PRIMARY KEY,
                file_bitmap BLOB NOT NULL,
                external_packages TEXT,
                FOREIGN KEY(test_id) REFERENCES tests(id) ON DELETE CASCADE
            );
        """

    def init_tables(self):
        connection = self.con

        connection.executescript(
            self._create_metadata_statement()
            + self._create_runs_statement()
            + self._create_files_table_statement()
            + self._create_tests_table_statement()
            + self._create_test_deps_table_statement()
        )

        connection.execute(f"PRAGMA user_version = {self.version_compatibility()}")

    def get_file_dependency_filenames(self):
        """Get all data file dependency filenames for an environment.

        Uses the new files table to find data files referenced by tests.
        """
        cursor = self.con.execute(
            """
            SELECT DISTINCT f.path
            FROM files f
            WHERE f.file_type = 'data'
            """,
        )
        return [row["path"] for row in cursor]

    def delete_test_executions(self, test_names):
        """Delete tests and their dependencies."""
        for test_name in test_names:
            self.delete_test(test_name)

    def all_test_executions(self):
        """Get all tests with their metadata."""
        return {
            row["name"]: {"duration": row["duration"], "failed": bool(row["failed"]), "forced": row["forced"]}
            for row in self.con.execute(
                "SELECT name, duration, failed, forced FROM tests"
            )
        }

    def filenames(self):
        """Get all Python filenames tracked."""
        cursor = self.con.execute(
            "SELECT DISTINCT path FROM files WHERE file_type = 'python'"
        )
        return [row[0] for row in cursor]

    # TODO unify with filenames? Restrict not to go into ancient history, but not miss combinations?
    def all_filenames(self):
        """Get all tracked filenames from the files table."""
        cursor = self.con.execute(
            """SELECT DISTINCT path FROM files WHERE file_type = 'python'"""
        )
        return [row[0] for row in cursor]

    # ==========================================================================
    # New Roaring Bitmap-based methods for simplified dependency storage
    # ==========================================================================
    # ==========================================================================
    # New Roaring Bitmap-based methods for simplified dependency storage
    # ==========================================================================

    def get_or_create_file_id(self, path: str, checksum: int = None,
                              fsha: str = None, file_type: str = 'python',
                              run_id: int = None) -> int:
        """Get or create a stable file ID for a given path.

        Each file gets a unique integer ID that can be stored in Roaring bitmaps.
        The run_id records which run last wrote this file's metadata.
        """
        cursor = self.con.cursor()

        # Try to get existing file ID
        row = cursor.execute(
            "SELECT id FROM files WHERE path = ?", (path,)
        ).fetchone()

        if row:
            file_id = row[0]
            # Update checksum/fsha/run_id if provided
            updates = []
            params = []
            if checksum is not None:
                updates.append("checksum = ?")
                params.append(checksum)
            if fsha is not None:
                updates.append("fsha = ?")
                params.append(fsha)
            if run_id is not None:
                updates.append("run_id = ?")
                params.append(run_id)
            if updates:
                params.append(file_id)
                cursor.execute(
                    f"UPDATE files SET {', '.join(updates)} WHERE id = ?",
                    params,
                )
            return file_id

        # Create new file record
        cursor.execute(
            """INSERT INTO files (path, checksum, fsha, file_type, run_id)
               VALUES (?, ?, ?, ?, ?)""",
            (path, checksum, fsha, file_type, run_id)
        )
        return cursor.lastrowid

    def get_file_id_map(self) -> Dict[str, int]:
        """Get a mapping of file paths to their IDs."""
        cursor = self.con.execute("SELECT path, id FROM files")
        return {row["path"]: row["id"] for row in cursor}

    def get_file_ids_for_paths(self, paths: Set[str]) -> Set[int]:
        """Return file IDs for known file paths."""
        if not paths:
            return set()
        placeholders = ",".join("?" * len(paths))
        cursor = self.con.execute(
            f"SELECT id FROM files WHERE path IN ({placeholders})",
            tuple(sorted(paths)),
        )
        return {row["id"] for row in cursor}

    def get_file_checksums(self) -> Dict[str, int]:
        """Get current checksums for all files.

        Returns:
            Dict mapping file path to checksum
        """
        cursor = self.con.execute("SELECT path, checksum FROM files")
        return {row["path"]: row["checksum"] for row in cursor}

    def update_file_checksum(self, path: str, checksum: int, fsha: str = None) -> None:
        """Update the checksum for a file.

        Args:
            path: File path
            checksum: New checksum value
            fsha: Optional git blob SHA
        """
        self.con.execute(
            """UPDATE files SET checksum = ?, fsha = COALESCE(?, fsha)
               WHERE path = ?""",
            (checksum, fsha, path)
        )

    def get_or_create_test_id(
        self,
        test_name: str,
        duration: float = None,
        failed: bool = False,
        test_file: Optional[str] = None,
        run_id: int = None,
        forced: Optional[int] = None,
    ) -> int:
        """Get or create a test ID for a given test name.

        Args:
            test_name: Full test node ID
            duration: Test duration in seconds
            failed: Whether the test failed
            test_file: Test file path
            run_id: Run ID for provenance tracking
        """
        cursor = self.con.cursor()

        row = cursor.execute(
            "SELECT id FROM tests WHERE name = ?", (test_name,)
        ).fetchone()

        if row:
            test_id = row[0]
            updates = ["duration = COALESCE(?, duration)", "failed = ?",
                        "test_file = COALESCE(?, test_file)", "forced = ?"]
            params = [duration, 1 if failed else 0, test_file, forced]
            if run_id is not None:
                updates.append("run_id = ?")
                params.append(run_id)
            params.append(test_id)
            cursor.execute(
                f"UPDATE tests SET {', '.join(updates)} WHERE id = ?", params
            )
            return test_id

        cursor.execute(
            """INSERT INTO tests (name, test_file, duration, failed, forced, run_id)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (test_name, test_file, duration, 1 if failed else 0, forced, run_id)
        )
        return cursor.lastrowid

    def get_or_create_test_ids_batch(
        self,
        run_id: int,
        tests: list,
    ) -> Dict[str, int]:
        """Bulk get-or-create test IDs.

        Args:
            run_id: Run ID for provenance tracking
            tests: List of (test_name, test_file, duration, failed) tuples

        Returns:
            Dict mapping test_name to test_id
        """
        if not tests:
            return {}

        cursor = self.con.cursor()
        chunk_size = 500

        # 1. Bulk INSERT OR IGNORE — creates rows for new tests
        cursor.executemany(
            """INSERT OR IGNORE INTO tests (name, test_file, duration, failed, forced, run_id)
               VALUES (?, ?, ?, ?, ?, ?)""",
            [(name, tf, dur, 1 if fail else 0, forced, run_id) for name, tf, dur, fail, forced in tests],
        )

        # 2. Bulk SELECT to get all IDs
        all_names = [t[0] for t in tests]
        result = {}
        for i in range(0, len(all_names), chunk_size):
            chunk = all_names[i:i + chunk_size]
            placeholders = ",".join("?" * len(chunk))
            rows = cursor.execute(
                f"SELECT id, name FROM tests WHERE name IN ({placeholders})",
                chunk,
            ).fetchall()
            for row in rows:
                result[row[1]] = row[0]

        # 3. Bulk UPDATE duration/failed/forced/run_id for all tests
        cursor.executemany(
            """UPDATE tests SET duration = COALESCE(?, duration),
               failed = ?, forced = ?, test_file = COALESCE(?, test_file),
               run_id = COALESCE(?, run_id)
               WHERE name = ?""",
            [(dur, 1 if fail else 0, forced, tf, run_id, name) for name, tf, dur, fail, forced in tests],
        )

        return result

    def save_test_deps(self, test_id: int, deps: TestDeps) -> None:
        """Save test dependencies as a compressed Roaring bitmap.

        Args:
            test_id: Test ID from tests table
            deps: TestDeps object with file IDs and external packages
        """
        blob = deps.serialize()
        external_packages = deps.serialize_external_packages()

        with self.con as con:
            con.execute(
                """INSERT OR REPLACE INTO test_deps (test_id, file_bitmap, external_packages)
                   VALUES (?, ?, ?)""",
                (test_id, blob, external_packages)
            )

    def save_test_deps_batch(self, batch: list) -> None:
        """Save multiple test dependencies in a single transaction.

        Args:
            batch: List of (test_id, blob, external_packages_str) tuples
        """
        if not batch:
            return
        with self.con as con:
            con.executemany(
                """INSERT OR REPLACE INTO test_deps (test_id, file_bitmap, external_packages)
                   VALUES (?, ?, ?)""",
                batch,
            )

    def get_test_deps_batch(self, test_ids: list) -> Dict[int, bytes]:
        """Bulk-load existing test_deps blobs for a set of test IDs.

        Args:
            test_ids: List of test IDs to look up

        Returns:
            Dict mapping test_id to file_bitmap blob
        """
        if not test_ids:
            return {}
        result = {}
        # Process in chunks to avoid exceeding SQLite variable limit
        chunk_size = 500
        for i in range(0, len(test_ids), chunk_size):
            chunk = test_ids[i:i + chunk_size]
            placeholders = ",".join("?" * len(chunk))
            cursor = self.con.execute(
                f"SELECT test_id, file_bitmap FROM test_deps WHERE test_id IN ({placeholders})",
                chunk,
            )
            for row in cursor:
                result[row["test_id"]] = bytes(row["file_bitmap"])
        return result

    def get_test_deps(self, test_id: int) -> Optional[TestDeps]:
        """Get test dependencies for a single test.

        Args:
            test_id: Test ID

        Returns:
            TestDeps object or None if not found
        """
        row = self.con.execute(
            "SELECT file_bitmap, external_packages FROM test_deps WHERE test_id = ?",
            (test_id,)
        ).fetchone()

        if not row:
            return None

        return TestDeps.deserialize(test_id, row["file_bitmap"], row["external_packages"])

    def get_all_test_deps(self) -> List[TestDeps]:
        """Get all test dependencies.

        Returns:
            List of TestDeps objects
        """
        cursor = self.con.execute(
            """SELECT t.id, td.file_bitmap, td.external_packages
               FROM tests t
               JOIN test_deps td ON t.id = td.test_id"""
        )

        deps_list = []
        for row in cursor:
            deps = TestDeps.deserialize(
                row["id"], row["file_bitmap"], row["external_packages"]
            )
            deps_list.append(deps)

        return deps_list

    def find_affected_tests_bitmap(
        self,
        changed_file_ids: Set[int],
        changed_packages: Optional[Set[str]] = None
    ) -> List[str]:
        """Find tests affected by file or package changes using bitmap intersection.

        Args:
            changed_file_ids: Set of file IDs that changed
            changed_packages: Set of package names that changed

        Returns:
            List of affected test names
        """
        all_deps = self.get_all_test_deps()
        affected_test_ids = find_affected_tests(all_deps, changed_file_ids, changed_packages)

        # Get test names for the affected IDs
        if not affected_test_ids:
            return []

        placeholders = ",".join("?" * len(affected_test_ids))
        cursor = self.con.execute(
            f"SELECT name FROM tests WHERE id IN ({placeholders})",
            affected_test_ids
        )
        return [row["name"] for row in cursor]

    def get_changed_file_ids(
        self,
        files_checksums: Dict[str, int]
    ) -> Set[int]:
        """Find file IDs for files whose checksums have changed.

        Compares provided checksums against stored checksums.

        Args:
            files_checksums: Dict of {path: current_checksum}

        Returns:
            Set of file IDs whose checksums differ
        """
        changed_ids = set()

        for path, current_checksum in files_checksums.items():
            row = self.con.execute(
                "SELECT id, checksum FROM files WHERE path = ?", (path,)
            ).fetchone()

            if row:
                if row["checksum"] != current_checksum:
                    changed_ids.add(row["id"])
            # New file - it's changed by definition
            else:
                file_id = self.get_or_create_file_id(path, current_checksum)
                changed_ids.add(file_id)

        return changed_ids

    def get_tests_for_env(self) -> Dict[str, Dict]:
        """Get all tests with their metadata."""
        cursor = self.con.execute(
            "SELECT name, duration, failed FROM tests"
        )
        return {
            row["name"]: {"duration": row["duration"], "failed": bool(row["failed"])}
            for row in cursor
        }

    def get_test_files_for_tests(self, test_names: Set[str]) -> Set[str]:
        """Get test files for a set of test names."""
        if not test_names:
            return set()
        placeholders = ",".join("?" * len(test_names))
        cursor = self.con.execute(
            f"SELECT DISTINCT test_file FROM tests WHERE name IN ({placeholders})",
            tuple(sorted(test_names)),
        )
        return {row["test_file"] for row in cursor if row["test_file"]}

    def get_all_test_files(self) -> Set[str]:
        """Get all known test files."""
        cursor = self.con.execute(
            "SELECT DISTINCT test_file FROM tests"
        )
        return {row["test_file"] for row in cursor if row["test_file"]}

    def delete_test(self, test_name: str) -> None:
        """Delete a test and its dependencies."""
        row = self.con.execute(
            "SELECT id FROM tests WHERE name = ?", (test_name,)
        ).fetchone()

        if row:
            test_id = row[0]
            with self.con as con:
                con.execute("DELETE FROM test_deps WHERE test_id = ?", (test_id,))
                con.execute("DELETE FROM tests WHERE id = ?", (test_id,))

    def get_changed_data_file_ids(
        self,
        file_deps_shas: Dict[str, str]
    ) -> Set[int]:
        """Find file IDs for data files whose fsha has changed.

        Args:
            file_deps_shas: Dict of {path: current_fsha}

        Returns:
            Set of file IDs whose fsha differs
        """
        changed_ids = set()

        for path, current_fsha in file_deps_shas.items():
            row = self.con.execute(
                "SELECT id, fsha FROM files WHERE path = ?", (path,)
            ).fetchone()

            if row:
                if row["fsha"] != current_fsha:
                    changed_ids.add(row["id"])
            else:
                # New file - get or create with data type
                file_id = self.get_or_create_file_id(
                    path, checksum=None, fsha=current_fsha, file_type='data'
                )
                changed_ids.add(file_id)

        return changed_ids

    def get_failing_tests_bitmap(self) -> List[str]:
        """Get names of tests that failed in their last run."""
        cursor = self.con.execute(
            "SELECT name FROM tests WHERE failed = 1"
        )
        return [row["name"] for row in cursor]

    def determine_tests_bitmap(
        self,
        files_checksums: Dict[str, int],
        file_deps_shas: Dict[str, str] = None,
        changed_packages: Optional[Set[str]] = None
    ) -> Dict[str, List[str]]:
        """Determine affected and failing tests using bitmap schema.

        Args:
            files_checksums: Dict of {path: checksum} for Python files
            file_deps_shas: Dict of {path: fsha} for data files
            changed_packages: Set of changed external package names

        Returns:
            {"affected": [...], "failing": [...]}
        """
        if file_deps_shas is None:
            file_deps_shas = {}

        changed_ids = self.get_changed_file_ids(files_checksums)
        changed_ids |= self.get_changed_data_file_ids(file_deps_shas)

        affected = self.find_affected_tests_bitmap(changed_ids, changed_packages)
        failing = self.get_failing_tests_bitmap()

        return {"affected": affected, "failing": failing}
