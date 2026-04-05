"""Tests for database operations with bitmap schema."""
import os
import tempfile
import pytest
from ezmon.db import DB


class TestBitmapSchema:
    """Test the bitmap-based schema operations."""

    @pytest.fixture
    def temp_db(self):
        """Create a temporary database for testing."""
        with tempfile.NamedTemporaryFile(suffix='.testmondata', delete=False) as f:
            db_path = f.name
        database = DB(db_path)
        yield database
        database.con.close()
        os.unlink(db_path)

    def test_all_filenames_returns_tracked_files(self, temp_db):
        """all_filenames() should return files from the files table."""
        temp_db.get_or_create_file_id("src/foo.py", checksum=123)
        temp_db.get_or_create_file_id("tests/test_foo.py", checksum=456)

        filenames = temp_db.all_filenames()

        assert "src/foo.py" in filenames
        assert "tests/test_foo.py" in filenames

    def test_all_filenames_excludes_data_files(self, temp_db):
        """all_filenames() should only return python files, not data files."""
        temp_db.get_or_create_file_id("src/foo.py", checksum=123, file_type='python')
        temp_db.get_or_create_file_id("config.json", checksum=456, file_type='data')

        filenames = temp_db.all_filenames()

        assert "src/foo.py" in filenames
        assert "config.json" not in filenames

    def test_filenames_returns_tracked_files(self, temp_db):
        """filenames() should return files from the files table."""
        temp_db.get_or_create_file_id("src/bar.py", checksum=789)

        filenames = temp_db.filenames()

        assert "src/bar.py" in filenames

    def test_get_changed_file_ids_detects_checksum_change(self, temp_db):
        """get_changed_file_ids() should detect when checksums differ."""
        file_id = temp_db.get_or_create_file_id("src/foo.py", checksum=100)

        changed = temp_db.get_changed_file_ids({"src/foo.py": 200})

        assert file_id in changed

    def test_get_changed_file_ids_no_change(self, temp_db):
        """get_changed_file_ids() should not flag unchanged files."""
        temp_db.get_or_create_file_id("src/foo.py", checksum=100)

        changed = temp_db.get_changed_file_ids({"src/foo.py": 100})

        assert len(changed) == 0

    def test_get_changed_file_ids_new_file(self, temp_db):
        """get_changed_file_ids() should flag new files as changed."""
        changed = temp_db.get_changed_file_ids({"src/new_file.py": 100})

        assert len(changed) == 1
        filenames = temp_db.all_filenames()
        assert "src/new_file.py" in filenames

    def test_determine_tests_bitmap_finds_affected_tests(self, temp_db):
        """determine_tests_bitmap() should find tests affected by file changes."""
        run_id = temp_db.create_run("abc123", "packages", "3.9")

        file_id = temp_db.get_or_create_file_id("src/foo.py", checksum=100, run_id=run_id)
        test_id = temp_db.get_or_create_test_id(test_name="test_foo", run_id=run_id)

        from ezmon.bitmap_deps import TestDeps
        deps = TestDeps.from_file_ids(test_id, {file_id}, set())
        temp_db.save_test_deps(test_id, deps)

        result = temp_db.determine_tests_bitmap(
            files_checksums={"src/foo.py": 200},
        )

        assert "test_foo" in result["affected"]

    def test_determine_tests_bitmap_no_change_no_affected(self, temp_db):
        """determine_tests_bitmap() should not flag tests when no changes."""
        run_id = temp_db.create_run("abc123", "packages", "3.9")

        file_id = temp_db.get_or_create_file_id("src/foo.py", checksum=100, run_id=run_id)
        test_id = temp_db.get_or_create_test_id(test_name="test_foo", run_id=run_id)

        from ezmon.bitmap_deps import TestDeps
        deps = TestDeps.from_file_ids(test_id, {file_id}, set())
        temp_db.save_test_deps(test_id, deps)

        result = temp_db.determine_tests_bitmap(
            files_checksums={"src/foo.py": 100},
        )

        assert "test_foo" not in result["affected"]

    def test_all_test_executions_returns_from_tests_table(self, temp_db):
        """all_test_executions() should query the tests table."""
        run_id = temp_db.create_run("abc123", "packages", "3.9")

        temp_db.get_or_create_test_id(
            test_name="test_one", duration=1.5, failed=False, run_id=run_id
        )
        temp_db.get_or_create_test_id(
            test_name="test_two", duration=2.0, failed=True, run_id=run_id
        )

        tests = temp_db.all_test_executions()

        assert "test_one" in tests
        assert "test_two" in tests
        assert tests["test_one"]["duration"] == 1.5
        assert tests["test_two"]["failed"] is True

    def test_fetch_current_run_stats_returns_from_tests_table(self, temp_db):
        """fetch_current_run_stats() should query the tests table."""
        run_id = temp_db.create_run("abc123", "packages", "3.9")

        temp_db.get_or_create_test_id(
            test_name="test_one", duration=1.5, failed=False, run_id=run_id
        )
        temp_db.get_or_create_test_id(
            test_name="test_two", duration=2.5, failed=False, run_id=run_id
        )

        run_saved_time, run_all_time, run_saved_tests, run_all_tests = \
            temp_db.fetch_current_run_stats()

        assert run_saved_tests == 2
        assert run_all_tests == 2
        assert run_saved_time == 4.0
        assert run_all_time == 4.0

    def test_get_failing_tests_bitmap(self, temp_db):
        """get_failing_tests_bitmap() should return failed tests."""
        run_id = temp_db.create_run("abc123", "packages", "3.9")

        temp_db.get_or_create_test_id(
            test_name="test_pass", duration=1.0, failed=False, run_id=run_id
        )
        temp_db.get_or_create_test_id(
            test_name="test_fail", duration=1.0, failed=True, run_id=run_id
        )

        failing = temp_db.get_failing_tests_bitmap()

        assert "test_fail" in failing
        assert "test_pass" not in failing

    def test_delete_test(self, temp_db):
        """delete_test() should remove test and its dependencies."""
        run_id = temp_db.create_run("abc123", "packages", "3.9")

        file_id = temp_db.get_or_create_file_id("src/foo.py", checksum=100, run_id=run_id)
        test_id = temp_db.get_or_create_test_id(
            test_name="test_to_delete", run_id=run_id
        )

        from ezmon.bitmap_deps import TestDeps
        deps = TestDeps.from_file_ids(test_id, {file_id}, set())
        temp_db.save_test_deps(test_id, deps)

        tests = temp_db.all_test_executions()
        assert "test_to_delete" in tests

        temp_db.delete_test("test_to_delete")

        tests = temp_db.all_test_executions()
        assert "test_to_delete" not in tests

        deps = temp_db.get_test_deps(test_id)
        assert deps is None

    def test_get_or_create_file_id_updates_checksum(self, temp_db):
        """get_or_create_file_id() should update checksum on subsequent calls."""
        file_id1 = temp_db.get_or_create_file_id("src/foo.py", checksum=100)

        file_id2 = temp_db.get_or_create_file_id("src/foo.py", checksum=200)

        assert file_id1 == file_id2

        checksums = temp_db.get_file_checksums()
        assert checksums["src/foo.py"] == 200

    def test_update_file_checksum(self, temp_db):
        """update_file_checksum() should update the checksum."""
        temp_db.get_or_create_file_id("src/foo.py", checksum=100)

        temp_db.update_file_checksum("src/foo.py", 200)

        checksums = temp_db.get_file_checksums()
        assert checksums["src/foo.py"] == 200

    def test_get_file_id_map(self, temp_db):
        """get_file_id_map() should return path to ID mapping."""
        id1 = temp_db.get_or_create_file_id("src/foo.py", checksum=100)
        id2 = temp_db.get_or_create_file_id("src/bar.py", checksum=200)

        id_map = temp_db.get_file_id_map()

        assert id_map["src/foo.py"] == id1
        assert id_map["src/bar.py"] == id2


