"""
Tests for file_cache checksum computation.

These tests verify that checksum computation works correctly for files
that have changed fshas but unchanged AST checksums (e.g., docstring-only changes).

The bug scenario (run7):
1. Database has file with fsha=A, checksum=X
2. Current HEAD has same file with fsha=B (docstring changed), but same AST checksum=X
3. Working tree is clean (disk == HEAD)
4. batch_get_checksums should return checksum=X, not None
5. get_changed_file_ids should NOT mark this file as changed

Currently these tests FAIL because batch_get_checksums returns None
for files not in _modified, even when we need the source for checksum computation.
"""

import os
import tempfile
import subprocess
import pytest

from ezmon.file_cache import FileInfoCache
from ezmon.process_code import compute_file_checksum


class TestBatchGetChecksumsForUnmodifiedFiles:
    """Test that batch_get_checksums works for files not in _modified."""

    @pytest.fixture
    def git_repo(self, tmp_path):
        """Create a temporary git repo with a Python file."""
        repo_path = tmp_path / "repo"
        repo_path.mkdir()

        # Initialize git repo
        subprocess.run(["git", "init"], cwd=repo_path, capture_output=True, check=True)
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=repo_path, capture_output=True, check=True
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=repo_path, capture_output=True, check=True
        )

        return repo_path

    def test_checksum_computed_for_unmodified_file(self, git_repo):
        """batch_get_checksums should return actual checksum for unmodified files.

        This test verifies that when a file is tracked by git and not modified
        in the working tree, batch_get_checksums still computes and returns
        the actual checksum (not None).

        This is the core bug: get_source_and_fsha returns source=None for
        unmodified files as an optimization, but batch_get_checksums needs
        the source to compute the checksum.
        """
        # Create a Python file
        py_file = git_repo / "module.py"
        source_code = '''def hello():
    """Say hello."""
    return "hello"
'''
        py_file.write_text(source_code)

        # Commit it
        subprocess.run(["git", "add", "module.py"], cwd=git_repo, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "initial"],
            cwd=git_repo, capture_output=True, check=True
        )

        # Create FileInfoCache and refresh
        cache = FileInfoCache(str(git_repo))
        cache.refresh()

        # Verify the file is tracked but not modified
        assert cache.is_tracked("module.py")
        assert "module.py" not in cache._modified

        # Get checksum via batch_get_checksums
        checksums = cache.batch_get_checksums(["module.py"])

        # BUG: Currently returns None because source is None for unmodified files
        # EXPECTED: Should return the actual computed checksum
        expected_checksum = compute_file_checksum(source_code, "py")

        assert checksums["module.py"] is not None, (
            "batch_get_checksums returned None for unmodified file. "
            "This is the bug: it should read the file and compute checksum."
        )
        assert checksums["module.py"] == expected_checksum

    def test_docstring_only_change_same_checksum(self, git_repo):
        """Docstring-only changes should result in same AST checksum.

        This test simulates the run6→run7 scenario:
        1. Commit 1: file with original docstring
        2. Commit 2: file with expanded docstring (same code)
        3. Both commits should have same AST checksum
        4. batch_get_checksums at commit 2 should return the checksum (not None)
        """
        py_file = git_repo / "accessor.py"

        # Commit 1: Original docstring
        source_v1 = '''class DatetimeAccessor:
    """Accessor for datetime properties."""

    def get_year(self):
        return self._data.year
'''
        py_file.write_text(source_v1)
        subprocess.run(["git", "add", "accessor.py"], cwd=git_repo, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "v1"], cwd=git_repo, capture_output=True, check=True)

        # Record checksum at v1
        checksum_v1 = compute_file_checksum(source_v1, "py")

        # Commit 2: Expanded docstring (same code logic)
        source_v2 = '''class DatetimeAccessor:
    """Accessor for datetime properties.

    This accessor provides access to datetime components like year, month, day.

    Parameters
    ----------
    data : Series
        The datetime series.
    """

    def get_year(self):
        return self._data.year
'''
        py_file.write_text(source_v2)
        subprocess.run(["git", "add", "accessor.py"], cwd=git_repo, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "v2 - expanded docstring"], cwd=git_repo, capture_output=True, check=True)

        # Verify checksums are the same (docstring stripped)
        checksum_v2 = compute_file_checksum(source_v2, "py")
        assert checksum_v1 == checksum_v2, "Docstring-only change should not affect AST checksum"

        # Now test FileInfoCache at v2
        cache = FileInfoCache(str(git_repo))
        cache.refresh()

        # File should be tracked but not modified
        assert cache.is_tracked("accessor.py")
        assert "accessor.py" not in cache._modified

        # Get checksum via batch_get_checksums
        checksums = cache.batch_get_checksums(["accessor.py"])

        # BUG: Currently returns None
        # EXPECTED: Should return checksum_v2 (which equals checksum_v1)
        assert checksums["accessor.py"] is not None, (
            "batch_get_checksums returned None. This causes false positive change detection "
            "when comparing against a database from an older commit."
        )
        assert checksums["accessor.py"] == checksum_v2


