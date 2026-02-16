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
        db = DB(db_path)
        yield db
        db.con.close()
        os.unlink(db_path)

    def test_all_filenames_returns_tracked_files(self, temp_db):
        """all_filenames() should return files from the files table."""
        # Add files to the new schema
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

        filenames = temp_db.filenames(exec_id=1)

        assert "src/bar.py" in filenames

    def test_get_changed_file_ids_detects_checksum_change(self, temp_db):
        """get_changed_file_ids() should detect when checksums differ."""
        # Create file with original checksum
        file_id = temp_db.get_or_create_file_id("src/foo.py", checksum=100)

        # Query with different checksum
        changed = temp_db.get_changed_file_ids({"src/foo.py": 200})

        assert file_id in changed

    def test_get_changed_file_ids_no_change(self, temp_db):
        """get_changed_file_ids() should not flag unchanged files."""
        temp_db.get_or_create_file_id("src/foo.py", checksum=100)

        changed = temp_db.get_changed_file_ids({"src/foo.py": 100})

        assert len(changed) == 0

    def test_get_changed_file_ids_new_file(self, temp_db):
        """get_changed_file_ids() should flag new files as changed."""
        # Don't create the file in DB first

        changed = temp_db.get_changed_file_ids({"src/new_file.py": 100})

        # New file should be created and flagged as changed
        assert len(changed) == 1
        # Verify the file was created
        filenames = temp_db.all_filenames()
        assert "src/new_file.py" in filenames

    def test_determine_tests_bitmap_finds_affected_tests(self, temp_db):
        """determine_tests_bitmap() should find tests affected by file changes."""
        # Setup: Create environment first
        exec_id, _, _, _ = temp_db.fetch_or_create_environment(
            "test_env", "packages", "3.9"
        )

        # Create file and test with dependency
        file_id = temp_db.get_or_create_file_id("src/foo.py", checksum=100)
        test_id = temp_db.get_or_create_test_id(exec_id=exec_id, test_name="test_foo")

        from ezmon.bitmap_deps import TestDeps
        deps = TestDeps.from_file_ids(test_id, {file_id}, set())
        temp_db.save_test_deps(test_id, deps)

        # Change the file
        result = temp_db.determine_tests_bitmap(
            exec_id=exec_id,
            files_checksums={"src/foo.py": 200},  # Different checksum
        )

        assert "test_foo" in result["affected"]

    def test_determine_tests_bitmap_no_change_no_affected(self, temp_db):
        """determine_tests_bitmap() should not flag tests when no changes."""
        # Setup: Create environment first
        exec_id, _, _, _ = temp_db.fetch_or_create_environment(
            "test_env", "packages", "3.9"
        )

        file_id = temp_db.get_or_create_file_id("src/foo.py", checksum=100)
        test_id = temp_db.get_or_create_test_id(exec_id=exec_id, test_name="test_foo")

        from ezmon.bitmap_deps import TestDeps
        deps = TestDeps.from_file_ids(test_id, {file_id}, set())
        temp_db.save_test_deps(test_id, deps)

        result = temp_db.determine_tests_bitmap(
            exec_id=exec_id,
            files_checksums={"src/foo.py": 100},  # Same checksum
        )

        assert "test_foo" not in result["affected"]

    def test_all_test_executions_returns_from_tests_table(self, temp_db):
        """all_test_executions() should query the tests table."""
        exec_id, _, _, _ = temp_db.fetch_or_create_environment(
            "test_env", "packages", "3.9"
        )

        temp_db.get_or_create_test_id(
            exec_id=exec_id, test_name="test_one", duration=1.5, failed=False
        )
        temp_db.get_or_create_test_id(
            exec_id=exec_id, test_name="test_two", duration=2.0, failed=True
        )

        tests = temp_db.all_test_executions(exec_id=exec_id)

        assert "test_one" in tests
        assert "test_two" in tests
        assert tests["test_one"]["duration"] == 1.5
        assert tests["test_two"]["failed"] is True

    def test_fetch_current_run_stats_returns_from_tests_table(self, temp_db):
        """fetch_current_run_stats() should query the tests table."""
        exec_id, _, _, _ = temp_db.fetch_or_create_environment(
            "test_env", "packages", "3.9"
        )

        temp_db.get_or_create_test_id(
            exec_id=exec_id, test_name="test_one", duration=1.5, failed=False
        )
        temp_db.get_or_create_test_id(
            exec_id=exec_id, test_name="test_two", duration=2.5, failed=False
        )

        run_saved_time, run_all_time, run_saved_tests, run_all_tests = \
            temp_db.fetch_current_run_stats(exec_id)

        assert run_saved_tests == 2
        assert run_all_tests == 2
        assert run_saved_time == 4.0  # 1.5 + 2.5
        assert run_all_time == 4.0

    def test_get_failing_tests_bitmap(self, temp_db):
        """get_failing_tests_bitmap() should return failed tests."""
        exec_id, _, _, _ = temp_db.fetch_or_create_environment(
            "test_env", "packages", "3.9"
        )

        temp_db.get_or_create_test_id(
            exec_id=exec_id, test_name="test_pass", duration=1.0, failed=False
        )
        temp_db.get_or_create_test_id(
            exec_id=exec_id, test_name="test_fail", duration=1.0, failed=True
        )

        failing = temp_db.get_failing_tests_bitmap(exec_id)

        assert "test_fail" in failing
        assert "test_pass" not in failing

    def test_delete_test(self, temp_db):
        """delete_test() should remove test and its dependencies."""
        exec_id, _, _, _ = temp_db.fetch_or_create_environment(
            "test_env", "packages", "3.9"
        )

        file_id = temp_db.get_or_create_file_id("src/foo.py", checksum=100)
        test_id = temp_db.get_or_create_test_id(
            exec_id=exec_id, test_name="test_to_delete"
        )

        from ezmon.bitmap_deps import TestDeps
        deps = TestDeps.from_file_ids(test_id, {file_id}, set())
        temp_db.save_test_deps(test_id, deps)

        # Verify test exists
        tests = temp_db.all_test_executions(exec_id)
        assert "test_to_delete" in tests

        # Delete the test
        temp_db.delete_test(exec_id, "test_to_delete")

        # Verify test is gone
        tests = temp_db.all_test_executions(exec_id)
        assert "test_to_delete" not in tests

        # Verify deps are gone too
        deps = temp_db.get_test_deps(test_id)
        assert deps is None

    def test_get_or_create_file_id_updates_checksum(self, temp_db):
        """get_or_create_file_id() should update checksum on subsequent calls."""
        # First call creates file
        file_id1 = temp_db.get_or_create_file_id("src/foo.py", checksum=100)

        # Second call with different checksum should return same ID
        temp_db.get_or_create_file_id.cache_clear()  # Clear cache to force DB lookup
        file_id2 = temp_db.get_or_create_file_id("src/foo.py", checksum=200)

        assert file_id1 == file_id2

        # Verify checksum was updated
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


