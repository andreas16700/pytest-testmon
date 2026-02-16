import ast
import io
import textwrap
import tokenize
import zlib
from functools import lru_cache
import sqlite3
import hashlib
from pathlib import Path
from typing import Optional, Union
from array import array
from subprocess import run, CalledProcessError


def source_encoding(source_bytes: bytes) -> str:
    """Detect the encoding of Python source code.

    Uses Python's tokenize module to detect encoding from coding declarations
    or BOM. Falls back to 'utf-8' if detection fails.
    """
    try:
        encoding, _ = tokenize.detect_encoding(io.BytesIO(source_bytes).readline)
        return encoding
    except (SyntaxError, UnicodeDecodeError):
        return "utf-8"


def to_signed(unsigned33):
    unsigned33 = unsigned33 & 0xFFFFFFFF
    return (unsigned33 ^ 0x80000000) - 0x80000000


def _strip_docstrings(tree):
    """Remove docstrings from AST nodes in-place.

    Docstrings appear as the first statement in module/class/function bodies
    and are Expr nodes containing a Constant string.
    """
    import sys

    def is_docstring(node):
        if not isinstance(node, ast.Expr):
            return False
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            return True
        # Python 3.7 compatibility
        if sys.version_info < (3, 12):
            if hasattr(ast, 'Str') and isinstance(node.value, ast.Str):
                return True
        return False

    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if hasattr(node, 'body') and node.body and is_docstring(node.body[0]):
                node.body = node.body[1:]


def _strip_comment_lines(text: str) -> str:
    """Remove lines that are comments (after leading whitespace, start with '#')."""
    kept = []
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        kept.append(line)
    return "\n".join(kept)


def compute_file_checksum(source_code: str, ext: str = "py") -> int:
    """Compute a single checksum for an entire file.

    For Python files, this parses the AST, strips docstrings, and computes
    a checksum over the AST representation. This means:
    - Comment changes don't affect the checksum
    - Docstring changes don't affect the checksum
    - Only actual code changes trigger re-runs

    For non-Python files, we compute CRC32 of the entire content.
    """
    if ext == "py":
        try:
            tree = ast.parse(source_code)
            _strip_docstrings(tree)
            # Use ast.dump to get a canonical representation
            ast_repr = ast.dump(tree, annotate_fields=False)
            return to_signed(zlib.crc32(ast_repr.encode("utf-8")))
        except SyntaxError:
            # For files with syntax errors, fall back to content hash
            return to_signed(zlib.crc32(source_code.encode("utf-8")))
    else:
        return to_signed(zlib.crc32(source_code.encode("utf-8")))


@lru_cache(300)
def bytes_to_string_and_fsha(byte_stream: bytes) -> Union[str, bytes]:
    # Replace \f because of http://bugs.python.org/issue19035
    byte_stream = byte_stream.replace(b"\f", b" ")
    byte_stream = byte_stream.replace(b"\r\n", b"\n")

    # Try to detect source encoding, fall back to utf-8 for binary/non-Python files
    try:
        encoding = source_encoding(byte_stream)
    except (SyntaxError, UnicodeDecodeError):
        # Binary files or files with invalid encoding declarations
        encoding = "utf-8"

    byte_string = byte_stream.decode(encoding, "replace")
    git_header = b"blob %u\0" % len(byte_string)
    hsh = hashlib.sha1()
    hsh.update(git_header)
    hsh.update(byte_stream)
    if byte_string and byte_string[-1] != "\n":
        byte_string += "\n"
    return byte_string, hsh.hexdigest()


class Module:
    """Represents a source file for fingerprinting.

    With the simplified single-checksum model, each file has exactly one
    checksum that represents the entire file's code (excluding comments
    and docstrings for Python files).
    """

    def __init__(
        self,
        source_code=None,
        mtime=None,
        ext="py",
        fs_fsha=None,
        filename=None,
        rootdir=None,
    ):
        self.filename = filename
        self.rootdir = rootdir
        self.mtime = mtime
        self._source_code = (
            None if source_code is None else textwrap.dedent(source_code)
        )
        self.fs_fsha = (
            fs_fsha or bytes_to_string_and_fsha(bytes(source_code, "utf-8"))[1]
        )
        self.ext = ext
        self._checksum = None

    @property
    def checksum(self) -> int:
        """Get the single file-level checksum."""
        if self._checksum is None:
            self._checksum = compute_file_checksum(self.source_code, self.ext)
        return self._checksum

    @property
    def source_code(self):
        if self._source_code is None:
            self._source_code = read_source_sha(Path(self.rootdir) / self.filename)[0]
        return self._source_code


def read_source_sha(filename: str):
    source_bytes: Optional[bytes]

    try:
        with open(filename, "rb") as file:
            source_bytes = file.read()
    except FileNotFoundError:
        return None, None

    source, fsha = bytes_to_string_and_fsha(source_bytes)
    return source, fsha


def noncached_get_files_shas(directory):
    all_shas = {}
    try:
        result = run(
            ["git", "ls-files", "--stage", "-m", directory],
            capture_output=True,
            universal_newlines=True,
            check=True,
        )
    except (FileNotFoundError, CalledProcessError):
        return all_shas

    modified_files = set()
    for line in result.stdout.splitlines():
        _, hsh, filename_with_junk = line.split(" ", 2)
        _, filename = filename_with_junk.split("\t", 1)
        if filename in all_shas:
            modified_files.add(filename)
        else:
            all_shas[filename] = hsh
    for modified_file in modified_files:
        del all_shas[modified_file]
    return all_shas


@lru_cache()
def get_files_shas(directory):
    return noncached_get_files_shas(directory)


def get_source_sha(directory: "str", filename: "str"):
    try:
        sha = get_files_shas(directory)[filename]
        return (None, sha)
    except KeyError:
        pass
    return read_source_sha(Path(directory) / filename)


def match_fingerprint(module: Module, stored_checksum: int) -> bool:
    """Check if the module's current checksum matches the stored fingerprint.

    With single-file checksums, matching is simply equality comparison.
    """
    return module.checksum == stored_checksum


def create_fingerprint(module: Module) -> int:
    """Create a fingerprint for a module.

    With the simplified model, a fingerprint is just the file's checksum.
    We no longer track which specific lines/functions were covered -
    any change to the file triggers re-runs of all tests that depend on it.
    """
    return module.checksum
