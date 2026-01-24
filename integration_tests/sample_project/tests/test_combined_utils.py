"""Tests that use BOTH requests and numpy packages.

These tests import from combined_utils which imports both packages.
They should be affected when EITHER package changes.
"""

import pytest

from src.combined_utils import process_with_both, HAS_REQUESTS, HAS_NUMPY


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

        result = process_with_both("http://example.com", 2)
        assert isinstance(result, list)

    def test_combined_deps_import_check(self):
        """Test that both import flags are booleans.

        DEPENDS ON: requests, numpy availability
        """
        assert isinstance(HAS_REQUESTS, bool)
        assert isinstance(HAS_NUMPY, bool)