class TestFetchUnknownFiles:
    """Test the _fetch_unknown_files_from_one_v method."""

    @pytest.fixture
    def temp_db(self):
        """Create a temporary database for testing."""
        with tempfile.NamedTemporaryFile(suffix='.testmondata', delete=False) as f:
            db_path = f.name
        db = DB(db_path)
        yield db
        db.con.close()
        os.unlink(db_path)

    def test_fetch_unknown_files_detects_changed_fsha(self, temp_db):
        """_fetch_unknown_files_from_one_v() should find files with changed fsha."""
        exec_id, _, _, _ = temp_db.fetch_or_create_environment(
            "test_env", "packages", "3.9"
        )

        # Create a file with a known fsha
        temp_db.get_or_create_file_id("src/foo.py", checksum=100, fsha="abc123")

        # Call fetch_unknown_files with a different fsha
        unknown = temp_db.fetch_unknown_files(
            {"src/foo.py": "different_fsha"},
            exec_id
        )

        assert "src/foo.py" in unknown

    def test_fetch_unknown_files_no_change_when_fsha_matches(self, temp_db):
        """_fetch_unknown_files_from_one_v() should not flag unchanged files."""
        exec_id, _, _, _ = temp_db.fetch_or_create_environment(
            "test_env", "packages", "3.9"
        )

        # Create a file with a known fsha
        temp_db.get_or_create_file_id("src/foo.py", checksum=100, fsha="abc123")

        # Call fetch_unknown_files with the same fsha
        unknown = temp_db.fetch_unknown_files(
            {"src/foo.py": "abc123"},
            exec_id
        )

        assert "src/foo.py" not in unknown


