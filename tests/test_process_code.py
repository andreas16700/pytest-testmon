"""
Unit tests for ezmon's code processing and fingerprinting logic.

These tests verify that:
1. File checksums are correctly calculated
2. Comments and docstrings are stripped before checksum calculation
3. Fingerprints match/mismatch as expected
"""
import pytest
from ezmon.process_code import (
    Module,
    compute_file_checksum,
    match_fingerprint,
    create_fingerprint,
    _strip_docstrings,
)
import ast


class TestFileChecksum:
    """Tests for single-file checksum calculation."""

    def test_identical_code_same_checksum(self):
        """Identical code should produce identical checksums."""
        code = "def foo():\n    return 1\n"
        checksum1 = compute_file_checksum(code)
        checksum2 = compute_file_checksum(code)
        assert checksum1 == checksum2

    def test_different_code_different_checksum(self):
        """Different code should produce different checksums."""
        code1 = "def foo():\n    return 1\n"
        code2 = "def foo():\n    return 2\n"
        checksum1 = compute_file_checksum(code1)
        checksum2 = compute_file_checksum(code2)
        assert checksum1 != checksum2

    def test_cosmetic_whitespace_does_not_matter(self):
        """Cosmetic whitespace differences should NOT affect checksums.

        The AST normalizes whitespace in expressions, so extra spaces
        in return statements etc. don't change the checksum. This is
        desirable - cosmetic changes shouldn't trigger test re-runs.
        """
        code1 = "def foo():\n    return  1\n"  # Extra space
        code2 = "def foo():\n    return 1\n"
        checksum1 = compute_file_checksum(code1)
        checksum2 = compute_file_checksum(code2)
        # AST normalizes whitespace, so checksums should match
        assert checksum1 == checksum2

    def test_comment_changes_do_not_affect_checksum(self):
        """Comments should not affect the checksum (AST-based)."""
        code_with_comment = '''
def foo():
    # This is a comment
    return 1
'''
        code_without_comment = '''
def foo():
    return 1
'''
        checksum1 = compute_file_checksum(code_with_comment)
        checksum2 = compute_file_checksum(code_without_comment)
        # AST doesn't include comments, so checksums should match
        assert checksum1 == checksum2

    def test_syntax_error_falls_back_to_content_hash(self):
        """Files with syntax errors should still get a checksum."""
        bad_code = "def broken(\n"  # Syntax error
        checksum = compute_file_checksum(bad_code)
        # Should return a valid checksum (content-based fallback)
        assert isinstance(checksum, int)

    def test_non_python_file_uses_content_hash(self):
        """Non-Python files should use content-based hashing."""
        content = "Some text content\nthat is not Python\n"
        checksum = compute_file_checksum(content, ext="txt")
        assert isinstance(checksum, int)

    def test_non_python_file_different_content(self):
        """Non-Python files with different content have different checksums."""
        content1 = "version: 1.0\n"
        content2 = "version: 2.0\n"
        checksum1 = compute_file_checksum(content1, ext="yaml")
        checksum2 = compute_file_checksum(content2, ext="yaml")
        assert checksum1 != checksum2


class TestDocstringStripping:
    """Tests for docstring stripping before checksum calculation."""

    def test_module_docstring_stripped(self):
        """Module docstrings should not affect checksums."""
        code_with_docstring = '''
"""Module docstring."""

def foo():
    return 42
'''
        code_without_docstring = '''
def foo():
    return 42
'''
        checksum1 = compute_file_checksum(code_with_docstring)
        checksum2 = compute_file_checksum(code_without_docstring)
        assert checksum1 == checksum2

    def test_function_docstring_stripped(self):
        """Function docstrings should not affect checksums."""
        code_with_docstring = '''
def foo():
    """This function does something."""
    return 42
'''
        code_without_docstring = '''
def foo():
    return 42
'''
        checksum1 = compute_file_checksum(code_with_docstring)
        checksum2 = compute_file_checksum(code_without_docstring)
        assert checksum1 == checksum2

    def test_class_docstring_stripped(self):
        """Class docstrings should not affect checksums."""
        code_with_docstring = '''
class Foo:
    """This class does something."""

    def method(self):
        return 42
'''
        code_without_docstring = '''
class Foo:
    def method(self):
        return 42
'''
        checksum1 = compute_file_checksum(code_with_docstring)
        checksum2 = compute_file_checksum(code_without_docstring)
        assert checksum1 == checksum2

    def test_different_docstrings_same_checksum(self):
        """Changing only the docstring should not change the checksum."""
        code_v1 = '''
def foo():
    """Version 1 docstring."""
    return 42
'''
        code_v2 = '''
def foo():
    """Completely different docstring with more details."""
    return 42
'''
        checksum1 = compute_file_checksum(code_v1)
        checksum2 = compute_file_checksum(code_v2)
        assert checksum1 == checksum2

    def test_logic_change_still_detected(self):
        """Actual code changes should still be detected even with docstrings."""
        code_v1 = '''
def foo():
    """Same docstring."""
    return 42
'''
        code_v2 = '''
def foo():
    """Same docstring."""
    return 43  # Changed!
'''
        checksum1 = compute_file_checksum(code_v1)
        checksum2 = compute_file_checksum(code_v2)
        assert checksum1 != checksum2


