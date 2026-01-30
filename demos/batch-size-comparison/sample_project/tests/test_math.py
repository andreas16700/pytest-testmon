"""Two tests that both call add().

This demonstrates the coverage context limitation:
- Both tests call the same function (add)
- When add() changes, BOTH tests should be re-run
- With batch size 250, only the first test gets the dependency
- With batch size 1, both tests correctly get the dependency
"""
from src.math_utils import add


def test_add_positive():
    """First test - calls add() with positive numbers."""
    assert add(2, 3) == 5


def test_add_negative():
    """Second test - also calls add() with negative numbers."""
    assert add(-1, -2) == -3
