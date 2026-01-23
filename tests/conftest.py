"""
Shared fixtures for ezmon tests.

Unit tests are in tests/test_process_code.py.
Integration tests are in integration_tests/ and run separately.
"""
import pytest

# Enable pytester fixture for unit tests that may need it
pytest_plugins = ["pytester"]
