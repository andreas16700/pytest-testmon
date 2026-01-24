"""Tests that use NO external dependencies.

These tests import ONLY from pure_utils which has NO external deps.
They should NOT be affected by any external package changes.
"""

from src.pure_utils import pure_python_function


class TestUsesNeither:
    """Tests that use NO external dependencies.

    These tests should NOT be affected by any external package changes.
    With granular tracking, only tests that actually use a changed package
    will be invalidated.
    """

    def test_pure_python_mean(self):
        """Test pure Python function - no external deps."""
        result = pure_python_function([1, 2, 3, 4, 5])
        assert result == 3.0

    def test_pure_python_empty(self):
        """Test pure Python function with empty input."""
        result = pure_python_function([])
        assert result == 0

    def test_pure_python_single(self):
        """Test pure Python function with single value."""
        result = pure_python_function([42])
        assert result == 42.0
