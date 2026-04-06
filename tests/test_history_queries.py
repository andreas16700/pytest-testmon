"""Tests for ezmon/history.py query helpers.

These tests build a multi-run DB by hand (directly inserting into the
history tables) and verify that each query helper returns the correct
results. This isolates the query layer from the DepStore capture layer
(tested separately in test_versioning_capture.py).
"""
import os
import tempfile

import pytest

from ezmon.bitmap_deps import TestDeps
from ezmon.db import DB
from ezmon.history import (
    FileChange,
    FileVersion,
    SelectionExplanation,
    TestDepsVersion,
    TestFailedVersion,
    explain_selection,
    file_churn,
    get_file_at_run,
    get_file_changes_between,
    get_test_deps_at_run,
    get_test_deps_changes_between,
)


@pytest.fixture
def db_path():
    fd, path = tempfile.mkstemp(suffix=".testmondata")
    os.close(fd)
    os.unlink(path)
    yield path
    for suffix in ("", "-wal", "-shm"):
        try:
            os.unlink(path + suffix)
        except FileNotFoundError:
            pass


@pytest.fixture
def multi_run_db(db_path):
    """Build a DB with 3 runs and known history data.

    Run 1: fresh DB, file src/a.py checksum=100, test_a depends on a.py
    Run 2: src/a.py checksum changed to 200, test_a deps updated
    Run 3: src/b.py added, test_a unchanged, test_b new depending on b.py
    """
    database = DB(db_path)
    con = database.con

    # Create 3 runs
    r1 = database.create_run("commit1", "numpy==1.0", "3.11")
    r2 = database.create_run("commit2", "numpy==1.0", "3.11")
    r3 = database.create_run("commit3", "numpy==2.0", "3.11")

    # Create files in current-state tables
    con.execute(
        "INSERT INTO files (id, path, checksum, fsha, file_type, run_id) "
        "VALUES (1, 'src/a.py', 200, 'sha_a2', 'python', ?)",
        (r2,),
    )
    con.execute(
        "INSERT INTO files (id, path, checksum, fsha, file_type, run_id) "
        "VALUES (2, 'src/b.py', 300, 'sha_b1', 'python', ?)",
        (r3,),
    )

    # Create tests in current-state tables
    con.execute(
        "INSERT INTO tests (id, name, test_file, duration, failed, run_id) "
        "VALUES (1, 'test_a', 'tests/test_a.py', 0.5, 0, ?)",
        (r2,),
    )
    con.execute(
        "INSERT INTO tests (id, name, test_file, duration, failed, run_id) "
        "VALUES (2, 'test_b', 'tests/test_b.py', 0.3, 0, ?)",
        (r3,),
    )

    # Populate test_deps current state
    deps_a = TestDeps.from_file_ids(1, {1, 2}, {"numpy"})
    con.execute(
        "INSERT INTO test_deps (test_id, file_bitmap, external_packages) "
        "VALUES (?, ?, ?)",
        (1, deps_a.serialize(), "numpy"),
    )
    deps_b = TestDeps.from_file_ids(2, {2}, set())
    con.execute(
        "INSERT INTO test_deps (test_id, file_bitmap, external_packages) "
        "VALUES (?, ?, ?)",
        (2, deps_b.serialize(), ""),
    )

    # ---- History tables ----

    # files_history: src/a.py appeared in run 1 with checksum=100,
    #               changed in run 2 to checksum=200
    con.execute(
        "INSERT INTO files_history (file_id, run_id, path, file_type, checksum, fsha) "
        "VALUES (1, ?, 'src/a.py', 'python', 100, 'sha_a1')",
        (r1,),
    )
    con.execute(
        "INSERT INTO files_history (file_id, run_id, path, file_type, checksum, fsha) "
        "VALUES (1, ?, 'src/a.py', 'python', 200, 'sha_a2')",
        (r2,),
    )
    # files_history: src/b.py appeared in run 3
    con.execute(
        "INSERT INTO files_history (file_id, run_id, path, file_type, checksum, fsha) "
        "VALUES (2, ?, 'src/b.py', 'python', 300, 'sha_b1')",
        (r3,),
    )

    # tests_failed_history: test_a started passing, flipped to failed in run 2
    con.execute(
        "INSERT INTO tests_failed_history (test_id, run_id, name, test_file, failed) "
        "VALUES (1, ?, 'test_a', 'tests/test_a.py', 0)",
        (r1,),
    )
    con.execute(
        "INSERT INTO tests_failed_history (test_id, run_id, name, test_file, failed) "
        "VALUES (1, ?, 'test_a', 'tests/test_a.py', 1)",
        (r2,),
    )
    # test_b first appeared in run 3
    con.execute(
        "INSERT INTO tests_failed_history (test_id, run_id, name, test_file, failed) "
        "VALUES (2, ?, 'test_b', 'tests/test_b.py', 0)",
        (r3,),
    )

    # test_deps_history: test_a deps in run 1 (only a.py), updated in run 2 (a.py+b.py)
    deps_a_r1 = TestDeps.from_file_ids(1, {1}, {"numpy"})
    con.execute(
        "INSERT INTO test_deps_history "
        "(test_id, run_id, name, test_file, file_bitmap, external_packages) "
        "VALUES (1, ?, 'test_a', 'tests/test_a.py', ?, 'numpy')",
        (r1, deps_a_r1.serialize()),
    )
    deps_a_r2 = TestDeps.from_file_ids(1, {1, 2}, {"numpy"})
    con.execute(
        "INSERT INTO test_deps_history "
        "(test_id, run_id, name, test_file, file_bitmap, external_packages) "
        "VALUES (1, ?, 'test_a', 'tests/test_a.py', ?, 'numpy')",
        (r2, deps_a_r2.serialize()),
    )
    # test_b deps in run 3
    deps_b_r3 = TestDeps.from_file_ids(2, {2}, set())
    con.execute(
        "INSERT INTO test_deps_history "
        "(test_id, run_id, name, test_file, file_bitmap, external_packages) "
        "VALUES (2, ?, 'test_b', 'tests/test_b.py', ?, '')",
        (r3, deps_b_r3.serialize()),
    )

    con.commit()
    yield database, r1, r2, r3
    database.close()


