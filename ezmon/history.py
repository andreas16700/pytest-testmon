"""
Read-only query helpers for the versioning history tables.

All functions accept a ``db`` argument (an ``ezmon.db.DB`` instance)
and return plain data (dataclasses, lists, dicts). No mutations,
no dependencies on DepStore or pytest — this module is safe to call
from scripts, ez-viz, or ad-hoc debugging.

The history tables are append-only; these helpers only read them.
When versioning is disabled or a DB has no history rows, every
helper returns an empty result rather than raising.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Set

from ezmon.bitmap_deps import TestDeps


# ---- Result types ----

@dataclass
class FileVersion:
    file_id: int
    run_id: int
    path: str
    file_type: str
    checksum: Optional[int]
    fsha: Optional[str]

    @property
    def is_tombstone(self) -> bool:
        return self.checksum is None and self.fsha is None


@dataclass
class TestFailedVersion:
    test_id: int
    run_id: int
    name: str
    test_file: Optional[str]
    failed: bool


@dataclass
class TestDepsVersion:
    test_id: int
    run_id: int
    name: str
    test_file: Optional[str]
    file_bitmap: Optional[bytes]
    external_packages: str

    def file_ids(self) -> Set[int]:
        """Deserialize the bitmap to a set of file IDs."""
        if self.file_bitmap is None:
            return set()
        deps = TestDeps.deserialize(self.test_id, self.file_bitmap, self.external_packages)
        return set(deps.file_ids)


@dataclass
class FileChange:
    path: str
    file_id: int
    old_checksum: Optional[int]
    new_checksum: Optional[int]
    old_fsha: Optional[str]
    new_fsha: Optional[str]
    run_id: int


@dataclass
class SelectionExplanation:
    """Why a test was selected in a given run."""
    test_name: str
    run_id: int
    # Files that changed between the prior run and this run AND that
    # intersect the test's dependency bitmap from the prior run.
    triggering_files: List[str]
    # Was the test marked failed in the prior run?
    was_failed: bool
    # Is this the test's first run (no prior deps)?
    is_new: bool


# ---- Point-in-time lookups ----

def get_file_at_run(db, path: str, run_id: int) -> Optional[FileVersion]:
    """Return the file's state as of ``run_id``.

    Finds the most recent files_history row for ``path`` with
    ``run_id <= target``. Returns None if the file has no recorded
    history at or before that run.
    """
    row = db.con.execute(
        "SELECT file_id, run_id, path, file_type, checksum, fsha "
        "FROM files_history WHERE path = ? AND run_id <= ? "
        "ORDER BY run_id DESC LIMIT 1",
        (path, run_id),
    ).fetchone()
    if row is None:
        return None
    return FileVersion(
        file_id=row["file_id"],
        run_id=row["run_id"],
        path=row["path"],
        file_type=row["file_type"],
        checksum=row["checksum"],
        fsha=row["fsha"],
    )


def get_test_deps_at_run(db, test_name: str, run_id: int) -> Optional[TestDepsVersion]:
    """Return the test's dependency state as of ``run_id``.

    Finds the most recent test_deps_history row for ``test_name`` with
    ``run_id <= target``.
    """
    row = db.con.execute(
        "SELECT test_id, run_id, name, test_file, file_bitmap, external_packages "
        "FROM test_deps_history WHERE name = ? AND run_id <= ? "
        "ORDER BY run_id DESC LIMIT 1",
        (test_name, run_id),
    ).fetchone()
    if row is None:
        return None
    return TestDepsVersion(
        test_id=row["test_id"],
        run_id=row["run_id"],
        name=row["name"],
        test_file=row["test_file"],
        file_bitmap=bytes(row["file_bitmap"]) if row["file_bitmap"] else None,
        external_packages=row["external_packages"] or "",
    )


# ---- Diff queries ----

def get_file_changes_between(db, run_a: int, run_b: int) -> List[FileChange]:
    """List files whose checksum or fsha changed between run_a and run_b.

    Returns one ``FileChange`` per file that has a history row with
    ``run_a < run_id <= run_b``. The ``old_*`` values come from the
    most recent history row at or before ``run_a``; the ``new_*``
    values come from the most recent row at or before ``run_b``.
    """
    # Files that have at least one history row in the (run_a, run_b] window
    rows = db.con.execute(
        "SELECT DISTINCT path, file_id FROM files_history "
        "WHERE run_id > ? AND run_id <= ?",
        (run_a, run_b),
    ).fetchall()

    result = []
    for row in rows:
        path, file_id = row["path"], row["file_id"]
        old = get_file_at_run(db, path, run_a)
        new = get_file_at_run(db, path, run_b)
        if new is None:
            continue
        result.append(FileChange(
            path=path,
            file_id=file_id,
            old_checksum=old.checksum if old else None,
            new_checksum=new.checksum,
            old_fsha=old.fsha if old else None,
            new_fsha=new.fsha,
            run_id=new.run_id,
        ))
    return result


def get_test_deps_changes_between(db, run_a: int, run_b: int) -> List[TestDepsVersion]:
    """List tests whose deps or external packages changed between runs.

    Returns the test_deps_history rows with ``run_a < run_id <= run_b``.
    """
    rows = db.con.execute(
        "SELECT test_id, run_id, name, test_file, file_bitmap, external_packages "
        "FROM test_deps_history WHERE run_id > ? AND run_id <= ? "
        "ORDER BY name",
        (run_a, run_b),
    ).fetchall()

    return [
        TestDepsVersion(
            test_id=row["test_id"],
            run_id=row["run_id"],
            name=row["name"],
            test_file=row["test_file"],
            file_bitmap=bytes(row["file_bitmap"]) if row["file_bitmap"] else None,
            external_packages=row["external_packages"] or "",
        )
        for row in rows
    ]


# ---- Analysis ----

def explain_selection(db, test_name: str, run_id: int) -> SelectionExplanation:
    """Explain why test ``test_name`` was selected in run ``run_id``.

    Reconstructs the selection decision by:
    1. Finding the test's dependency bitmap from the prior run.
    2. Finding all files that changed between the prior run and this run.
    3. Intersecting the two to identify triggering files.
    4. Checking if the test was marked ``failed`` in the prior run.

    Returns a ``SelectionExplanation`` even when history is incomplete
    (the ``triggering_files`` list may be empty and ``is_new`` True).
    """
    # Find the prior run
    prior_run = db.con.execute(
        "SELECT id FROM runs WHERE id < ? ORDER BY id DESC LIMIT 1",
        (run_id,),
    ).fetchone()

    if prior_run is None:
        return SelectionExplanation(
            test_name=test_name,
            run_id=run_id,
            triggering_files=[],
            was_failed=False,
            is_new=True,
        )

    prior_run_id = prior_run["id"]

    # Get test's deps from the prior run
    prior_deps = get_test_deps_at_run(db, test_name, prior_run_id)
    if prior_deps is None:
        return SelectionExplanation(
            test_name=test_name,
            run_id=run_id,
            triggering_files=[],
            was_failed=False,
            is_new=True,
        )

    prior_file_ids = prior_deps.file_ids()

    # Get files that changed between prior and current run
    file_changes = get_file_changes_between(db, prior_run_id, run_id)

    # Intersect: which changed files does this test depend on?
    changed_file_ids = {fc.file_id for fc in file_changes}
    triggering_ids = prior_file_ids & changed_file_ids
    triggering_paths = [
        fc.path for fc in file_changes if fc.file_id in triggering_ids
    ]

    # Was the test marked failed?
    failed_row = db.con.execute(
        "SELECT failed FROM tests_failed_history "
        "WHERE name = ? AND run_id <= ? "
        "ORDER BY run_id DESC LIMIT 1",
        (test_name, prior_run_id),
    ).fetchone()
    was_failed = bool(failed_row["failed"]) if failed_row else False

    # Fall back to the current tests table if no history row
    if failed_row is None:
        current = db.con.execute(
            "SELECT failed FROM tests WHERE name = ?",
            (test_name,),
        ).fetchone()
        if current:
            was_failed = bool(current["failed"])

    return SelectionExplanation(
        test_name=test_name,
        run_id=run_id,
        triggering_files=sorted(triggering_paths),
        was_failed=was_failed,
        is_new=False,
    )


# ---- Maintenance ----

@dataclass
class PruneStats:
    """Summary of a prune operation."""
    files_deleted: int
    tests_failed_deleted: int
    test_deps_deleted: int


def prune_history_before_run(db, keep_from_run_id: int) -> PruneStats:
    """Delete all history rows with ``run_id < keep_from_run_id``.

    This is an explicit, user-initiated operation — NOT called
    automatically. Users decide their own retention policy.

    The prune runs inside the DB's connection context. Callers should
    call ``db.con.commit()`` afterward if they want the deletion to
    persist, or rely on the plugin's normal session-end commit.
    """
    con = db.con
    f = con.execute(
        "DELETE FROM files_history WHERE run_id < ?", (keep_from_run_id,)
    ).rowcount
    t = con.execute(
        "DELETE FROM tests_failed_history WHERE run_id < ?", (keep_from_run_id,)
    ).rowcount
    d = con.execute(
        "DELETE FROM test_deps_history WHERE run_id < ?", (keep_from_run_id,)
    ).rowcount
    return PruneStats(files_deleted=f, tests_failed_deleted=t, test_deps_deleted=d)


def file_churn(db, since_run: int = 0) -> List[Dict[str, object]]:
    """Return files sorted by number of distinct versions since ``since_run``.

    Each entry is ``{"path": str, "versions": int, "last_run_id": int}``.
    """
    rows = db.con.execute(
        "SELECT path, COUNT(*) as versions, MAX(run_id) as last_run_id "
        "FROM files_history WHERE run_id > ? "
        "GROUP BY path ORDER BY versions DESC",
        (since_run,),
    ).fetchall()
    return [
        {"path": row["path"], "versions": row["versions"], "last_run_id": row["last_run_id"]}
        for row in rows
    ]
