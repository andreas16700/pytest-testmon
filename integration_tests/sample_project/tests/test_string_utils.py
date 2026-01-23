"""Tests for string_utils - only depends on string_utils."""

from src.string_utils import uppercase, lowercase, capitalize


class TestUppercase:
    def test_lowercase_input(self):
        assert uppercase("hello") == "HELLO"

    def test_mixed_input(self):
        assert uppercase("Hello World") == "HELLO WORLD"


class TestLowercase:
    def test_uppercase_input(self):
        assert lowercase("HELLO") == "hello"

    def test_mixed_input(self):
        assert lowercase("Hello World") == "hello world"


class TestCapitalize:
    def test_lowercase_input(self):
        assert capitalize("hello") == "Hello"

    def test_already_capitalized(self):
        assert capitalize("Hello") == "Hello"
