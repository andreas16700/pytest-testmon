"""Tests for formatter - depends on formatter AND string_utils."""

from src.formatter import Formatter


class TestFormatter:
    def test_upper_style(self):
        fmt = Formatter(style="upper")
        assert fmt.format("hello") == "HELLO"

    def test_lower_style(self):
        fmt = Formatter(style="lower")
        assert fmt.format("HELLO") == "hello"

    def test_title_style(self):
        fmt = Formatter(style="title")
        assert fmt.format("hello") == "Hello"

    def test_default_style(self):
        fmt = Formatter()
        assert fmt.format("hello") == "HELLO"

    def test_unknown_style(self):
        fmt = Formatter(style="unknown")
        assert fmt.format("hello") == "hello"


class TestFormatterStyleChange:
    def test_change_style(self):
        fmt = Formatter(style="upper")
        assert fmt.format("hello") == "HELLO"
        fmt.set_style("lower")
        assert fmt.format("HELLO") == "hello"