class TestRunManagement:
    """Test the new runs table management."""

    @pytest.fixture
    def temp_db(self):
        with tempfile.NamedTemporaryFile(suffix='.testmondata', delete=False) as f:
            db_path = f.name
        database = DB(db_path)
        yield database
        database.con.close()
        os.unlink(db_path)

    def test_create_run_returns_id(self, temp_db):
        run_id = temp_db.create_run("abc123", "numpy 1.0, pandas 2.0", "3.11.0")
        assert run_id is not None
        assert isinstance(run_id, int)

    def test_get_latest_run_commit_id(self, temp_db):
        temp_db.create_run("first_commit", "packages", "3.11.0")
        temp_db.create_run("second_commit", "packages", "3.11.0")
        assert temp_db.get_latest_run_commit_id() == "second_commit"

    def test_get_latest_run_commit_id_empty(self, temp_db):
        assert temp_db.get_latest_run_commit_id() is None

    def test_get_previous_run_info(self, temp_db):
        temp_db.create_run("abc123", "numpy 1.0", "3.11.0")
        info = temp_db.get_previous_run_info()
        assert info["commit_id"] == "abc123"
        assert info["packages"] == "numpy 1.0"
        assert info["python_version"] == "3.11.0"

    def test_get_previous_run_info_empty(self, temp_db):
        assert temp_db.get_previous_run_info() is None

    def test_finish_run(self, temp_db):
        run_id = temp_db.create_run("abc123", "packages", "3.11.0")
        temp_db.finish_run(run_id, duration=10.5, tests_selected=50,
                          tests_deselected=100, tests_all=150,
                          time_saved=30.0, time_all=45.0)
        row = temp_db.con.execute(
            "SELECT * FROM runs WHERE id = ?", (run_id,)
        ).fetchone()
        assert row["duration"] == 10.5
        assert row["tests_selected"] == 50
        assert row["tests_all"] == 150

    def test_run_id_on_files(self, temp_db):
        """Files should track which run last wrote them."""
        run_id = temp_db.create_run("abc123", "packages", "3.11.0")
        temp_db.get_or_create_file_id("src/foo.py", checksum=100, run_id=run_id)
        row = temp_db.con.execute(
            "SELECT run_id FROM files WHERE path = 'src/foo.py'"
        ).fetchone()
        assert row["run_id"] == run_id

    def test_run_id_on_tests(self, temp_db):
        """Tests should track which run last wrote them."""
        run_id = temp_db.create_run("abc123", "packages", "3.11.0")
        temp_db.get_or_create_test_id("test_foo", run_id=run_id)
        row = temp_db.con.execute(
            "SELECT run_id FROM tests WHERE name = 'test_foo'"
        ).fetchone()
        assert row["run_id"] == run_id


