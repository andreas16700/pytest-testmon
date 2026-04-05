"""Tests for the database migration framework (db.check_data_version).

These tests verify the non-destructive migration contract:

1. A fresh empty DB initializes to DATA_VERSION via init_tables().
2. Matching versions are a no-op.
3. A DB with a stored version **newer** than DATA_VERSION raises
   IncompatibleDatabaseError and does NOT modify the file.
4. A DB with an older version but a **missing migration** in the chain
   raises IncompatibleDatabaseError and does NOT modify the file.
5. A migration that raises rolls back cleanly; the file is unchanged.
6. Error messages mention the datafile path so operators can diagnose.
7. A migration that succeeds is applied in place; existing data is
   preserved across the version bump.

The tests register dummy migrations via monkeypatch so we can exercise
the framework without adding a real schema bump (which belongs in the
versioning feature PR that follows this one).
"""
import os
import sqlite3
import tempfile

import pytest

from ezmon import db as db_module
from ezmon.db import DB, IncompatibleDatabaseError, DATA_VERSION


def _make_db_at_version(path: str, version: int) -> None:
    """Create a bare SQLite file with PRAGMA user_version = `version`.

    This does NOT call init_tables() — the goal is to simulate a file
    that claims to be at some schema version without depending on the
    current plugin's init_tables() behavior.
    """
    con = sqlite3.connect(path)
    con.execute(f"PRAGMA user_version = {version}")
    con.execute("CREATE TABLE marker (id INTEGER PRIMARY KEY, value TEXT)")
    con.execute("INSERT INTO marker (value) VALUES ('sentinel')")
    con.commit()
    con.close()


def _read_user_version(path: str) -> int:
    con = sqlite3.connect(path)
    try:
        return con.execute("PRAGMA user_version").fetchone()[0]
    finally:
        con.close()


def _read_marker(path: str):
    """Return marker rows if the table exists, else None."""
    con = sqlite3.connect(path)
    try:
        try:
            rows = con.execute("SELECT value FROM marker").fetchall()
            return [r[0] for r in rows]
        except sqlite3.OperationalError:
            return None
    finally:
        con.close()


@pytest.fixture
def tmp_db_path():
    fd, path = tempfile.mkstemp(suffix=".testmondata")
    os.close(fd)
    os.unlink(path)  # let DB create a fresh one
    yield path
    for suffix in ("", "-wal", "-shm"):
        try:
            os.unlink(path + suffix)
        except FileNotFoundError:
            pass


@pytest.fixture
def clean_migrations(monkeypatch):
    """Reset the MIGRATIONS registry for each test so they don't leak."""
    monkeypatch.setattr(db_module, "MIGRATIONS", {})
    yield db_module.MIGRATIONS


class TestFreshDatabase:
    def test_fresh_db_initializes_to_current_version(self, tmp_db_path, clean_migrations):
        """A newly created DB should run init_tables() and land on DATA_VERSION."""
        database = DB(tmp_db_path)
        assert database.file_created is True
        assert _read_user_version(tmp_db_path) == DATA_VERSION
        database.con.close()

    def test_zero_version_file_is_treated_as_fresh(self, tmp_db_path, clean_migrations):
        """A sqlite file with user_version = 0 and no tables is treated as fresh."""
        # Create an empty sqlite file with user_version = 0 (the default).
        con = sqlite3.connect(tmp_db_path)
        con.close()
        assert _read_user_version(tmp_db_path) == 0

        database = DB(tmp_db_path)
        assert database.file_created is True
        assert _read_user_version(tmp_db_path) == DATA_VERSION
        database.con.close()

    def test_zero_version_with_existing_tables_raises(
        self, tmp_db_path, clean_migrations
    ):
        """A file with user_version=0 but pre-existing tables is corrupt — refuse.

        Before this check was added, the code would fall through to
        init_tables() and crash with a raw sqlite3 'table X already exists'
        error. We want an explicit IncompatibleDatabaseError instead.
        """
        con = sqlite3.connect(tmp_db_path)
        con.execute("CREATE TABLE leftover (id INTEGER, data TEXT)")
        con.execute("INSERT INTO leftover VALUES (1, 'stale')")
        con.commit()
        con.close()
        # user_version is still 0 (never set), but there's a table
        assert _read_user_version(tmp_db_path) == 0

        with pytest.raises(IncompatibleDatabaseError) as excinfo:
            DB(tmp_db_path)

        # File is untouched
        assert os.path.exists(tmp_db_path)
        con = sqlite3.connect(tmp_db_path)
        try:
            rows = con.execute("SELECT data FROM leftover").fetchall()
            assert rows == [("stale",)]
        finally:
            con.close()
        # Error message is informative
        msg = str(excinfo.value)
        assert "leftover" in msg
        assert "user_version = 0" in msg
        assert tmp_db_path in msg
        assert "NOT been modified" in msg

    def test_zero_version_with_many_existing_tables_truncates_list(
        self, tmp_db_path, clean_migrations
    ):
        """Error message stays readable when the corrupt DB has many tables.

        List is truncated to 10 names plus "...and N more" so the
        IncompatibleDatabaseError doesn't become a multi-screen wall.
        """
        con = sqlite3.connect(tmp_db_path)
        for i in range(25):
            con.execute(f"CREATE TABLE t{i:02d} (id INTEGER)")
        con.commit()
        con.close()

        with pytest.raises(IncompatibleDatabaseError) as excinfo:
            DB(tmp_db_path)

        msg = str(excinfo.value)
        # Count is accurate
        assert "25 table(s)" in msg
        # First 10 names are present (alphabetical order)
        for i in range(10):
            assert f"t{i:02d}" in msg
        # Names past the limit are NOT present
        assert "t10" not in msg  # 11th alphabetically, should be truncated
        # Truncation marker
        assert "and 15 more" in msg


