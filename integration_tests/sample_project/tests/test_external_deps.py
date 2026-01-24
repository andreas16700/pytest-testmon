"""Tests for external_deps - demonstrates granular external dependency tracking.

ezmon tracks external packages per-test, enabling selective invalidation
when specific packages change (instead of re-running all tests).

Test categories:
- test_uses_neither: Uses NO external deps (should NOT be affected by package changes)
- test_uses_dep_a: Uses only dependency A (requests)
- test_uses_dep_b: Uses only dependency B (numpy)
- test_uses_both: Uses both dependencies A and B

Expected behavior with granular tracking (NOW IMPLEMENTED):
- Remove requests: test_uses_dep_a and test_uses_both should re-run
- Remove numpy: test_uses_dep_b and test_uses_both should re-run
- Add new package: No tests should run (unless they use it)
- Update requests: Only test_uses_dep_a and test_uses_both should re-run
- test_uses_neither: Should NEVER be affected by external package changes
"""

import pytest
from unittest.mock import patch, MagicMock

from src.external_deps import (
    pure_python_function,
    HAS_REQUESTS,
    HAS_NUMPY,
)


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


class TestUsesDepA:
    """Tests that use dependency A (requests).

    These tests should be affected when requests changes/is removed.
    """

    def test_fetch_data_structure(self):
        """Test that fetch_data returns expected structure.

        DEPENDS ON: requests
        """
        # Mock the requests call
        with patch('src.external_deps.HAS_REQUESTS', True):
            with patch('src.external_deps.requests') as mock_requests:
                mock_response = MagicMock()
                mock_response.json.return_value = {"data": "test"}
                mock_requests.get.return_value = mock_response

                # Import and call with mocked requests
                from src.external_deps import fetch_data_with_requests
                # Force reimport with mocked HAS_REQUESTS
                import importlib
                import src.external_deps
                original_has = src.external_deps.HAS_REQUESTS
                src.external_deps.HAS_REQUESTS = True

                try:
                    result = fetch_data_with_requests("http://example.com")
                    assert result == {"data": "test"}
                except ImportError:
                    # If requests isn't actually installed, skip
                    pytest.skip("requests not installed")
                finally:
                    src.external_deps.HAS_REQUESTS = original_has

    def test_requests_import_check(self):
        """Test that HAS_REQUESTS flag is properly set.

        DEPENDS ON: requests availability
        """
        # This test just checks the import flag
        assert isinstance(HAS_REQUESTS, bool)


class TestUsesDepB:
    """Tests that use dependency B (numpy).

    These tests should be affected when numpy changes/is removed.
    """

    def test_numpy_calculation_structure(self):
        """Test calculation returns expected keys.

        DEPENDS ON: numpy
        """
        if not HAS_NUMPY:
            pytest.skip("numpy not installed")

        from src.external_deps import calculate_with_numpy
        result = calculate_with_numpy([1, 2, 3, 4, 5])
        assert "mean" in result
        assert "sum" in result
        assert "max" in result

    def test_numpy_import_check(self):
        """Test that HAS_NUMPY flag is properly set.

        DEPENDS ON: numpy availability
        """
        assert isinstance(HAS_NUMPY, bool)


class TestUsesBoth:
    """Tests that use BOTH dependencies (requests and numpy).

    These tests should be affected when either dependency changes.
    """

    def test_process_with_both_structure(self):
        """Test process_with_both returns a list.

        DEPENDS ON: requests, numpy
        """
        if not HAS_REQUESTS or not HAS_NUMPY:
            pytest.skip("Both requests and numpy required")

        from src.external_deps import process_with_both
        result = process_with_both("http://example.com", 2)
        assert isinstance(result, list)

    def test_combined_deps_import_check(self):
        """Test that both import flags are booleans.

        DEPENDS ON: requests, numpy availability
        """
        assert isinstance(HAS_REQUESTS, bool)
        assert isinstance(HAS_NUMPY, bool)
