"""Tests for calculator - depends on calculator AND math_utils."""

import pytest
from src.calculator import Calculator


class TestCalculator:
    def test_add(self):
        calc = Calculator()
        assert calc.calculate(2, '+', 3) == 5

    def test_subtract(self):
        calc = Calculator()
        assert calc.calculate(5, '-', 3) == 2

    def test_multiply(self):
        calc = Calculator()
        assert calc.calculate(3, '*', 4) == 12

    def test_divide(self):
        calc = Calculator()
        assert calc.calculate(10, '/', 2) == 5

    def test_divide_by_zero(self):
        calc = Calculator()
        with pytest.raises(ValueError):
            calc.calculate(5, '/', 0)

    def test_unknown_operator(self):
        calc = Calculator()
        with pytest.raises(ValueError):
            calc.calculate(5, '^', 2)


class TestCalculatorHistory:
    def test_history_recording(self):
        calc = Calculator()
        calc.calculate(2, '+', 3)
        calc.calculate(5, '*', 2)
        history = calc.get_history()
        assert len(history) == 2
        assert history[0] == (2, '+', 3, 5)
        assert history[1] == (5, '*', 2, 10)

    def test_clear_history(self):
        calc = Calculator()
        calc.calculate(2, '+', 3)
        calc.clear_history()
        assert calc.get_history() == []
