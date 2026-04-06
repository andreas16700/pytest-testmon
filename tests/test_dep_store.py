"""Tests for DepStore unified in-memory cache."""
import os
import tempfile
import pytest
from ezmon.db import DB
from ezmon.dep_store import DepStore
from ezmon.bitmap_deps import TestDeps


class TestDepStorePreload:
    """Test pre-loading from database."""

    @pytest.fixture
    def db_and_store(self):
        with tempfile.NamedTemporaryFile(suffix=".testmondata", delete=False) as f:
            db_path = f.name
        database = DB(db_path)
        run_id = database.create_run("abc123", "packages", "3.11")
        yield database, run_id
        database.con.close()
        os.unlink(db_path)

    def test_empty_db_preload(self, db_and_store):
        database, _ = db_and_store
        store = DepStore(database)
        assert store._files == {}
        assert store._tests == {}
        assert store._blobs == {}

    def test_preload_files(self, db_and_store):
        database, run_id = db_and_store
        database.get_or_create_file_id("src/foo.py", checksum=100, run_id=run_id)
        database.get_or_create_file_id("data.json", fsha="abc", file_type="data")

        store = DepStore(database)

        assert "src/foo.py" in store._files
        assert store._files["src/foo.py"].checksum == 100
        assert "data.json" in store._files
        assert store._files["data.json"].file_type == "data"

    def test_preload_tests(self, db_and_store):
        database, run_id = db_and_store
        database.get_or_create_test_id("test_one", duration=1.5, failed=False, run_id=run_id)
        database.get_or_create_test_id("test_two", duration=2.0, failed=True, run_id=run_id)

        store = DepStore(database)

        assert "test_one" in store._tests
        assert store._tests["test_one"].duration == 1.5
        assert "test_two" in store._tests
        assert store._tests["test_two"].failed is True

    def test_preload_blobs(self, db_and_store):
        database, run_id = db_and_store
        file_id = database.get_or_create_file_id("src/foo.py", checksum=100)
        test_id = database.get_or_create_test_id("test_one", run_id=run_id)
        deps = TestDeps.from_file_ids(test_id, {file_id}, set())
        database.save_test_deps(test_id, deps)

        store = DepStore(database)

        assert test_id in store._blobs
        assert isinstance(store._blobs[test_id], bytes)


class TestGetFileId:
    """Test get_file_id for known and new files."""

    @pytest.fixture
    def store(self):
        with tempfile.NamedTemporaryFile(suffix=".testmondata", delete=False) as f:
            db_path = f.name
        database = DB(db_path)
        database.create_run("abc123", "packages", "3.11")
        database.get_or_create_file_id("src/existing.py", checksum=100)
        store = DepStore(database)
        yield store, database
        database.con.close()
        os.unlink(db_path)

    def test_known_file_returns_cached_id(self, store):
        s, _ = store
        file_id = s.get_file_id("src/existing.py")
        assert file_id == s._files["src/existing.py"].id

    def test_known_file_ignores_checksum(self, store):
        s, _ = store
        original_checksum = s._files["src/existing.py"].checksum
        s.get_file_id("src/existing.py", checksum=999)
        # Checksum should NOT be updated for known files
        assert s._files["src/existing.py"].checksum == original_checksum

    def test_new_file_inserts_and_caches(self, store):
        s, db = store
        file_id = s.get_file_id("src/new.py", checksum=200)
        assert file_id is not None
        assert "src/new.py" in s._files
        assert s._files["src/new.py"].checksum == 200
        # Verify it's in the DB too
        row = db.con.execute(
            "SELECT id, checksum FROM files WHERE path = 'src/new.py'"
        ).fetchone()
        assert row is not None
        assert row["id"] == file_id

    def test_new_file_without_checksum(self, store):
        s, _ = store
        file_id = s.get_file_id("src/brand_new.py")
        assert file_id is not None
        assert s._files["src/brand_new.py"].checksum is None

    def test_new_data_file(self, store):
        s, _ = store
        file_id = s.get_file_id("config.json", fsha="abc123", file_type="data")
        assert file_id is not None
        assert s._files["config.json"].file_type == "data"
        assert s._files["config.json"].fsha == "abc123"