class TestDataFileDependencies:
    """Test data file dependency tracking."""

    @pytest.fixture
    def temp_db(self):
        with tempfile.NamedTemporaryFile(suffix='.testmondata', delete=False) as f:
            db_path = f.name
        database = DB(db_path)
        yield database
        database.con.close()
        os.unlink(db_path)

    def test_get_changed_data_file_ids_detects_fsha_change(self, temp_db):
        """get_changed_data_file_ids() should detect when fsha differs."""
        file_id = temp_db.get_or_create_file_id(
            "config.json", checksum=None, fsha="original_hash", file_type='data'
        )

        changed = temp_db.get_changed_data_file_ids({"config.json": "new_hash"})

        assert file_id in changed

    def test_get_changed_data_file_ids_no_change(self, temp_db):
        """get_changed_data_file_ids() should not flag unchanged files."""
        temp_db.get_or_create_file_id(
            "config.json", checksum=None, fsha="same_hash", file_type='data'
        )

        changed = temp_db.get_changed_data_file_ids({"config.json": "same_hash"})

        assert len(changed) == 0

    def test_determine_tests_bitmap_with_data_files(self, temp_db):
        """determine_tests_bitmap() should detect data file changes."""
        run_id = temp_db.create_run("abc123", "packages", "3.9")

        py_file_id = temp_db.get_or_create_file_id("src/foo.py", checksum=100, run_id=run_id)
        data_file_id = temp_db.get_or_create_file_id(
            "config.json", checksum=None, fsha="original", file_type='data', run_id=run_id
        )

        test_id = temp_db.get_or_create_test_id(test_name="test_config", run_id=run_id)

        from ezmon.bitmap_deps import TestDeps
        deps = TestDeps.from_file_ids(test_id, {py_file_id, data_file_id}, set())
        temp_db.save_test_deps(test_id, deps)

        result = temp_db.determine_tests_bitmap(
            files_checksums={"src/foo.py": 100},
            file_deps_shas={"config.json": "changed"},
        )

        assert "test_config" in result["affected"]