class TestDataFileDependencies:
    """Test data file dependency tracking."""

    @pytest.fixture
    def temp_db(self):
        """Create a temporary database for testing."""
        with tempfile.NamedTemporaryFile(suffix='.testmondata', delete=False) as f:
            db_path = f.name
        db = DB(db_path)
        yield db
        db.con.close()
        os.unlink(db_path)

    def test_get_changed_data_file_ids_detects_fsha_change(self, temp_db):
        """get_changed_data_file_ids() should detect when fsha differs."""
        # Create data file with original fsha
        file_id = temp_db.get_or_create_file_id(
            "config.json", checksum=None, fsha="original_hash", file_type='data'
        )

        # Query with different fsha
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
        exec_id, _, _, _ = temp_db.fetch_or_create_environment(
            "test_env", "packages", "3.9"
        )

        # Create python file and data file
        py_file_id = temp_db.get_or_create_file_id("src/foo.py", checksum=100)
        data_file_id = temp_db.get_or_create_file_id(
            "config.json", checksum=None, fsha="original", file_type='data'
        )

        # Create test that depends on both files
        test_id = temp_db.get_or_create_test_id(exec_id=exec_id, test_name="test_config")

        from ezmon.bitmap_deps import TestDeps
        deps = TestDeps.from_file_ids(test_id, {py_file_id, data_file_id}, set())
        temp_db.save_test_deps(test_id, deps)

        # Change the data file
        result = temp_db.determine_tests_bitmap(
            exec_id=exec_id,
            files_checksums={"src/foo.py": 100},  # Python file unchanged
            file_deps_shas={"config.json": "changed"},  # Data file changed
        )

        assert "test_config" in result["affected"]


class TestExternalPackageDependencies:
    """Test external package dependency tracking."""

    @pytest.fixture
    def temp_db(self):
        """Create a temporary database for testing."""
        with tempfile.NamedTemporaryFile(suffix='.testmondata', delete=False) as f:
            db_path = f.name
        db = DB(db_path)
        yield db
        db.con.close()
        os.unlink(db_path)

    def test_find_affected_tests_with_package_change(self, temp_db):
        """find_affected_tests_bitmap() should detect package changes."""
        exec_id, _, _, _ = temp_db.fetch_or_create_environment(
            "test_env", "packages", "3.9"
        )

        # Create file and test with package dependency
        file_id = temp_db.get_or_create_file_id("src/foo.py", checksum=100)
        test_id = temp_db.get_or_create_test_id(exec_id=exec_id, test_name="test_numpy")

        from ezmon.bitmap_deps import TestDeps
        deps = TestDeps.from_file_ids(test_id, {file_id}, {"numpy"})
        temp_db.save_test_deps(test_id, deps)

        # Simulate package change
        affected = temp_db.find_affected_tests_bitmap(
            exec_id=exec_id,
            changed_file_ids=set(),  # No file changes
            changed_packages={"numpy"},  # Package changed
        )

        assert "test_numpy" in affected

    def test_find_affected_tests_unrelated_package_change(self, temp_db):
        """find_affected_tests_bitmap() should not flag unrelated package changes."""
        exec_id, _, _, _ = temp_db.fetch_or_create_environment(
            "test_env", "packages", "3.9"
        )

        file_id = temp_db.get_or_create_file_id("src/foo.py", checksum=100)
        test_id = temp_db.get_or_create_test_id(exec_id=exec_id, test_name="test_numpy")

        from ezmon.bitmap_deps import TestDeps
        deps = TestDeps.from_file_ids(test_id, {file_id}, {"numpy"})
        temp_db.save_test_deps(test_id, deps)

        # Simulate unrelated package change
        affected = temp_db.find_affected_tests_bitmap(
            exec_id=exec_id,
            changed_file_ids=set(),
            changed_packages={"pandas"},  # Different package changed
        )

        assert "test_numpy" not in affected