class TestEnsureTestsBatch:
    """Test ensure_tests_batch for cached and new tests."""

    @pytest.fixture
    def store(self):
        with tempfile.NamedTemporaryFile(suffix=".testmondata", delete=False) as f:
            db_path = f.name
        database = DB(db_path)
        run_id = database.create_run("abc123", "packages", "3.11")
        database.get_or_create_test_id("existing_test", duration=1.0, run_id=run_id)
        store = DepStore(database)
        yield store, database, run_id
        database.con.close()
        os.unlink(db_path)

    def test_cached_test_returns_id(self, store):
        s, _, run_id = store
        result = s.ensure_tests_batch(run_id, [
            ("existing_test", "test_file.py", 2.0, False, None),
        ])
        assert "existing_test" in result
        assert result["existing_test"] == s._tests["existing_test"].id

    def test_cached_test_updates_metadata(self, store):
        s, _, run_id = store
        s.ensure_tests_batch(run_id, [
            ("existing_test", "test_file.py", 5.0, True, None),
        ])
        entry = s._tests["existing_test"]
        assert entry.duration == 5.0
        assert entry.failed is True
        assert entry.dirty is True

    def test_new_test_inserts(self, store):
        s, db, run_id = store
        result = s.ensure_tests_batch(run_id, [
            ("new_test", "test_new.py", 3.0, False, None),
        ])
        assert "new_test" in result
        assert "new_test" in s._tests
        # Verify in DB
        row = db.con.execute(
            "SELECT id FROM tests WHERE name = 'new_test'"
        ).fetchone()
        assert row is not None
        assert row["id"] == result["new_test"]

    def test_mixed_batch(self, store):
        s, _, run_id = store
        result = s.ensure_tests_batch(run_id, [
            ("existing_test", "test_file.py", 2.0, False, None),
            ("brand_new_test", "test_new.py", 1.0, False, None),
        ])
        assert "existing_test" in result
        assert "brand_new_test" in result
        assert len(result) == 2


class TestGetExistingBlob:
    """Test get_existing_blob cache lookup."""

    @pytest.fixture
    def store(self):
        with tempfile.NamedTemporaryFile(suffix=".testmondata", delete=False) as f:
            db_path = f.name
        database = DB(db_path)
        run_id = database.create_run("abc123", "packages", "3.11")
        file_id = database.get_or_create_file_id("src/foo.py", checksum=100)
        test_id = database.get_or_create_test_id("test_one", run_id=run_id)
        deps = TestDeps.from_file_ids(test_id, {file_id}, set())
        database.save_test_deps(test_id, deps)
        store = DepStore(database)
        yield store, test_id
        database.con.close()
        os.unlink(db_path)

    def test_existing_blob_found(self, store):
        s, test_id = store
        blob = s.get_existing_blob(test_id)
        assert blob is not None
        assert isinstance(blob, bytes)

    def test_missing_blob_returns_none(self, store):
        s, _ = store
        assert s.get_existing_blob(99999) is None


