"""Regression tests for the test_deps skip-unchanged filter.

Before Prereq 2, the skip-unchanged check in testmon_core.py compared
only the file_bitmap blob, silently dropping writes when a package
version bump left the bitmap unchanged but flipped external_packages.
These tests pin the fixed behavior: changes to external_packages alone
must persist.
"""
import os
import tempfile

import pytest

from ezmon.bitmap_deps import TestDeps
from ezmon.db import DB
from ezmon.dep_store import DepStore


@pytest.fixture
def db_path():
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
def db_with_store(db_path):
    database = DB(db_path)
    run_id = database.create_run("initial", "packages", "3.11")
    yield database, run_id
    database.close()


def _save_via_depstore(ds, test_id, file_ids, packages):
    """Drive DepStore.save_batch with the same skip-check logic as testmon_core.py."""
    deps = TestDeps.from_file_ids(test_id, file_ids, packages)
    blob = deps.serialize()
    pkgs = deps.serialize_external_packages()
    pending = [(test_id, blob, pkgs)]
    # Apply the same skip-unchanged filter as testmon_core.py uses
    pending = [
        (tid, b, p)
        for tid, b, p in pending
        if ds.get_existing_blob(tid) != b
        or ds.get_existing_packages(tid) != (p or "")
    ]
    ds.save_batch(pending)
    return len(pending)


def _load_packages_from_db(database, test_id):
    row = database.con.execute(
        "SELECT external_packages FROM test_deps WHERE test_id = ?",
        (test_id,),
    ).fetchone()
    return row["external_packages"] if row else None


class TestSkipUnchangedPreservesPackageChanges:
    def test_package_only_change_persists(self, db_with_store):
        """Adding a package without changing imports must be written."""
        database, run_id = db_with_store
        file_id = database.get_or_create_file_id("src/foo.py", checksum=1)
        test_id = database.get_or_create_test_id(
            "test_foo", run_id=run_id, test_file="src/test_foo.py"
        )

        ds = DepStore(database)

        # First write: test depends on {numpy}
        written = _save_via_depstore(ds, test_id, {file_id}, {"numpy"})
        assert written == 1
        assert _load_packages_from_db(database, test_id) == "numpy"

        # Second write: same files, different packages ({numpy, scipy}).
        # The bitmap is unchanged but external_packages differs — this
        # MUST be written, not skipped as unchanged.
        written = _save_via_depstore(ds, test_id, {file_id}, {"numpy", "scipy"})
        assert written == 1, (
            "Package addition was dropped by skip-unchanged filter"
        )
        stored = _load_packages_from_db(database, test_id)
        assert stored is not None
        assert sorted(stored.split(",")) == ["numpy", "scipy"]

    def test_package_removal_persists(self, db_with_store):
        """Removing the last package must be written even if bitmap unchanged."""
        database, run_id = db_with_store
        file_id = database.get_or_create_file_id("src/bar.py", checksum=1)
        test_id = database.get_or_create_test_id(
            "test_bar", run_id=run_id, test_file="src/test_bar.py"
        )

        ds = DepStore(database)

        written = _save_via_depstore(ds, test_id, {file_id}, {"requests"})
        assert written == 1
        assert _load_packages_from_db(database, test_id) == "requests"

        # Remove the package entirely. Bitmap unchanged, packages flip
        # from "requests" to "". Must be written.
        written = _save_via_depstore(ds, test_id, {file_id}, set())
        assert written == 1, (
            "Package removal was dropped by skip-unchanged filter"
        )
        # After removal, serialize_external_packages returns "" which
        # SQLite may store as NULL or "" — both mean "no packages".
        stored = _load_packages_from_db(database, test_id)
        assert stored in (None, "")

    def test_package_swap_persists(self, db_with_store):
        """Swapping one package for another must be written."""
        database, run_id = db_with_store
        file_id = database.get_or_create_file_id("src/baz.py", checksum=1)
        test_id = database.get_or_create_test_id(
            "test_baz", run_id=run_id, test_file="src/test_baz.py"
        )

        ds = DepStore(database)

        written = _save_via_depstore(ds, test_id, {file_id}, {"numpy"})
        assert written == 1
        assert _load_packages_from_db(database, test_id) == "numpy"

        # Swap numpy -> pandas
        written = _save_via_depstore(ds, test_id, {file_id}, {"pandas"})
        assert written == 1
        assert _load_packages_from_db(database, test_id) == "pandas"

    def test_same_bitmap_same_packages_is_skipped(self, db_with_store):
        """The optimization still fires when both fields are unchanged."""
        database, run_id = db_with_store
        file_id = database.get_or_create_file_id("src/qux.py", checksum=1)
        test_id = database.get_or_create_test_id(
            "test_qux", run_id=run_id, test_file="src/test_qux.py"
        )

        ds = DepStore(database)

        written = _save_via_depstore(ds, test_id, {file_id}, {"numpy"})
        assert written == 1

        # Identical write: should be filtered out
        written = _save_via_depstore(ds, test_id, {file_id}, {"numpy"})
        assert written == 0, "Unchanged row was not skipped"

    def test_preloaded_store_sees_packages(self, db_with_store):
        """DepStore._preload must load external_packages alongside the blob."""
        database, run_id = db_with_store
        file_id = database.get_or_create_file_id("src/x.py", checksum=1)
        test_id = database.get_or_create_test_id("test_x", run_id=run_id)
        deps = TestDeps.from_file_ids(test_id, {file_id}, {"numpy", "scipy"})
        database.save_test_deps(test_id, deps)

        # Fresh DepStore should load both blob and packages from disk
        ds = DepStore(database)
        assert ds.get_existing_blob(test_id) is not None
        assert ds.get_existing_packages(test_id) != ""
        assert "numpy" in ds.get_existing_packages(test_id)
        assert "scipy" in ds.get_existing_packages(test_id)

    def test_get_existing_packages_returns_empty_for_unknown(self, db_with_store):
        """Unknown test_id returns "" — the same as known-but-empty."""
        database, _ = db_with_store
        ds = DepStore(database)
        assert ds.get_existing_packages(99999) == ""


class TestDbFallbackBatch:
    """Coverage for the non-DepStore path in testmon_core.py:867-881.

    When DepStore is not available (single-process fallback), the skip
    filter uses db.get_test_deps_batch which previously returned only
    blobs. It now returns (blob, packages) tuples.
    """

    def test_get_test_deps_batch_returns_tuples(self, db_with_store):
        database, run_id = db_with_store
        file_id = database.get_or_create_file_id("src/a.py", checksum=1)
        t1 = database.get_or_create_test_id("test_a1", run_id=run_id)
        t2 = database.get_or_create_test_id("test_a2", run_id=run_id)

        deps1 = TestDeps.from_file_ids(t1, {file_id}, {"numpy"})
        deps2 = TestDeps.from_file_ids(t2, {file_id}, set())
        database.save_test_deps(t1, deps1)
        database.save_test_deps(t2, deps2)

        result = database.get_test_deps_batch([t1, t2])
        assert t1 in result
        assert t2 in result
        # Each value is a (blob, packages_str) tuple
        blob1, pkgs1 = result[t1]
        blob2, pkgs2 = result[t2]
        assert isinstance(blob1, bytes)
        assert pkgs1 == "numpy"
        assert isinstance(blob2, bytes)
        # Empty packages is normalized to ""
        assert pkgs2 == ""

    def test_get_test_deps_batch_missing_ids(self, db_with_store):
        database, _ = db_with_store
        result = database.get_test_deps_batch([99998, 99999])
        assert result == {}
