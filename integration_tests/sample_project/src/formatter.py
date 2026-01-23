"""Formatter that uses string_utils - demonstrates dependency chain."""

from src.string_utils import uppercase, lowercase, capitalize


class Formatter:
    def __init__(self, style="upper"):
        self.style = style

    def format(self, text):
        if self.style == "upper":
            return uppercase(text)
        elif self.style == "lower":
            return lowercase(text)
        elif self.style == "title":
            return capitalize(text)
        else:
            return text

    def set_style(self, style):
        self.style = style