class TestGetFileAtRun:
    def test_file_at_run1(self, multi_run_db):
        db, r1, r2, r3 = multi_run_db
        fv = get_file_at_run(db, "src/a.py", r1)
        assert fv is not None
        assert fv.checksum == 100
        assert fv.fsha == "sha_a1"
        assert fv.run_id == r1

    def test_file_at_run2_shows_updated_checksum(self, multi_run_db):
        db, r1, r2, r3 = multi_run_db
        fv = get_file_at_run(db, "src/a.py", r2)
        assert fv.checksum == 200
        assert fv.fsha == "sha_a2"
        assert fv.run_id == r2

    def test_file_before_creation_returns_none(self, multi_run_db):
        db, r1, r2, r3 = multi_run_db
        # src/b.py only appeared in run 3
        assert get_file_at_run(db, "src/b.py", r2) is None

    def test_file_at_run3_sees_b(self, multi_run_db):
        db, r1, r2, r3 = multi_run_db
        fv = get_file_at_run(db, "src/b.py", r3)
        assert fv is not None
        assert fv.checksum == 300

    def test_nonexistent_file(self, multi_run_db):
        db, r1, r2, r3 = multi_run_db
        assert get_file_at_run(db, "src/nope.py", r3) is None

    def test_tombstone_reports_is_tombstone(self, multi_run_db):
        db, r1, r2, r3 = multi_run_db
        # Insert a tombstone for src/a.py at run 3
        db.con.execute(
            "INSERT INTO files_history (file_id, run_id, path, file_type, checksum, fsha) "
            "VALUES (1, ?, 'src/a.py', 'python', NULL, NULL)",
            (r3,),
        )
        db.con.commit()
        fv = get_file_at_run(db, "src/a.py", r3)
        assert fv is not None
        assert fv.is_tombstone


class TestGetTestDepsAtRun:
    def test_deps_at_run1(self, multi_run_db):
        db, r1, r2, r3 = multi_run_db
        td = get_test_deps_at_run(db, "test_a", r1)
        assert td is not None
        assert td.file_ids() == {1}
        assert td.external_packages == "numpy"

    def test_deps_at_run2_has_both_files(self, multi_run_db):
        db, r1, r2, r3 = multi_run_db
        td = get_test_deps_at_run(db, "test_a", r2)
        assert td.file_ids() == {1, 2}

    def test_deps_before_test_exists_returns_none(self, multi_run_db):
        db, r1, r2, r3 = multi_run_db
        assert get_test_deps_at_run(db, "test_b", r1) is None

    def test_deps_at_run3_for_test_b(self, multi_run_db):
        db, r1, r2, r3 = multi_run_db
        td = get_test_deps_at_run(db, "test_b", r3)
        assert td is not None
        assert td.file_ids() == {2}