class TestSaveBatch:
    """Test save_batch flushes dirty data and writes test_deps."""

    @pytest.fixture
    def store(self):
        with tempfile.NamedTemporaryFile(suffix=".testmondata", delete=False) as f:
            db_path = f.name
        database = DB(db_path)
        run_id = database.create_run("abc123", "packages", "3.11")
        store = DepStore(database)
        yield store, database, run_id
        database.con.close()
        os.unlink(db_path)

    def test_flush_dirty_tests(self, store):
        s, db, run_id = store
        # Create a test and mark it dirty
        s.ensure_tests_batch(run_id, [("test_flush", "test.py", 1.0, False, None)])
        entry = s._tests["test_flush"]
        entry.duration = 99.0
        entry.dirty = True

        with db.con:
            s.save_batch([])

        # Verify dirty flag cleared
        assert entry.dirty is False
        # Verify DB updated
        row = db.con.execute(
            "SELECT duration FROM tests WHERE name = 'test_flush'"
        ).fetchone()
        assert row["duration"] == 99.0

    def test_write_test_deps(self, store):
        s, db, run_id = store
        file_id = s.get_file_id("src/foo.py", checksum=100)
        result = s.ensure_tests_batch(run_id, [("test_deps", "test.py", 1.0, False, None)])
        test_id = result["test_deps"]

        deps = TestDeps.from_file_ids(test_id, {file_id}, set())
        blob = deps.serialize()
        pkgs_str = deps.serialize_external_packages()

        with db.con:
            s.save_batch([(test_id, blob, pkgs_str)])

        # Verify blob cached
        assert s._blobs[test_id] == blob
        # Verify in DB
        row = db.con.execute(
            "SELECT file_bitmap FROM test_deps WHERE test_id = ?", (test_id,)
        ).fetchone()
        assert row is not None


class TestAccessors:
    """Test read-only accessor methods."""

    @pytest.fixture
    def store(self):
        with tempfile.NamedTemporaryFile(suffix=".testmondata", delete=False) as f:
            db_path = f.name
        database = DB(db_path)
        run_id = database.create_run("abc123", "packages", "3.11")
        database.get_or_create_file_id("src/foo.py", checksum=100)
        database.get_or_create_file_id("src/bar.py", checksum=200)
        database.get_or_create_file_id("config.json", fsha="abc", file_type="data")
        database.get_or_create_test_id("test_pass", duration=1.0, failed=False, run_id=run_id)
        database.get_or_create_test_id("test_fail", duration=2.0, failed=True, run_id=run_id,
                                       test_file="tests/test_a.py")
        store = DepStore(database)
        yield store
        database.con.close()
        os.unlink(db_path)

    def test_get_file_checksums(self, store):
        checksums = store.get_file_checksums()
        assert checksums["src/foo.py"] == 100
        assert checksums["src/bar.py"] == 200

    def test_get_file_id_map(self, store):
        id_map = store.get_file_id_map()
        assert "src/foo.py" in id_map
        assert "src/bar.py" in id_map
        assert isinstance(id_map["src/foo.py"], int)

    def test_get_file_ids_for_paths(self, store):
        ids = store.get_file_ids_for_paths({"src/foo.py", "nonexistent.py"})
        assert len(ids) == 1

    def test_update_file_checksum(self, store):
        store.update_file_checksum("src/foo.py", 999, fsha="new_sha")
        assert store._files["src/foo.py"].checksum == 999
        assert store._files["src/foo.py"].fsha == "new_sha"
        # Also verify via accessor
        checksums = store.get_file_checksums()
        assert checksums["src/foo.py"] == 999

    def test_all_filenames(self, store):
        filenames = store.all_filenames()
        assert "src/foo.py" in filenames
        assert "src/bar.py" in filenames
        assert "config.json" not in filenames  # data file excluded

    def test_all_test_executions(self, store):
        tests = store.all_test_executions()
        assert "test_pass" in tests
        assert "test_fail" in tests
        assert tests["test_pass"]["duration"] == 1.0
        assert tests["test_fail"]["failed"] is True

    def test_get_failing_tests(self, store):
        failing = store.get_failing_tests()
        assert "test_fail" in failing
        assert "test_pass" not in failing

    def test_get_test_files_for_tests(self, store):
        files = store.get_test_files_for_tests({"test_fail", "test_pass"})
        assert "tests/test_a.py" in files

    def test_get_all_test_files(self, store):
        files = store.get_all_test_files()
        assert "tests/test_a.py" in files
