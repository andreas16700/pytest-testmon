"""Tests that use ONLY the requests package.

These tests import from requests_utils which imports requests.
They should be affected when requests changes but NOT when numpy changes.
"""

import pytest
from unittest.mock import patch, MagicMock

from src.requests_utils import fetch_data_with_requests, HAS_REQUESTS


class TestUsesDepA:
    """Tests that use dependency A (requests).

    These tests should be affected when requests changes/is removed.
    """

    def test_fetch_data_structure(self):
        """Test that fetch_data returns expected structure.

        DEPENDS ON: requests
        """
        if not HAS_REQUESTS:
            pytest.skip("requests not installed")

        with patch('src.requests_utils.requests') as mock_requests:
            mock_response = MagicMock()
            mock_response.json.return_value = {"data": "test"}
            mock_requests.get.return_value = mock_response

            result = fetch_data_with_requests("http://example.com")
            assert result == {"data": "test"}

    def test_requests_import_check(self):
        """Test that HAS_REQUESTS flag is properly set.

        DEPENDS ON: requests availability
        """
        assert isinstance(HAS_REQUESTS, bool)