class TestExternalPackageDependencies:
    """Test external package dependency tracking."""

    @pytest.fixture
    def temp_db(self):
        with tempfile.NamedTemporaryFile(suffix='.testmondata', delete=False) as f:
            db_path = f.name
        database = DB(db_path)
        yield database
        database.con.close()
        os.unlink(db_path)

    def test_find_affected_tests_with_package_change(self, temp_db):
        """find_affected_tests_bitmap() should detect package changes."""
        run_id = temp_db.create_run("abc123", "packages", "3.9")

        file_id = temp_db.get_or_create_file_id("src/foo.py", checksum=100, run_id=run_id)
        test_id = temp_db.get_or_create_test_id(test_name="test_numpy", run_id=run_id)

        from ezmon.bitmap_deps import TestDeps
        deps = TestDeps.from_file_ids(test_id, {file_id}, {"numpy"})
        temp_db.save_test_deps(test_id, deps)

        affected = temp_db.find_affected_tests_bitmap(
            changed_file_ids=set(),
            changed_packages={"numpy"},
        )

        assert "test_numpy" in affected

    def test_find_affected_tests_unrelated_package_change(self, temp_db):
        """find_affected_tests_bitmap() should not flag unrelated package changes."""
        run_id = temp_db.create_run("abc123", "packages", "3.9")

        file_id = temp_db.get_or_create_file_id("src/foo.py", checksum=100, run_id=run_id)
        test_id = temp_db.get_or_create_test_id(test_name="test_numpy", run_id=run_id)

        from ezmon.bitmap_deps import TestDeps
        deps = TestDeps.from_file_ids(test_id, {file_id}, {"numpy"})
        temp_db.save_test_deps(test_id, deps)

        affected = temp_db.find_affected_tests_bitmap(
            changed_file_ids=set(),
            changed_packages={"pandas"},
        )

        assert "test_numpy" not in affected


class TestSchemaIntegrity:
    """Test the new 5-table schema."""

    @pytest.fixture
    def temp_db(self):
        with tempfile.NamedTemporaryFile(suffix='.testmondata', delete=False) as f:
            db_path = f.name
        database = DB(db_path)
        yield database
        database.con.close()
        os.unlink(db_path)

    def test_eight_tables_created(self, temp_db):
        """v20 schema: 5 core tables + 3 history tables = 8 user tables."""
        tables = temp_db.con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        table_names = sorted([row[0] for row in tables])
        assert table_names == [
            "files",
            "files_history",
            "metadata",
            "runs",
            "test_deps",
            "test_deps_history",
            "tests",
            "tests_failed_history",
        ]

    def test_metadata_uses_key_value(self, temp_db):
        """Metadata table should use key/value columns."""
        temp_db.write_attribute("test_key", "test_value")
        result = temp_db.fetch_attribute("test_key")
        assert result == "test_value"

    def test_data_version_is_20(self, temp_db):
        """Data version should be 20 (v20 added empty history tables)."""
        version = temp_db.con.execute("PRAGMA user_version").fetchone()[0]
        assert version == 20
