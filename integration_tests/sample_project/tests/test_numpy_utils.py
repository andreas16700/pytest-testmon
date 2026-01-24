"""Tests that use ONLY the numpy package.

These tests import from numpy_utils which imports numpy.
They should be affected when numpy changes but NOT when requests changes.
"""

import pytest

from src.numpy_utils import calculate_with_numpy, HAS_NUMPY


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

        result = calculate_with_numpy([1, 2, 3, 4, 5])
        assert "mean" in result
        assert "sum" in result
        assert "max" in result

    def test_numpy_import_check(self):
        """Test that HAS_NUMPY flag is properly set.

        DEPENDS ON: numpy availability
        """
        assert isinstance(HAS_NUMPY, bool)