class TestModule:
    """Tests for the Module class."""

    def test_module_has_single_checksum(self):
        """A Module should have a single checksum property."""
        source = '''
def hello():
    return "hello"
'''
        module = Module(source_code=source)
        checksum = module.checksum
        assert isinstance(checksum, int)

    def test_module_checksum_cached(self):
        """The checksum should be cached after first access."""
        source = '''
def hello():
    return "hello"
'''
        module = Module(source_code=source)
        checksum1 = module.checksum
        checksum2 = module.checksum
        assert checksum1 == checksum2

    def test_module_with_multiple_functions(self):
        """A module with multiple functions should have one checksum."""
        source = '''
def foo():
    return 1

def bar():
    return 2

def baz():
    return 3
'''
        module = Module(source_code=source)
        checksum = module.checksum
        assert isinstance(checksum, int)

    def test_module_checksum_changes_with_any_function(self):
        """Changing any function should change the module checksum."""
        source1 = '''
def foo():
    return 1

def bar():
    return 2
'''
        source2 = '''
def foo():
    return 1

def bar():
    return 3  # Changed!
'''
        module1 = Module(source_code=source1)
        module2 = Module(source_code=source2)
        assert module1.checksum != module2.checksum


class TestFingerprintMatching:
    """Tests for fingerprint matching logic."""

    def test_unchanged_code_matches(self):
        """Unchanged code should match its fingerprint."""
        source = '''
def foo():
    return 1
'''
        module = Module(source_code=source)
        fingerprint = create_fingerprint(module)

        # Same source should match
        assert match_fingerprint(module, fingerprint)

    def test_changed_code_does_not_match(self):
        """Changed code should not match old fingerprint."""
        source1 = '''
def foo():
    return 1
'''
        source2 = '''
def foo():
    return 2
'''
        module1 = Module(source_code=source1)
        fingerprint1 = create_fingerprint(module1)

        module2 = Module(source_code=source2)
        # New module should NOT match old fingerprint
        assert not match_fingerprint(module2, fingerprint1)

    def test_added_function_changes_fingerprint(self):
        """Adding a new function should change the fingerprint."""
        source1 = '''
def foo():
    return 1
'''
        source2 = '''
def foo():
    return 1

def bar():
    return 2
'''
        module1 = Module(source_code=source1)
        fingerprint1 = create_fingerprint(module1)

        module2 = Module(source_code=source2)
        # Adding a function changes the checksum
        assert not match_fingerprint(module2, fingerprint1)


class TestCreateFingerprint:
    """Tests for creating fingerprints."""

    def test_fingerprint_is_integer(self):
        """Fingerprint should be a single integer."""
        source = '''
def foo():
    return 1
'''
        module = Module(source_code=source)
        fingerprint = create_fingerprint(module)
        assert isinstance(fingerprint, int)

    def test_fingerprint_matches_checksum(self):
        """Fingerprint should equal the module's checksum."""
        source = '''
def foo():
    return 1
'''
        module = Module(source_code=source)
        fingerprint = create_fingerprint(module)
        assert fingerprint == module.checksum


class TestModuleChanges:
    """Tests for detecting module-level changes."""

    def test_module_constant_change_detected(self):
        """Changing a module-level constant should change the checksum."""
        source1 = '''
CONSTANT = 42

def foo():
    return 1
'''
        source2 = '''
CONSTANT = 100

def foo():
    return 1
'''
        module1 = Module(source_code=source1)
        module2 = Module(source_code=source2)
        assert module1.checksum != module2.checksum

    def test_import_change_detected(self):
        """Changing imports should change the checksum."""
        source1 = '''
import os

def foo():
    return 1
'''
        source2 = '''
import sys

def foo():
    return 1
'''
        module1 = Module(source_code=source1)
        module2 = Module(source_code=source2)
        assert module1.checksum != module2.checksum


class TestSyntaxErrorHandling:
    """Tests for handling invalid Python syntax."""

    def test_syntax_error_produces_valid_checksum(self):
        """Invalid syntax should produce a valid checksum, not crash."""
        source = '''
def broken(
    # Missing closing paren
'''
        module = Module(source_code=source)
        checksum = module.checksum
        # Should return a valid checksum (content-based fallback)
        assert isinstance(checksum, int)


class TestNonPythonFiles:
    """Tests for non-Python file handling."""

    def test_non_py_file_has_checksum(self):
        """Non-Python files should have a checksum."""
        source = '''
Some text content
that is not Python
'''
        module = Module(source_code=source, ext="txt")
        checksum = module.checksum
        assert isinstance(checksum, int)

    def test_non_py_file_content_change_detected(self):
        """Content changes in non-Python files should change the checksum."""
        source1 = "version: 1.0\n"
        source2 = "version: 2.0\n"
        module1 = Module(source_code=source1, ext="yaml")
        module2 = Module(source_code=source2, ext="yaml")
        assert module1.checksum != module2.checksum