class TestFileChangesBetween:
    def test_changes_between_run1_and_run2(self, multi_run_db):
        db, r1, r2, r3 = multi_run_db
        changes = get_file_changes_between(db, r1, r2)
        assert len(changes) == 1
        assert changes[0].path == "src/a.py"
        assert changes[0].old_checksum == 100
        assert changes[0].new_checksum == 200

    def test_changes_between_run2_and_run3(self, multi_run_db):
        db, r1, r2, r3 = multi_run_db
        changes = get_file_changes_between(db, r2, r3)
        assert len(changes) == 1
        assert changes[0].path == "src/b.py"
        assert changes[0].old_checksum is None  # didn't exist at run 2
        assert changes[0].new_checksum == 300

    def test_changes_between_run1_and_run3(self, multi_run_db):
        db, r1, r2, r3 = multi_run_db
        changes = get_file_changes_between(db, r1, r3)
        paths = {c.path for c in changes}
        assert paths == {"src/a.py", "src/b.py"}

    def test_no_changes_between_identical_runs(self, multi_run_db):
        db, r1, r2, r3 = multi_run_db
        # No history rows between r3 and r3
        assert get_file_changes_between(db, r3, r3) == []


class TestTestDepsChangesBetween:
    def test_deps_changes_run1_to_run2(self, multi_run_db):
        db, r1, r2, r3 = multi_run_db
        changes = get_test_deps_changes_between(db, r1, r2)
        assert len(changes) == 1
        assert changes[0].name == "test_a"

    def test_deps_changes_run2_to_run3(self, multi_run_db):
        db, r1, r2, r3 = multi_run_db
        changes = get_test_deps_changes_between(db, r2, r3)
        assert len(changes) == 1
        assert changes[0].name == "test_b"


class TestExplainSelection:
    def test_explain_run2_test_a(self, multi_run_db):
        """test_a was selected in run 2 because src/a.py changed.

        Prior deps (run 1): {file_id=1 (src/a.py)}
        Changed files (run 1 → run 2): src/a.py
        Intersection: src/a.py → triggering.
        """
        db, r1, r2, r3 = multi_run_db
        exp = explain_selection(db, "test_a", r2)
        assert not exp.is_new
        assert "src/a.py" in exp.triggering_files
        assert not exp.was_failed

    def test_explain_run3_test_b_is_new(self, multi_run_db):
        """test_b first appears in run 3 — explain should mark is_new."""
        db, r1, r2, r3 = multi_run_db
        exp = explain_selection(db, "test_b", r3)
        # test_b has no deps at run 2, so it's "new" from explain's perspective
        assert exp.is_new is True

    def test_explain_first_run(self, multi_run_db):
        """The very first run has no prior — everything is new."""
        db, r1, r2, r3 = multi_run_db
        exp = explain_selection(db, "test_a", r1)
        assert exp.is_new is True
        assert exp.triggering_files == []

    def test_explain_failed_test(self, multi_run_db):
        """test_a was marked failed in run 2. At run 3 explain should
        report was_failed=True."""
        db, r1, r2, r3 = multi_run_db
        exp = explain_selection(db, "test_a", r3)
        assert exp.was_failed is True

    def test_explain_nonexistent_test(self, multi_run_db):
        db, r1, r2, r3 = multi_run_db
        exp = explain_selection(db, "test_nonexistent", r3)
        assert exp.is_new is True
        assert exp.triggering_files == []


class TestFileChurn:
    def test_churn_all_runs(self, multi_run_db):
        db, r1, r2, r3 = multi_run_db
        churn = file_churn(db)
        # src/a.py has 2 versions (run 1, run 2), src/b.py has 1
        assert len(churn) == 2
        assert churn[0]["path"] == "src/a.py"
        assert churn[0]["versions"] == 2
        assert churn[1]["path"] == "src/b.py"
        assert churn[1]["versions"] == 1

    def test_churn_since_run2(self, multi_run_db):
        db, r1, r2, r3 = multi_run_db
        churn = file_churn(db, since_run=r2)
        # Only src/b.py changed after run 2
        assert len(churn) == 1
        assert churn[0]["path"] == "src/b.py"

    def test_churn_empty_db(self, db_path):
        database = DB(db_path)
        assert file_churn(database) == []
        database.close()
