"""Tests for dynamic_loader - tests dynamic imports via importlib.import_module().

This module tests that the dependency tracker correctly captures dependencies
when modules are imported dynamically using importlib.import_module() with
string arguments stored in variables.
"""

import pytest
from src.dynamic_loader import (
    compute_with_dynamic_import,
    format_with_dynamic_import,
    get_math_add,
    get_string_capitalize,
)


class TestDynamicMathImport:
    """Tests that use dynamically imported math_utils functions."""

    def test_dynamic_add(self):
        """Use add function obtained via dynamic import."""
        add = get_math_add()
        assert add(2, 3) == 5

    def test_compute_with_dynamic_import(self):
        """Compute using dynamically imported module."""
        result = compute_with_dynamic_import(10, 20)
        assert result == 30


class TestDynamicStringImport:
    """Tests that use dynamically imported string_utils functions."""

    def test_dynamic_capitalize(self):
        """Use capitalize function obtained via dynamic import."""
        capitalize = get_string_capitalize()
        assert capitalize("hello") == "Hello"

    def test_format_with_dynamic_import(self):
        """Format using dynamically imported module."""
        result = format_with_dynamic_import("world")
        assert result == "World"
