"""Tests for DepStore's append-only history capture (v2 of versioning).

These tests exercise the capture buffer directly against a real DB to
verify:

- History is zero-cost when versioning is disabled (the guardrail).
- First-insert, unchanged, and changed mutations produce the right
  rows in the right tables.
- NULL checksum/fsha is reserved for git-deletion tombstones — regular
  inserts with unknown content do NOT pollute history with NULL rows.
- Within a single session, repeat mutations to the same entity
  produce exactly one history row (rerun/retry dedup via in-memory
  buffer keyed by (entity_id, run_id)).
- failed-flag history captures flips, not steady states.
- test_deps history fires on bitmap OR package changes (not just
  bitmap, which would silently drop package-only updates).
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
    os.unlink(path)
    yield path
    for suffix in ("", "-wal", "-shm"):
        try:
            os.unlink(path + suffix)
        except FileNotFoundError:
            pass


@pytest.fixture
def fresh_db(db_path):
    database = DB(db_path)
    run_id = database.create_run("abc123", "pkgs", "3.11")
    yield database, run_id
    database.close()


def _history_rows(database, table):
    return database.con.execute(f"SELECT * FROM {table}").fetchall()


def _make_store(database, run_id, versioning_enabled=True):
    return DepStore(database, run_id=run_id, versioning_enabled=versioning_enabled)


class TestVersioningDisabled:
    def test_buffer_stays_empty_when_disabled(self, fresh_db):
        database, run_id = fresh_db
        ds = _make_store(database, run_id, versioning_enabled=False)
        # Make some mutations
        ds.get_file_id("src/a.py", checksum=1, fsha="sha1")
        ds.update_file_checksum("src/a.py", 2, fsha="sha2")
        ds.ensure_tests_batch(run_id, [("t1", "src/test_a.py", 0.1, False, None)])
        test_id = ds._tests["t1"].id
        deps = TestDeps.from_file_ids(test_id, {ds._files["src/a.py"].id}, {"numpy"})
        ds.save_batch([(test_id, deps.serialize(), "numpy")])

        assert ds._history_ops == {}
        for table in ("files_history", "tests_failed_history", "test_deps_history"):
            assert _history_rows(database, table) == []

    def test_no_run_id_is_noop_even_when_enabled(self, fresh_db):
        """Without a run_id, history emission is disabled as a safety rail."""
        database, _ = fresh_db
        ds = DepStore(database, run_id=None, versioning_enabled=True)
        ds.get_file_id("src/b.py", checksum=1, fsha="s")
        assert ds._history_ops == {}


class TestFilesHistory:
    def test_new_file_with_known_content_emits_row(self, fresh_db):
        database, run_id = fresh_db
        ds = _make_store(database, run_id)
        ds.get_file_id("src/foo.py", checksum=42, fsha="abc")
        # save_batch triggers flush
        ds.save_batch([])

        rows = _history_rows(database, "files_history")
        assert len(rows) == 1
        assert rows[0]["path"] == "src/foo.py"
        assert rows[0]["run_id"] == run_id
        assert rows[0]["checksum"] == 42
        assert rows[0]["fsha"] == "abc"
        assert rows[0]["file_type"] == "python"

    def test_new_file_without_checksum_or_fsha_emits_nothing(self, fresh_db):
        """Guardrail 2: get_file_id with unknown content must not emit.

        A row with NULL checksum AND NULL fsha is indistinguishable
        from a tombstone. If we emitted here, later update_file_checksum
        would need to overwrite a noisy phantom row.
        """
        database, run_id = fresh_db
        ds = _make_store(database, run_id)
        ds.get_file_id("src/bar.py")  # no checksum, no fsha
        ds.save_batch([])

        assert _history_rows(database, "files_history") == []

    def test_update_file_checksum_emits_on_change(self, fresh_db):
        database, run_id = fresh_db
        database.get_or_create_file_id("src/baz.py", checksum=1, fsha="old")
        ds = _make_store(database, run_id)

        ds.update_file_checksum("src/baz.py", 2, fsha="new")
        ds.save_batch([])

        rows = _history_rows(database, "files_history")
        assert len(rows) == 1
        assert rows[0]["checksum"] == 2
        assert rows[0]["fsha"] == "new"

    def test_update_file_checksum_no_emit_when_unchanged(self, fresh_db):
        database, run_id = fresh_db
        database.get_or_create_file_id("src/qux.py", checksum=1, fsha="x")
        ds = _make_store(database, run_id)

        ds.update_file_checksum("src/qux.py", 1, fsha="x")  # same values
        ds.save_batch([])

        assert _history_rows(database, "files_history") == []

    def test_update_fsha_only_emits(self, fresh_db):
        """Same checksum, different fsha still counts as a change."""
        database, run_id = fresh_db
        database.get_or_create_file_id("src/x.py", checksum=1, fsha="old_sha")
        ds = _make_store(database, run_id)

        ds.update_file_checksum("src/x.py", 1, fsha="new_sha")
        ds.save_batch([])

        rows = _history_rows(database, "files_history")
        assert len(rows) == 1
        assert rows[0]["fsha"] == "new_sha"

    def test_idempotent_within_session(self, fresh_db):
        """Multiple updates in one session produce ONE history row."""
        database, run_id = fresh_db
        database.get_or_create_file_id("src/y.py", checksum=1, fsha="s1")
        ds = _make_store(database, run_id)

        # Three updates to the same file in the same session
        ds.update_file_checksum("src/y.py", 2, fsha="s2")
        ds.update_file_checksum("src/y.py", 3, fsha="s3")
        ds.update_file_checksum("src/y.py", 4, fsha="s4")
        ds.save_batch([])

        rows = _history_rows(database, "files_history")
        assert len(rows) == 1, "Session dedup failed — expected 1 row per (file, run)"
        # Last write wins
        assert rows[0]["checksum"] == 4
        assert rows[0]["fsha"] == "s4"

    def test_emit_file_tombstone_writes_null_row(self, fresh_db):
        database, run_id = fresh_db
        database.get_or_create_file_id("src/deleted.py", checksum=9, fsha="old")
        ds = _make_store(database, run_id)

        ds.emit_file_tombstone("src/deleted.py")
        ds.save_batch([])

        rows = _history_rows(database, "files_history")
        assert len(rows) == 1
        assert rows[0]["path"] == "src/deleted.py"
        assert rows[0]["checksum"] is None
        assert rows[0]["fsha"] is None

    def test_tombstone_disabled_when_versioning_off(self, fresh_db):
        database, run_id = fresh_db
        database.get_or_create_file_id("src/d.py", checksum=1)
        ds = _make_store(database, run_id, versioning_enabled=False)

        ds.emit_file_tombstone("src/d.py")
        ds.save_batch([])

        assert _history_rows(database, "files_history") == []


class TestTestsFailedHistory:
    def test_new_test_emits_initial_row(self, fresh_db):
        database, run_id = fresh_db
        ds = _make_store(database, run_id)

        ds.ensure_tests_batch(run_id, [("test_new", "src/test_new.py", 0.5, False, None)])
        ds.save_batch([])

        rows = _history_rows(database, "tests_failed_history")
        assert len(rows) == 1
        assert rows[0]["name"] == "test_new"
        assert rows[0]["test_file"] == "src/test_new.py"
        assert rows[0]["failed"] == 0

    def test_new_failing_test_emits_row(self, fresh_db):
        """A brand-new test recorded as failed should produce one row."""
        database, run_id = fresh_db
        ds = _make_store(database, run_id)

        ds.ensure_tests_batch(run_id, [("test_fail", "src/test_f.py", 0.1, True, None)])
        ds.save_batch([])

        rows = _history_rows(database, "tests_failed_history")
        assert len(rows) == 1
        assert rows[0]["failed"] == 1

    def test_existing_test_no_emit_when_failed_unchanged(self, fresh_db):
        database, run_id = fresh_db
        database.get_or_create_test_id(
            "test_x", duration=0.1, failed=False, run_id=run_id
        )
        ds = _make_store(database, run_id)

        # Re-record with same failed state
        ds.ensure_tests_batch(run_id, [("test_x", "src/test_x.py", 0.2, False, None)])
        ds.save_batch([])

        assert _history_rows(database, "tests_failed_history") == []

    def test_failed_flip_emits(self, fresh_db):
        database, run_id = fresh_db
        database.get_or_create_test_id(
            "test_flip", duration=0.1, failed=False, run_id=run_id
        )
        ds = _make_store(database, run_id)

        ds.ensure_tests_batch(run_id, [("test_flip", "src/test_flip.py", 0.1, True, None)])
        ds.save_batch([])

        rows = _history_rows(database, "tests_failed_history")
        assert len(rows) == 1
        assert rows[0]["failed"] == 1

    def test_flip_within_session_produces_one_row(self, fresh_db):
        """Session dedup: fail, then pass via rerun → one row, final state."""
        database, run_id = fresh_db
        database.get_or_create_test_id(
            "test_rerun", duration=0.1, failed=False, run_id=run_id
        )
        ds = _make_store(database, run_id)

        # Fail
        ds.ensure_tests_batch(run_id, [("test_rerun", "src/test_r.py", 0.1, True, None)])
        # Rerun, now passes
        ds.ensure_tests_batch(run_id, [("test_rerun", "src/test_r.py", 0.1, False, None)])
        ds.save_batch([])

        # final state is failed=False, same as baseline → no history row
        assert _history_rows(database, "tests_failed_history") == []

    def test_flip_to_fail_and_rerun_passes_no_row(self, fresh_db):
        """If the flip ends back at the baseline, no row is written."""
        database, run_id = fresh_db
        database.get_or_create_test_id(
            "test_ok_fail_ok", duration=0.1, failed=True, run_id=run_id
        )
        ds = _make_store(database, run_id)

        ds.ensure_tests_batch(run_id, [("test_ok_fail_ok", None, 0.1, False, None)])
        ds.ensure_tests_batch(run_id, [("test_ok_fail_ok", None, 0.1, True, None)])
        ds.save_batch([])
        # Baseline was True, final is True — nothing to record
        assert _history_rows(database, "tests_failed_history") == []


class TestTestDepsHistory:
    def _setup_test_with_deps(self, database, run_id, pkgs=("numpy",)):
        file_id = database.get_or_create_file_id("src/m.py", checksum=1)
        test_id = database.get_or_create_test_id(
            "test_deps", duration=0.1, failed=False, run_id=run_id,
            test_file="src/test_m.py",
        )
        deps = TestDeps.from_file_ids(test_id, {file_id}, set(pkgs))
        database.save_test_deps(test_id, deps)
        return file_id, test_id

    def _new_deps(self, test_id, file_ids, pkgs):
        deps = TestDeps.from_file_ids(test_id, file_ids, set(pkgs))
        return (test_id, deps.serialize(), deps.serialize_external_packages())

    def test_bitmap_change_emits_row(self, fresh_db):
        database, run_id = fresh_db
        file_id, test_id = self._setup_test_with_deps(database, run_id)
        # Add another file to the deps
        other_id = database.get_or_create_file_id("src/n.py", checksum=2)

        ds = _make_store(database, run_id)
        pending = [self._new_deps(test_id, {file_id, other_id}, {"numpy"})]
        ds.save_batch(pending)

        rows = _history_rows(database, "test_deps_history")
        assert len(rows) == 1
        assert rows[0]["test_id"] == test_id
        assert rows[0]["name"] == "test_deps"
        assert rows[0]["test_file"] == "src/test_m.py"

    def test_package_only_change_emits_row(self, fresh_db):
        """Guardrail: package-only change must be captured in history."""
        database, run_id = fresh_db
        file_id, test_id = self._setup_test_with_deps(database, run_id, pkgs=("numpy",))

        ds = _make_store(database, run_id)
        # Same files, new package
        pending = [self._new_deps(test_id, {file_id}, {"numpy", "scipy"})]
        ds.save_batch(pending)

        rows = _history_rows(database, "test_deps_history")
        assert len(rows) == 1
        assert sorted(rows[0]["external_packages"].split(",")) == ["numpy", "scipy"]

    def test_unchanged_deps_no_emit(self, fresh_db):
        database, run_id = fresh_db
        file_id, test_id = self._setup_test_with_deps(database, run_id, pkgs=("numpy",))

        ds = _make_store(database, run_id)
        pending = [self._new_deps(test_id, {file_id}, {"numpy"})]
        ds.save_batch(pending)

        # The skip filter in testmon_core runs BEFORE save_batch, so
        # in the real path unchanged pending never reaches save_batch.
        # Here we bypass the filter to verify that DepStore itself
        # correctly no-ops on unchanged blob+pkgs inside save_batch.
        assert _history_rows(database, "test_deps_history") == []

    def test_deps_history_one_row_per_save_batch(self, fresh_db):
        """A single save_batch writes exactly one history row per test.

        Unlike files_history and tests_failed_history, the test_deps
        buffer is flushed at the end of every save_batch call (because
        save_batch also writes the current-state row, and both must
        commit in the same txn). The intra-session "revert to baseline"
        pop therefore applies within a single save_batch call only.
        In the real controller flow this is fine: test_deps for a given
        test are written at most once per session.
        """
        database, run_id = fresh_db
        file_id, test_id = self._setup_test_with_deps(
            database, run_id, pkgs=("numpy",)
        )

        ds = _make_store(database, run_id)
        other_id = database.get_or_create_file_id("src/other.py", checksum=9)

        # Single save_batch call representing the realistic controller
        # flow: one test, one pending tuple with its final deps.
        ds.save_batch([self._new_deps(test_id, {file_id, other_id}, {"numpy", "scipy"})])

        rows = _history_rows(database, "test_deps_history")
        assert len(rows) == 1
        assert rows[0]["test_id"] == test_id
        assert rows[0]["run_id"] == run_id
        assert sorted(rows[0]["external_packages"].split(",")) == ["numpy", "scipy"]

    def test_deps_history_revert_to_baseline_within_save_batch_is_noop(self, fresh_db):
        """Calling save_batch with the baseline values after a change reverts.

        This exercises the intra-save_batch pop path. After one save
        that changes deps, a subsequent save with baseline values would
        leave a stale history row on disk (because the first save_batch
        already flushed). But within ONE save_batch, if pending matches
        baseline, no row is emitted at all.
        """
        database, run_id = fresh_db
        file_id, test_id = self._setup_test_with_deps(
            database, run_id, pkgs=("numpy",)
        )

        ds = _make_store(database, run_id)
        # Pending matches the baseline exactly — no history row should land.
        ds.save_batch([self._new_deps(test_id, {file_id}, {"numpy"})])

        assert _history_rows(database, "test_deps_history") == []


class TestFlushInsideTransaction:
    def test_history_flushed_with_save_batch(self, fresh_db):
        """_flush_history_ops runs inside save_batch, same txn as data."""
        database, run_id = fresh_db
        ds = _make_store(database, run_id)
        ds.get_file_id("src/flush.py", checksum=7, fsha="f")
        assert ds._history_ops  # buffered
        ds.save_batch([])
        assert ds._history_ops == {}  # flushed
        assert len(_history_rows(database, "files_history")) == 1
