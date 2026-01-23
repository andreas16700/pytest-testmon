"""Calculator that uses math_utils - demonstrates dependency chain."""

from src.math_utils import add, subtract, multiply, divide


class Calculator:
    def __init__(self):
        self.history = []

    def calculate(self, a, op, b):
        if op == '+':
            result = add(a, b)
        elif op == '-':
            result = subtract(a, b)
        elif op == '*':
            result = multiply(a, b)
        elif op == '/':
            result = divide(a, b)
        else:
            raise ValueError(f"Unknown operator: {op}")

        self.history.append((a, op, b, result))
        return result

    def get_history(self):
        return self.history.copy()

    def clear_history(self):
        self.history = []