class TestMatchingVersion:
    def test_matching_version_is_noop(self, tmp_db_path, clean_migrations):
        """Opening a DB already at DATA_VERSION should not reinitialize."""
        # First open creates the DB
        database = DB(tmp_db_path)
        # Add some data via the real schema
        database.con.execute(
            "INSERT INTO files (path, file_type, checksum) VALUES (?, ?, ?)",
            ("src/foo.py", "python", 12345),
        )
        database.con.commit()
        database.close()

        # Reopen: should preserve the row and not recreate tables
        database = DB(tmp_db_path)
        assert database.file_created is False
        rows = database.con.execute(
            "SELECT path, checksum FROM files"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["path"] == "src/foo.py"
        assert rows[0]["checksum"] == 12345
        database.close()


class TestFutureVersion:
    def test_future_version_raises_without_deleting(self, tmp_db_path, clean_migrations):
        """A DB claiming a newer version must raise and leave the file intact."""
        future_version = DATA_VERSION + 99
        _make_db_at_version(tmp_db_path, future_version)

        with pytest.raises(IncompatibleDatabaseError) as excinfo:
            DB(tmp_db_path)

        # File still exists and still has the marker row
        assert os.path.exists(tmp_db_path)
        assert _read_user_version(tmp_db_path) == future_version
        assert _read_marker(tmp_db_path) == ["sentinel"]
        # Error message is useful
        assert tmp_db_path in str(excinfo.value)
        assert str(future_version) in str(excinfo.value)
        assert "NOT been modified" in str(excinfo.value)


class TestMissingMigration:
    def test_missing_migration_raises_without_deleting(
        self, tmp_db_path, clean_migrations, monkeypatch
    ):
        """If no migration bridges the stored version to the target, raise and preserve."""
        stored = DATA_VERSION - 2  # two hops away, no migrations registered
        _make_db_at_version(tmp_db_path, stored)

        with pytest.raises(IncompatibleDatabaseError) as excinfo:
            DB(tmp_db_path)

        assert os.path.exists(tmp_db_path)
        assert _read_user_version(tmp_db_path) == stored
        assert _read_marker(tmp_db_path) == ["sentinel"]
        assert tmp_db_path in str(excinfo.value)
        assert "no migration" in str(excinfo.value).lower()
        assert "NOT been modified" in str(excinfo.value)

    def test_missing_migration_error_is_actionable(
        self, tmp_db_path, clean_migrations
    ):
        """Operators hitting this error should be told how to recover.

        The error must (1) name the file, (2) mention deleting it as a
        recovery path, and (3) mention installing an older plugin version
        as the other recovery path.
        """
        stored = DATA_VERSION - 1
        _make_db_at_version(tmp_db_path, stored)

        with pytest.raises(IncompatibleDatabaseError) as excinfo:
            DB(tmp_db_path)

        msg = str(excinfo.value)
        assert tmp_db_path in msg
        assert "delete" in msg.lower()
        assert "pytest-ezmon" in msg.lower()


class TestSuccessfulMigration:
    def test_single_migration_preserves_data(
        self, tmp_db_path, clean_migrations, monkeypatch
    ):
        """A registered migration upgrades the DB in place, preserving all data."""
        stored = DATA_VERSION - 1
        _make_db_at_version(tmp_db_path, stored)

        calls = []

        def migrator(con):
            calls.append("migrated")
            con.execute("ALTER TABLE marker ADD COLUMN extra TEXT DEFAULT 'new'")

        monkeypatch.setitem(db_module.MIGRATIONS, stored, migrator)

        database = DB(tmp_db_path)
        assert database.file_created is False
        assert calls == ["migrated"]
        assert _read_user_version(tmp_db_path) == DATA_VERSION

        # Original marker row still present
        rows = database.con.execute("SELECT value, extra FROM marker").fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "sentinel"
        assert rows[0][1] == "new"
        database.close()

    def test_chained_migrations_run_in_order(
        self, tmp_db_path, clean_migrations, monkeypatch
    ):
        """Multi-hop migrations apply in ascending order."""
        stored = DATA_VERSION - 3
        _make_db_at_version(tmp_db_path, stored)

        calls = []

        def m1(con):
            calls.append(1)
            con.execute("CREATE TABLE step1 (id INTEGER)")

        def m2(con):
            calls.append(2)
            con.execute("CREATE TABLE step2 (id INTEGER)")

        def m3(con):
            calls.append(3)
            con.execute("CREATE TABLE step3 (id INTEGER)")

        monkeypatch.setitem(db_module.MIGRATIONS, stored, m1)
        monkeypatch.setitem(db_module.MIGRATIONS, stored + 1, m2)
        monkeypatch.setitem(db_module.MIGRATIONS, stored + 2, m3)

        database = DB(tmp_db_path)
        assert calls == [1, 2, 3]
        assert _read_user_version(tmp_db_path) == DATA_VERSION

        # All intermediate tables exist
        for table in ("step1", "step2", "step3", "marker"):
            rows = database.con.execute(
                f"SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchall()
            assert rows, f"table {table} missing"
        database.close()


class TestMigrationException:
    def test_exception_during_migration_rolls_back(
        self, tmp_db_path, clean_migrations, monkeypatch
    ):
        """A migration that raises must roll back; the DB stays at the old version."""
        stored = DATA_VERSION - 1
        _make_db_at_version(tmp_db_path, stored)

        def broken_migrator(con):
            con.execute("CREATE TABLE should_not_exist (id INTEGER)")
            raise RuntimeError("simulated migration failure")

        monkeypatch.setitem(db_module.MIGRATIONS, stored, broken_migrator)

        with pytest.raises(IncompatibleDatabaseError) as excinfo:
            DB(tmp_db_path)

        # Version unchanged on disk
        assert _read_user_version(tmp_db_path) == stored
        # Marker still intact
        assert _read_marker(tmp_db_path) == ["sentinel"]
        # The partially-created table did NOT persist (rollback worked)
        con = sqlite3.connect(tmp_db_path)
        try:
            rows = con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name='should_not_exist'"
            ).fetchall()
            assert rows == [], "rollback failed — should_not_exist table persisted"
        finally:
            con.close()
        # Error message wraps the original exception
        assert "RuntimeError" in str(excinfo.value)
        assert "simulated migration failure" in str(excinfo.value)
        assert tmp_db_path in str(excinfo.value)

    def test_exception_in_later_migration_rolls_back_all(
        self, tmp_db_path, clean_migrations, monkeypatch
    ):
        """If the 2nd of 2 migrations fails, neither takes effect."""
        stored = DATA_VERSION - 2

        _make_db_at_version(tmp_db_path, stored)

        def m1(con):
            con.execute("CREATE TABLE step1 (id INTEGER)")

        def m2_broken(con):
            con.execute("CREATE TABLE step2 (id INTEGER)")
            raise RuntimeError("boom")

        monkeypatch.setitem(db_module.MIGRATIONS, stored, m1)
        monkeypatch.setitem(db_module.MIGRATIONS, stored + 1, m2_broken)

        with pytest.raises(IncompatibleDatabaseError):
            DB(tmp_db_path)

        assert _read_user_version(tmp_db_path) == stored
        assert _read_marker(tmp_db_path) == ["sentinel"]

        # Neither step1 nor step2 should exist — the whole txn rolled back
        con = sqlite3.connect(tmp_db_path)
        try:
            for table in ("step1", "step2"):
                rows = con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                    (table,),
                ).fetchall()
                assert rows == [], f"rollback failed — {table} persisted"
        finally:
            con.close()