class TestChangeDetectionWithDocstringChanges:
    """Test the full change detection flow with docstring-only changes.

    These tests simulate the run6→run7 bug where:
    - File fsha changed (docstring added)
    - File AST checksum unchanged
    - But file is incorrectly marked as "changed" because checksum=None
    """

    @pytest.fixture
    def git_repo_with_history(self, tmp_path):
        """Create a git repo with two commits (simulating run6→run7)."""
        repo_path = tmp_path / "repo"
        repo_path.mkdir()

        subprocess.run(["git", "init"], cwd=repo_path, capture_output=True, check=True)
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=repo_path, capture_output=True, check=True
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=repo_path, capture_output=True, check=True
        )

        py_file = repo_path / "core.py"

        # Commit 1 (like run6): minimal docstring
        source_v1 = '''def process(data):
    """Process data."""
    return data * 2
'''
        py_file.write_text(source_v1)
        subprocess.run(["git", "add", "core.py"], cwd=repo_path, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "v1"], cwd=repo_path, capture_output=True, check=True)

        # Get commit SHA for v1
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_path, capture_output=True, text=True, check=True
        )
        commit_v1 = result.stdout.strip()

        # Commit 2 (like run7): expanded docstring
        source_v2 = '''def process(data):
    """Process data.

    Parameters
    ----------
    data : int
        The input data.

    Returns
    -------
    int
        The processed result.
    """
    return data * 2
'''
        py_file.write_text(source_v2)
        subprocess.run(["git", "add", "core.py"], cwd=repo_path, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "v2 - docs"], cwd=repo_path, capture_output=True, check=True)

        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_path, capture_output=True, text=True, check=True
        )
        commit_v2 = result.stdout.strip()

        # Get fshas for both versions
        result = subprocess.run(
            ["git", "ls-tree", commit_v1, "core.py"],
            cwd=repo_path, capture_output=True, text=True, check=True
        )
        fsha_v1 = result.stdout.split()[2]

        result = subprocess.run(
            ["git", "ls-tree", commit_v2, "core.py"],
            cwd=repo_path, capture_output=True, text=True, check=True
        )
        fsha_v2 = result.stdout.split()[2]

        checksum = compute_file_checksum(source_v1, "py")
        assert checksum == compute_file_checksum(source_v2, "py"), "Test setup: checksums should match"

        return {
            "repo_path": repo_path,
            "commit_v1": commit_v1,
            "commit_v2": commit_v2,
            "fsha_v1": fsha_v1,
            "fsha_v2": fsha_v2,
            "checksum": checksum,
            "source_v1": source_v1,
            "source_v2": source_v2,
        }

    def test_fsha_differs_but_checksum_same(self, git_repo_with_history):
        """Verify test setup: fsha differs but checksum is the same."""
        data = git_repo_with_history

        # fshas should be different (content changed)
        assert data["fsha_v1"] != data["fsha_v2"], "fshas should differ for docstring change"

        # But checksums should be the same (docstring stripped from AST)
        checksum_v1 = compute_file_checksum(data["source_v1"], "py")
        checksum_v2 = compute_file_checksum(data["source_v2"], "py")
        assert checksum_v1 == checksum_v2, "Checksums should match (docstring stripped)"

    def test_batch_get_checksums_returns_value_not_none(self, git_repo_with_history):
        """batch_get_checksums must return actual checksum, not None.

        This is the core assertion for the bug fix.
        """
        data = git_repo_with_history

        # We're at commit v2 (HEAD)
        cache = FileInfoCache(str(data["repo_path"]))
        cache.refresh()

        checksums = cache.batch_get_checksums(["core.py"])

        # THE BUG: This currently returns None
        assert checksums["core.py"] is not None, (
            "CRITICAL BUG: batch_get_checksums returns None for unmodified files. "
            "This causes docstring-only changes to trigger full test suite re-runs."
        )

    def test_simulated_change_detection_no_false_positive(self, git_repo_with_history):
        """Simulate the full change detection: should NOT detect a change.

        This simulates what happens in determine_stable():
        1. Database has fsha_v1 and checksum from commit v1
        2. Current HEAD is at commit v2
        3. File has different fsha but same checksum
        4. Should NOT be marked as changed
        """
        data = git_repo_with_history

        # Simulate database state from v1
        db_fsha = data["fsha_v1"]
        db_checksum = data["checksum"]

        # Current state at v2
        cache = FileInfoCache(str(data["repo_path"]))
        cache.refresh()

        # Step 1: Detect fsha change (this part works correctly)
        current_fsha = cache.get_fsha("core.py")
        fsha_changed = (db_fsha != current_fsha)
        assert fsha_changed, "Test setup: fsha should have changed"

        # Step 2: For files with changed fsha, compute checksum
        checksums = cache.batch_get_checksums(["core.py"])
        current_checksum = checksums["core.py"]

        # Step 3: Compare checksums to determine if file really changed
        # THE BUG: current_checksum is None, so this comparison fails
        if current_checksum is None:
            pytest.fail(
                "BUG REPRODUCED: batch_get_checksums returned None. "
                "In the real code, this causes: None != db_checksum → file marked as changed → "
                "all dependent tests selected (overselection)."
            )

        # If checksum is not None, verify it matches (no real change)
        file_really_changed = (db_checksum != current_checksum)
        assert not file_really_changed, (
            f"False positive! Checksums should match for docstring-only change. "
            f"DB: {db_checksum}, Current: {current_checksum}"
        )


