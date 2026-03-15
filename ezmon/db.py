import json
import os
import sqlite3

from functools import lru_cache
from typing import Dict, List, Optional, Set

from ezmon.common import TestExecutions
from ezmon.common import get_logger
from ezmon.bitmap_deps import TestDeps, find_affected_tests


DATA_VERSION = 18  # Bumped for Roaring bitmap dependency storage


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
        self.con.executescript(self._local_temp_tables_statement())
        # Ensure run_infos table exists for older DB files without recreating the DB file.
        # Using IF NOT EXISTS makes this safe to run against an existing DB.
        self.con.executescript(self._create_run_infos_statement())
        self._ensure_run_infos_schema()
        self._ensure_run_infos_unique_index()
        self._ensure_tests_schema()
        # Dependency graph is no longer stored; keep schema lean.

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

    def _test_execution_fk_column(self) -> str:
        return "environment_id"

    def _test_execution_fk_table(self) -> str:
        return "environment"

    def update_mtimes(self, new_mtimes):
        with self.con as con:
            con.executemany(
                "UPDATE file_fp SET mtime=?, fsha=? WHERE id = ?", new_mtimes
            )

    def finish_execution(
        self, exec_id, duration=None, select=True, commit_id: Optional[str] = None
    ):  # pylint: disable=unused-argument
        self.update_saving_stats(exec_id, select, commit_id=commit_id)
        self.get_or_create_file_id.cache_clear()
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

    def fetch_current_run_stats(self, exec_id):
        """Fetch run statistics from the tests table (bitmap schema)."""
        with self.con as con:
            cursor = con.cursor()
            # Count all tests for this environment
            run_saved_tests, run_saved_time = cursor.execute(
                """SELECT count(*), sum(duration) FROM tests
                   WHERE environment_id = ?""",
                (exec_id,),
            ).fetchone()
            run_all_tests, run_all_time = cursor.execute(
                """SELECT count(*), sum(duration) FROM tests
                   WHERE environment_id = ?""",
                (exec_id,),
            ).fetchone()

        return (
            run_saved_time,
            run_all_time,
            run_saved_tests,
            run_all_tests,
        )

    def update_saving_stats(self, exec_id, select, commit_id: Optional[str] = None):
        (
            run_saved_time,
            run_all_time,
            run_saved_tests,
            run_all_tests,
        ) = self.fetch_current_run_stats(exec_id)

        attribute_prefix = "" if select else "potential_"
        self.write_attribute(
        f"{attribute_prefix}run_time_saved",
        run_saved_time,
        exec_id=exec_id
        )

        self.write_increment_run_id_attribute()
        with self.con as con:
            cursor = con.cursor()
            cursor.execute(
                """SELECT MAX(id) FROM run_uid"""
            )
            row = cursor.fetchone()
            run_uid = row[0] if row and row[0] is not None else None
        self.write_run_info_attribute(
            exec_id=exec_id,
            run_saved_time=run_saved_time,
            run_all_time=run_all_time,
            run_saved_tests=run_saved_tests,
            run_all_tests=run_all_tests,
            run_uid=run_uid,
            commit_id=commit_id,
        )

        self.write_test_info_attribute(run_uid)
        
        self.write_file_fp_infos(run_uid)
        
        self.write_test_exec_file_fp_infos(run_uid)

        self.increment_attributes(
            {
                f"{attribute_prefix}time_saved": run_saved_time,
                f"{attribute_prefix}time_all": run_all_time,
                f"{attribute_prefix}tests_saved": run_saved_tests,
                f"{attribute_prefix}tests_all": run_all_tests,
            },
            exec_id=None,
        )

    def fetch_saving_stats(self, exec_id, select):
        (
            run_saved_time,
            run_all_time,
            run_saved_tests,
            run_all_tests,
        ) = self.fetch_current_run_stats(exec_id)
        attribute_prefix = "" if select else "potential_"
        total_saved_time = self.fetch_attribute(
            attribute=f"{attribute_prefix}time_saved", default=0, exec_id=None
        )
        total_all_time = self.fetch_attribute(
            attribute=f"{attribute_prefix}time_all", default=0, exec_id=None
        )
        total_saved_tests = self.fetch_attribute(
            attribute=f"{attribute_prefix}tests_saved", default=0, exec_id=None
        )
        total_all_tests = self.fetch_attribute(
            attribute=f"{attribute_prefix}tests_all", default=0, exec_id=None
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


    def get_latest_run_commit_id(self) -> Optional[str]:
        """Return the commit_id for the latest run_uid."""
        row = self.con.execute("SELECT MAX(run_uid) FROM run_infos").fetchone()
        if not row or row[0] is None:
            return None
        commit_row = self.con.execute(
            "SELECT commit_id FROM run_infos WHERE run_uid = ?",
            (row[0],),
        ).fetchone()
        if not commit_row:
            return None
        return commit_row[0]

    def write_attribute(self, attribute, data, exec_id=None):
        dataid = f"{exec_id}:{attribute}"
        with self.con as con:
            con.execute(
                "INSERT OR REPLACE INTO metadata VALUES (?, ?)",
                [dataid, json.dumps(data)],
            )
    def write_run_info_attribute(
        self,
        exec_id,
        run_saved_time,
        run_all_time,
        run_saved_tests,
        run_all_tests,
        run_uid,
        commit_id: Optional[str] = None,
    ):
        with self.con as con:
            con.execute(
                """INSERT OR REPLACE INTO run_infos 
                ( run_time_saved, run_time_all, tests_saved, tests_all, run_uid, commit_id)
                VALUES ( ?, ?, ?, ?, ?, ?)""",
                (run_saved_time, run_all_time, run_saved_tests, run_all_tests, run_uid, commit_id),
            )
            
    # Historical Data Insert

    def write_test_info_attribute(self, run_uid):
        """Copy test info to historical table from the tests table (bitmap schema)."""
        with self.con as con:
            con.execute(
                """
                INSERT INTO test_infos (test_execution_id, test_name, duration, failed, forced, run_uid)
                SELECT id, name, duration, failed, NULL, ?
                FROM tests
                """,
                (run_uid,)
            )
    def write_increment_run_id_attribute(self):
        with self.con as con:
            con.execute(
                """
                INSERT INTO run_uid DEFAULT VALUES;
                """
            )
    
    def write_test_exec_file_fp_infos(self, run_uid):
        """Copy test-file dependency info to historical table from bitmap schema.

        With the new bitmap schema, test dependencies are stored as compressed
        bitmaps in test_deps, not as individual rows. This method now extracts
        the file dependencies from the bitmap and stores them in the historical
        table for reporting purposes.
        """
        # The new bitmap schema stores dependencies differently, so we need
        # to extract them. For now, we skip this if there's no data in the old table.
        # The historical data will be populated from the new tables if needed.
        pass
    def write_file_fp_infos(self, run_uid):
        """Copy file info to historical table from the files table (bitmap schema)."""
        with self.con as con:
            con.execute(
                """
                INSERT INTO file_fp_infos (fingerprint_id, filename, file_checksum, mtime, fsha, run_uid)
                SELECT id, path, checksum, NULL, fsha, ?
                FROM files
                """,
                (run_uid,)
            )
      
    
    def fetch_attribute(self, attribute, default=None, exec_id=None):
        cursor = self.con.execute(
            "SELECT data FROM metadata WHERE dataid=?",
            [f"{exec_id}:{attribute}"],
        )
        result = cursor.fetchone()
        if result:
            return json.loads(result[0])  # zlib.decompress(result[0]).decode('utf-8)'))
        return default

    def increment_attributes(self, attributes_to_increment, exec_id=None):
        def sum_with_none(*to_sum):
            return sum(filter(None, to_sum))

        for attribute_name in attributes_to_increment:
            dataid = f"{exec_id}:{attribute_name}"
            old_value = self.fetch_attribute(
                attribute=attribute_name, default=0, exec_id=exec_id
            )
            with self.con as con:
                con.execute(
                    "INSERT OR REPLACE INTO metadata VALUES (?, ?)",
                    [
                        dataid,
                        sum_with_none(
                            old_value, attributes_to_increment[attribute_name]
                        ),
                    ],
                )

    def _create_metadata_statement(self) -> str:
        return """CREATE TABLE metadata (dataid TEXT PRIMARY KEY, data TEXT);"""

    def create_run_uid_statement(self) ->str:
           return """CREATE TABLE IF NOT EXISTS run_uid (
            id INTEGER PRIMARY KEY,
            repo_run_id INTEGER NULL,
            create_date TEXT DEFAULT (datetime('now'))
        );"""
    
    def _create_run_infos_statement(self) -> str:
        return """CREATE TABLE IF NOT EXISTS run_infos (
            run_time_saved REAL,
            run_time_all REAL,
            tests_saved INTEGER,
            tests_all INTEGER ,
            run_uid INTEGER,
            commit_id TEXT,
            FOREIGN KEY(run_uid) REFERENCES run_uid(id)
            
        );"""
        
        
    
    def _create_test_infos_statement(self) ->str:
          return f"""
                CREATE TABLE IF NOT EXISTS test_infos (
                id INTEGER PRIMARY KEY ASC,
                test_execution_id INTEGER,
                test_name TEXT,
                duration FLOAT,
                failed BIT,
                forced BIT,
                run_uid INTEGER NULL,
                FOREIGN KEY(run_uid) REFERENCES run_uid(id));
                
            """    
            
            
    def _create__file_fp_infos_statement(self) -> str:
        return """
            CREATE TABLE IF NOT EXISTS file_fp_infos (
                id INTEGER PRIMARY KEY,
                fingerprint_id INTEGER,
                filename TEXT,
                file_checksum INTEGER,
                mtime FLOAT,
                fsha TEXT,
                run_uid INTEGER NULL,
                FOREIGN KEY(run_uid) REFERENCES run_uid(id)
            );
        """
          
    def _create_test_execution_file_fp_infos_statement(self) -> str:
        return """
            CREATE TABLE IF NOT EXISTS test_execution_file_fp_infos (
                id INTEGER PRIMARY KEY,
                test_execution_id INTEGER,
                fingerprint_id INTEGER,
                run_uid INTEGER NULL,
                FOREIGN KEY(run_uid) REFERENCES run_uid(id)
            );
        """

  
            
         
    def _create_environment_statement(self) -> str:
        return """
                CREATE TABLE environment (
                id INTEGER PRIMARY KEY ASC,
                environment_name TEXT,
                system_packages TEXT,
                python_version TEXT,
                UNIQUE (environment_name, system_packages, python_version)
            );"""

    def _create_test_execution_statement(self) -> str:  # pylint: disable=invalid-name
        return f"""
                CREATE TABLE test_execution (
                id INTEGER PRIMARY KEY ASC,
                {self._test_execution_fk_column()} INTEGER,
                test_name TEXT,
                duration FLOAT,
                failed BIT,
                forced BIT,
                FOREIGN KEY({self._test_execution_fk_column()}) REFERENCES {self._test_execution_fk_table()}(id) ON DELETE CASCADE);
                CREATE INDEX test_execution_fk_name ON test_execution ({self._test_execution_fk_column()}, test_name);
            """

    def _create_temp_tables_statement(self) -> str:
        return ""

    def _local_temp_tables_statement(self) -> str:
        return """
                CREATE TEMPORARY TABLE changed_files_fshas (exec_id INTEGER, filename TEXT, fsha TEXT);
                CREATE INDEX changed_files_fshas_mcall ON changed_files_fshas (exec_id, filename, fsha);

                CREATE TEMPORARY TABLE changed_files_checksums (exec_id INTEGER, filename TEXT, file_checksum INTEGER);
                CREATE INDEX changed_files_checksums_eid ON changed_files_checksums (exec_id);
        """

    def _create_file_fp_statement(self) -> str:
        return """
            CREATE TABLE file_fp
            (
                id INTEGER PRIMARY KEY,
                filename TEXT,
                file_checksum INTEGER,
                mtime FLOAT,
                fsha TEXT,
                UNIQUE (filename, fsha, file_checksum)
            );"""

    def _create_test_execution_ffp_statement(  # pylint: disable=invalid-name
        self,
    ) -> str:
        return """
            CREATE TABLE test_execution_file_fp (
                test_execution_id INTEGER,
                fingerprint_id INTEGER,
                FOREIGN KEY(test_execution_id) REFERENCES test_execution(id) ON DELETE CASCADE,
                FOREIGN KEY(fingerprint_id) REFERENCES file_fp(id)
            );
            CREATE INDEX test_execution_file_fp_both ON test_execution_file_fp (test_execution_id, fingerprint_id);
            -- the following table stores the same data coarsely, but is used for faster queries
            CREATE TABLE suite_execution_file_fsha (
                suite_execution_id INTEGER,
                filename TEXT,
                fsha text,
                FOREIGN KEY(suite_execution_id) REFERENCES suite_execution(id) ON DELETE CASCADE
                );
                CREATE UNIQUE INDEX sefch_suite_id_filename_sha ON suite_execution_file_fsha(suite_execution_id, filename, fsha);
            """
            
    def _create_file_dependency_statement(self) -> str:
        """Table for tracking non-Python file dependencies (JSON, YAML, etc.)."""
        return """
            CREATE TABLE IF NOT EXISTS file_dependency (
                id INTEGER PRIMARY KEY,
                filename TEXT NOT NULL,
                sha TEXT NOT NULL,
                UNIQUE (filename, sha)
            );
            CREATE TABLE IF NOT EXISTS test_execution_file_dependency (
                test_execution_id INTEGER,
                file_dependency_id INTEGER,
                FOREIGN KEY(test_execution_id) REFERENCES test_execution(id) ON DELETE CASCADE,
                FOREIGN KEY(file_dependency_id) REFERENCES file_dependency(id)
            );
            CREATE INDEX IF NOT EXISTS tefd_both ON test_execution_file_dependency (test_execution_id, file_dependency_id);
        """

    def _create_external_dependency_statement(self) -> str:
        """Table for tracking external package dependencies per test.

        Stores which external packages each test uses, enabling granular
        invalidation when specific packages change (instead of re-running
        all tests when any package changes).
        """
        return """
            CREATE TABLE IF NOT EXISTS test_external_dependency (
                id INTEGER PRIMARY KEY,
                test_execution_id INTEGER,
                package_name TEXT NOT NULL,
                package_version TEXT,
                FOREIGN KEY(test_execution_id) REFERENCES test_execution(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS ted_te_id ON test_external_dependency (test_execution_id);
            CREATE INDEX IF NOT EXISTS ted_pkg_name ON test_external_dependency (package_name);
        """


    def _create_files_table_statement(self) -> str:
        """Table for unified file registry with stable integer IDs.

        This is the new simplified schema where all tracked files (Python and non-Python)
        get a stable integer ID for efficient Roaring bitmap operations.
        """
        return """
            CREATE TABLE IF NOT EXISTS files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                path TEXT NOT NULL UNIQUE,
                checksum INTEGER,
                fsha TEXT,
                file_type TEXT DEFAULT 'python' CHECK (file_type IN ('python', 'data'))
            );
            CREATE INDEX IF NOT EXISTS files_path ON files (path);
            CREATE INDEX IF NOT EXISTS files_checksum ON files (checksum);
        """

    def _create_tests_table_statement(self) -> str:
        """Table for simplified test storage."""
        return """
            CREATE TABLE IF NOT EXISTS tests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                environment_id INTEGER,
                name TEXT NOT NULL,
                test_file TEXT,
                duration REAL,
                failed INTEGER DEFAULT 0,
                UNIQUE (environment_id, name),
                FOREIGN KEY(environment_id) REFERENCES environment(id)
            );
            CREATE INDEX IF NOT EXISTS tests_env_name ON tests (environment_id, name);
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
            + self._create_environment_statement()
            + self._create_test_execution_statement()
            + self._create_temp_tables_statement()
            + self._create_file_fp_statement()
            + self._create_test_execution_ffp_statement()
            + self.create_run_uid_statement()
            + self._create_run_infos_statement()
            + self._create_test_infos_statement()
            + self._create__file_fp_infos_statement()
            + self._create_test_execution_file_fp_infos_statement()
            + self._create_file_dependency_statement()
            + self._create_external_dependency_statement()
            # New simplified schema tables
            + self._create_files_table_statement()
            + self._create_tests_table_statement()
            + self._create_test_deps_table_statement()
        )

        connection.execute(f"PRAGMA user_version = {self.version_compatibility()}")

    def _ensure_run_infos_schema(self) -> None:
        """Ensure run_infos has commit_id column for existing DBs."""
        cursor = self.con.execute("PRAGMA table_info(run_infos)")
        columns = {row["name"] for row in cursor.fetchall()}
        if "commit_id" not in columns:
            self.con.execute("ALTER TABLE run_infos ADD COLUMN commit_id TEXT")

    def _ensure_run_infos_unique_index(self) -> None:
        """Ensure run_infos has a unique index on run_uid."""
        try:
            self.con.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS run_infos_run_uid_unique ON run_infos(run_uid)"
            )
        except sqlite3.IntegrityError as exc:
            self._logger.warning(
                "Could not create unique index on run_infos(run_uid): %s", exc
            )

    def _ensure_tests_schema(self) -> None:
        """Ensure tests table has test_file column for existing DBs."""
        cursor = self.con.execute("PRAGMA table_info(tests)")
        columns = {row["name"] for row in cursor.fetchall()}
        if "test_file" not in columns:
            self.con.execute("ALTER TABLE tests ADD COLUMN test_file TEXT")

    def fetch_unknown_files(
        self, files_fshas, exec_id, restrict_to_known: bool = False
    ) -> []:  # exec_id is environment_id in this module
        with self.con as con:
            con.execute("DELETE FROM changed_files_fshas WHERE exec_id = ?", (exec_id,))
            con.executemany(
                "INSERT INTO changed_files_fshas VALUES (?, ?, ?)",
                [(exec_id, file, fsha) for file, fsha in files_fshas.items()],
            )
            return self._fetch_unknown_files_from_one_v(
                con, exec_id, exec_id, restrict_to_known=restrict_to_known
            )

    def _fetch_unknown_files_from_one_v(self, con, exec_id, files_shas_id, restrict_to_known: bool = False):
        """Find files whose fsha is not in the current files_fshas.

        Uses the new files table (bitmap schema).
        """
        result = []
        if restrict_to_known:
            query = """
                SELECT DISTINCT f.path
                FROM files f
                LEFT OUTER JOIN changed_files_fshas chff
                ON f.path = chff.filename AND f.fsha = chff.fsha AND chff.exec_id = :files_shas_id
                WHERE f.file_type = 'python'
                  AND f.path IN (SELECT filename FROM changed_files_fshas WHERE exec_id = :files_shas_id)
                  AND (f.fsha IS NULL OR chff.fsha IS NULL)
            """
        else:
            query = """
                SELECT DISTINCT f.path
                FROM files f
                LEFT OUTER JOIN changed_files_fshas chff
                ON f.path = chff.filename AND f.fsha = chff.fsha AND chff.exec_id = :files_shas_id
                WHERE f.file_type = 'python' AND (f.fsha IS NULL OR chff.fsha IS NULL)
            """
        for row in con.execute(
            query,
            {"files_shas_id": files_shas_id, "exec_id": exec_id},
        ):
            result.append(row["path"])
        return result

    def delete_filenames(self, con):
        con.execute("DELETE FROM changed_files_checksums")

    def get_file_dependency_filenames(self, exec_id):
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

    def delete_test_executions(self, test_names, exec_id):
        """Delete tests from the bitmap schema.

        Removes tests and their dependencies from the new tests/test_deps tables.
        """
        for test_name in test_names:
            self.delete_test(exec_id, test_name)

    def all_test_executions(self, exec_id):
        """Get all tests for an environment with their metadata.

        Uses the new tests table (bitmap schema).
        """
        return {
            row["name"]: {"duration": row["duration"], "failed": bool(row["failed"]), "forced": None}
            for row in self.con.execute(
                """
                SELECT name, duration, failed
                FROM tests
                WHERE environment_id = ?
                """,
                (exec_id,),
            )
        }

    def filenames(self, exec_id):
        """Get all Python filenames tracked for an environment.

        Uses the new files table (bitmap schema).
        """
        cursor = self.con.execute(
            """
            SELECT DISTINCT path
            FROM files
            WHERE file_type = 'python'
            """,
        )
        return [row[0] for row in cursor]

    # TODO unify with filenames? Restrict not to go into ancient history, but not miss combinations?
    def all_filenames(self):
        """Get all tracked filenames from the files table."""
        cursor = self.con.execute(
            """SELECT DISTINCT path FROM files WHERE file_type = 'python'"""
        )
        return [row[0] for row in cursor]

    def fetch_or_create_environment(
        self, environment_name, system_packages, python_version
    ):
        """
        Fetch or create an environment, with granular package change tracking.

        Instead of deleting all test data when packages change (old behavior),
        we now:
        1. Track which specific packages changed
        2. Update the environment record in-place
        3. Return the set of changed packages for selective invalidation

        Returns:
            (environment_id, changed_packages, old_packages, old_python)
            where changed_packages is a set of package names that changed.
        """
        from ezmon.common import compute_changed_packages

        with self.con as con:
            con.execute("BEGIN IMMEDIATE TRANSACTION")
            cursor = con.cursor()
            environment = cursor.execute(
                """
                SELECT
                id, environment_name, system_packages, python_version
                FROM environment
                WHERE environment_name = ?
                ORDER BY id DESC
                """,
                (environment_name,),
            ).fetchone()

            changed_packages = set()

            old_packages = ""
            old_python = ""
            if not environment:
                # New environment - no packages changed (first run)
                try:
                    cursor.execute(
                        """
                        INSERT INTO environment (environment_name, system_packages, python_version)
                        VALUES (?, ?, ?)
                        """,
                        (environment_name, system_packages, python_version),
                    )
                    environment_id = cursor.lastrowid
                except sqlite3.IntegrityError:
                    environment = con.execute(
                        """
                        SELECT
                        id as id, environment_name as name, system_packages as packages
                        FROM environment
                        WHERE environment_name = ?
                        """,
                        (environment_name,),
                    ).fetchone()
                    environment_id = environment["id"]
            else:
                environment_id = environment["id"]
                old_packages = environment["system_packages"] or ""
                old_python = environment["python_version"] or ""

                # Python version change still requires full re-run
                if old_python != python_version:
                    # Return special marker indicating all tests affected
                    changed_packages = {"__python_version_changed__"}
                elif old_packages != system_packages:
                    # Compute which specific packages changed
                    changed_packages = compute_changed_packages(old_packages, system_packages)

                # Update the environment record (don't delete and recreate)
                if old_packages != system_packages or old_python != python_version:
                    cursor.execute(
                        """
                        UPDATE environment
                        SET system_packages = ?, python_version = ?
                        WHERE id = ?
                        """,
                        (system_packages, python_version, environment_id),
                    )

            return environment_id, changed_packages, old_packages, old_python

    def initiate_execution(  # pylint: disable= R0913 W0613
        self,
        environment_name: str,
        system_packages: str,
        python_version: str,
        execution_metadata: dict,  # pylint: disable=unused-argument
    ) -> [int, list]:  # exec_id  # changed_file_data  # future_string2
        exec_id, changed_packages, old_packages, old_python = self.fetch_or_create_environment(
            environment_name, system_packages, python_version
        )
        return {
            "exec_id": exec_id,
            "filenames": self.all_filenames(),
            "changed_packages": changed_packages,  # Set of changed package names
            "previous_packages": old_packages,
            "previous_python": old_python,
            "current_packages": system_packages,
            "current_python": python_version,
            # Legacy: packages_changed is True if any packages changed
            "packages_changed": bool(changed_packages),
        }

    # ==========================================================================
    # New Roaring Bitmap-based methods for simplified dependency storage
    # ==========================================================================

    @lru_cache(maxsize=10000)
    def get_or_create_file_id(self, path: str, checksum: int = None,
                              fsha: str = None, file_type: str = 'python') -> int:
        """Get or create a stable file ID for a given path.

        This is the core method for the new simplified schema. Each file gets
        a unique integer ID that can be stored in Roaring bitmaps.

        Args:
            path: Relative file path
            checksum: AST checksum (Python) or content hash (data files)
            fsha: Git blob SHA for fast change detection
            file_type: 'python' or 'data'

        Returns:
            Integer file ID
        """
        cursor = self.con.cursor()

        # Try to get existing file ID
        row = cursor.execute(
            "SELECT id FROM files WHERE path = ?", (path,)
        ).fetchone()

        if row:
            file_id = row[0]
            # Update checksum/fsha if provided
            if checksum is not None or fsha is not None:
                cursor.execute(
                    """UPDATE files SET checksum = COALESCE(?, checksum),
                       fsha = COALESCE(?, fsha) WHERE id = ?""",
                    (checksum, fsha, file_id)
                )
            return file_id

        # Create new file record
        cursor.execute(
            """INSERT INTO files (path, checksum, fsha, file_type)
               VALUES (?, ?, ?, ?)""",
            (path, checksum, fsha, file_type)
        )
        return cursor.lastrowid

    def get_file_id_map(self, exec_id: int = None) -> Dict[str, int]:
        """Get a mapping of file paths to their IDs.

        Args:
            exec_id: Optional environment ID to filter files (not used yet)

        Returns:
            Dict mapping file path to integer ID
        """
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
        # Clear cache since file changed
        self.get_or_create_file_id.cache_clear()

    def get_or_create_test_id(
        self,
        exec_id: int,
        test_name: str,
        duration: float = None,
        failed: bool = False,
        test_file: Optional[str] = None,
    ) -> int:
        """Get or create a test ID for a given test name.

        Args:
            exec_id: Environment ID
            test_name: Full test node ID
            duration: Test duration in seconds
            failed: Whether the test failed

        Returns:
            Integer test ID
        """
        cursor = self.con.cursor()

        # Try to get existing test ID
        row = cursor.execute(
            "SELECT id FROM tests WHERE environment_id = ? AND name = ?",
            (exec_id, test_name)
        ).fetchone()

        if row:
            test_id = row[0]
            # Update duration/failed if provided
            cursor.execute(
                """UPDATE tests SET duration = COALESCE(?, duration),
                   failed = ?, test_file = COALESCE(?, test_file) WHERE id = ?""",
                (duration, 1 if failed else 0, test_file, test_id)
            )
            return test_id

        # Create new test record
        cursor.execute(
            """INSERT INTO tests (environment_id, name, test_file, duration, failed)
               VALUES (?, ?, ?, ?, ?)""",
            (exec_id, test_name, test_file, duration, 1 if failed else 0)
        )
        return cursor.lastrowid

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

    def get_all_test_deps(self, exec_id: int) -> List[TestDeps]:
        """Get all test dependencies for an environment.

        Args:
            exec_id: Environment ID

        Returns:
            List of TestDeps objects
        """
        cursor = self.con.execute(
            """SELECT t.id, td.file_bitmap, td.external_packages
               FROM tests t
               JOIN test_deps td ON t.id = td.test_id
               WHERE t.environment_id = ?""",
            (exec_id,)
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
        exec_id: int,
        changed_file_ids: Set[int],
        changed_packages: Optional[Set[str]] = None
    ) -> List[str]:
        """Find tests affected by file or package changes using bitmap intersection.

        This is the new fast path for determining affected tests.

        Args:
            exec_id: Environment ID
            changed_file_ids: Set of file IDs that changed
            changed_packages: Set of package names that changed

        Returns:
            List of affected test names
        """
        all_deps = self.get_all_test_deps(exec_id)
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

    def get_tests_for_env(self, exec_id: int) -> Dict[str, Dict]:
        """Get all tests for an environment with their metadata.

        Args:
            exec_id: Environment ID

        Returns:
            Dict mapping test name to {duration, failed} dict
        """
        cursor = self.con.execute(
            """SELECT name, duration, failed FROM tests WHERE environment_id = ?""",
            (exec_id,)
        )
        return {
            row["name"]: {"duration": row["duration"], "failed": bool(row["failed"])}
            for row in cursor
        }

    def get_test_files_for_tests(self, exec_id: int, test_names: Set[str]) -> Set[str]:
        """Get test files for a set of test names."""
        if not test_names:
            return set()
        placeholders = ",".join("?" * len(test_names))
        cursor = self.con.execute(
            f"""SELECT DISTINCT test_file FROM tests
                WHERE environment_id = ? AND name IN ({placeholders})""",
            (exec_id, *sorted(test_names)),
        )
        return {row["test_file"] for row in cursor if row["test_file"]}

    def get_all_test_files(self, exec_id: int) -> Set[str]:
        """Get all known test files for an environment."""
        cursor = self.con.execute(
            """SELECT DISTINCT test_file FROM tests WHERE environment_id = ?""",
            (exec_id,),
        )
        return {row["test_file"] for row in cursor if row["test_file"]}

    def delete_test(self, exec_id: int, test_name: str) -> None:
        """Delete a test and its dependencies.

        Args:
            exec_id: Environment ID
            test_name: Test name to delete
        """
        # Get test ID
        row = self.con.execute(
            "SELECT id FROM tests WHERE environment_id = ? AND name = ?",
            (exec_id, test_name)
        ).fetchone()

        if row:
            test_id = row[0]
            with self.con as con:
                # Delete deps first (foreign key)
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

    def get_failing_tests_bitmap(self, exec_id: int) -> List[str]:
        """Get names of tests that failed in their last run.

        Args:
            exec_id: Environment ID

        Returns:
            List of failing test names
        """
        cursor = self.con.execute(
            "SELECT name FROM tests WHERE environment_id = ? AND failed = 1",
            (exec_id,)
        )
        return [row["name"] for row in cursor]

    def determine_tests_bitmap(
        self,
        exec_id: int,
        files_checksums: Dict[str, int],
        file_deps_shas: Dict[str, str] = None,
        changed_packages: Optional[Set[str]] = None
    ) -> Dict[str, List[str]]:
        """Determine affected and failing tests using bitmap schema.

        This replaces determine_tests() with bitmap-based queries.

        Args:
            exec_id: Environment ID
            files_checksums: Dict of {path: checksum} for Python files
            file_deps_shas: Dict of {path: fsha} for data files
            changed_packages: Set of changed external package names

        Returns:
            {"affected": [...], "failing": [...]}
        """
        if file_deps_shas is None:
            file_deps_shas = {}

        # Get changed Python file IDs
        changed_ids = self.get_changed_file_ids(files_checksums)

        # Get changed data file IDs
        changed_ids |= self.get_changed_data_file_ids(file_deps_shas)

        # Find affected tests using bitmap intersection
        affected = self.find_affected_tests_bitmap(
            exec_id, changed_ids, changed_packages
        )

        # Get failing tests
        failing = self.get_failing_tests_bitmap(exec_id)

        return {"affected": affected, "failing": failing}