class TestGetSourceAndFsha:
    """Test get_source_and_fsha behavior for unmodified files."""

    @pytest.fixture
    def git_repo(self, tmp_path):
        """Create a simple git repo."""
        repo_path = tmp_path / "repo"
        repo_path.mkdir()

        subprocess.run(["git", "init"], cwd=repo_path, capture_output=True, check=True)
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=repo_path, capture_output=True, check=True
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=repo_path, capture_output=True, check=True
        )

        py_file = repo_path / "test.py"
        py_file.write_text("x = 1\n")
        subprocess.run(["git", "add", "test.py"], cwd=repo_path, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo_path, capture_output=True, check=True)

        return repo_path

    def test_get_source_and_fsha_unmodified_file(self, git_repo):
        """get_source_and_fsha returns source=None for unmodified files as an optimization.

        This is intentional: for batch_get_fshas, we don't need the source content,
        just the fsha which we can get from git without disk I/O.

        The key is that batch_get_checksums handles this correctly by reading the
        file when source is needed for checksum computation.
        """
        cache = FileInfoCache(str(git_repo))
        cache.refresh()

        source, fsha, mtime = cache.get_source_and_fsha("test.py")

        # source=None is the expected optimization for unmodified files
        # batch_get_checksums handles this by reading the file when needed
        assert source is None, "Expected source=None for unmodified files (optimization)"
        assert fsha is not None, "fsha should be available from git"

        # Verify that batch_get_checksums still works correctly despite source=None
        checksums = cache.batch_get_checksums(["test.py"])
        assert checksums.get("test.py") is not None, (
            "batch_get_checksums should read the file when source=None"
        )
