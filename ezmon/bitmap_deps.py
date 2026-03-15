"""
Bitmap-based test dependency storage for ezmon.

This module provides efficient storage and querying of test dependencies
using Roaring bitmaps with zstd compression.

Key benefits:
- Compact storage: ~50-200 bytes per test vs ~400KB with junction tables
- Fast affected test lookup: O(n) bitmap intersection vs O(n*m) row queries
- Efficient serialization for database storage and network transport
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple
import struct

# Try to import pyroaring, fall back to pure Python implementation
try:
    from pyroaring import BitMap
    HAVE_PYROARING = True
except ImportError:
    HAVE_PYROARING = False

    class BitMap:
        """Pure Python fallback for Roaring bitmap when pyroaring isn't available.

        This is a simple set-based implementation that provides the same API
        but without the memory efficiency of Roaring bitmaps.
        """
        def __init__(self, values=None):
            self._set = set(values) if values else set()

        def add(self, value):
            self._set.add(value)

        def __and__(self, other):
            result = BitMap()
            result._set = self._set & other._set
            return result

        def __or__(self, other):
            result = BitMap()
            result._set = self._set | other._set
            return result

        def __bool__(self):
            return bool(self._set)

        def __len__(self):
            return len(self._set)

        def __iter__(self):
            return iter(self._set)

        def serialize(self) -> bytes:
            """Serialize to bytes using a simple format."""
            # Format: count (4 bytes) + sorted integers (4 bytes each)
            sorted_values = sorted(self._set)
            return struct.pack(f'<I{len(sorted_values)}I', len(sorted_values), *sorted_values)

        @classmethod
        def deserialize(cls, data: bytes) -> 'BitMap':
            """Deserialize from bytes."""
            if not data:
                return cls()
            count = struct.unpack('<I', data[:4])[0]
            if count == 0:
                return cls()
            values = struct.unpack(f'<{count}I', data[4:4 + count * 4])
            return cls(values)


# Try to import zstandard, fall back to no compression
try:
    import zstandard as zstd
    HAVE_ZSTD = True
except ImportError:
    HAVE_ZSTD = False
    import gzip


# Compression level for zstd (1-22, higher = better compression, slower)
ZSTD_COMPRESSION_LEVEL = 3

# Module-level singletons to avoid per-call allocations (230K+ calls)
_zstd_compressor = zstd.ZstdCompressor(level=ZSTD_COMPRESSION_LEVEL) if HAVE_ZSTD else None
_zstd_decompressor = zstd.ZstdDecompressor() if HAVE_ZSTD else None


@dataclass
class FileRecord:
    """A tracked file in the unified files table.

    Each file gets a stable integer ID for efficient bitmap operations.
    Works for both Python files (AST checksum) and data files (content hash).
    """
    id: int
    path: str
    checksum: int  # AST checksum (Python) or content CRC32 (data files)
    fsha: Optional[str]  # Git blob SHA for fast change detection
    file_type: str = 'python'  # 'python' or 'data'

    def __hash__(self):
        return hash((self.id, self.path))

    def __eq__(self, other):
        if not isinstance(other, FileRecord):
            return False
        return self.id == other.id and self.path == other.path


@dataclass
class TestRecord:
    """A test in the tests table."""
    id: int
    name: str
    duration: Optional[float] = None
    failed: bool = False


@dataclass
class TestDeps:
    """Test dependencies stored as a Roaring bitmap.

    The file_ids bitmap contains integer file IDs from the files table.
    When serialized, we use zstd compression for compact storage.

    Usage:
        # Create from a set of file IDs
        deps = TestDeps.from_file_ids({1, 5, 10, 23})

        # Serialize for database storage
        blob = deps.serialize()

        # Deserialize from database
        deps = TestDeps.deserialize(blob)

        # Check if test depends on any changed files
        if deps.depends_on_any({5, 10}):
            print("Test needs to run")
    """
    test_id: int
    file_ids: BitMap = field(default_factory=BitMap)
    external_packages: Set[str] = field(default_factory=set)

    @classmethod
    def from_file_ids(cls, test_id: int, file_ids: Set[int],
                      external_packages: Optional[Set[str]] = None) -> 'TestDeps':
        """Create TestDeps from a set of file IDs."""
        bitmap = BitMap(file_ids) if file_ids else BitMap()
        return cls(
            test_id=test_id,
            file_ids=bitmap,
            external_packages=external_packages or set(),
        )

    def serialize(self) -> bytes:
        """Serialize the bitmap with compression.

        Returns:
            Compressed bytes suitable for database BLOB storage.
        """
        raw_bytes = self.file_ids.serialize()
        if _zstd_compressor is not None:
            return _zstd_compressor.compress(raw_bytes)
        else:
            return gzip.compress(raw_bytes)

    @classmethod
    def deserialize(cls, test_id: int, blob: bytes,
                    external_packages_str: Optional[str] = None) -> 'TestDeps':
        """Deserialize a compressed bitmap from database storage.

        Args:
            test_id: The test ID this dependency belongs to
            blob: Compressed bitmap bytes from database
            external_packages_str: Comma-separated package names (optional)

        Returns:
            TestDeps instance with populated bitmap and packages
        """
        # Try zstd first, fall back to gzip
        if _zstd_decompressor is not None:
            try:
                raw_bytes = _zstd_decompressor.decompress(blob)
            except Exception:
                # Might be gzip compressed
                raw_bytes = gzip.decompress(blob)
        else:
            try:
                raw_bytes = gzip.decompress(blob)
            except Exception:
                # Might be zstd compressed but we can't decode it
                # Return empty bitmap
                raw_bytes = BitMap().serialize()

        if HAVE_PYROARING:
            bitmap = BitMap.deserialize(raw_bytes)
        else:
            bitmap = BitMap.deserialize(raw_bytes)

        external_packages = set()
        if external_packages_str:
            external_packages = set(
                pkg.strip() for pkg in external_packages_str.split(',')
                if pkg.strip()
            )

        return cls(
            test_id=test_id,
            file_ids=bitmap,
            external_packages=external_packages,
        )

    def depends_on_any(self, changed_ids: Set[int]) -> bool:
        """Check if this test depends on any of the changed files.

        This is the core operation for determining affected tests.
        Uses bitmap intersection which is very fast (O(min(|A|,|B|))).

        Args:
            changed_ids: Set of file IDs that have changed

        Returns:
            True if the test depends on at least one changed file
        """
        if not changed_ids:
            return False
        changed_bitmap = BitMap(changed_ids)
        return bool(self.file_ids & changed_bitmap)

    def depends_on_packages(self, changed_packages: Set[str]) -> bool:
        """Check if this test depends on any changed external packages.

        Args:
            changed_packages: Set of package names that changed

        Returns:
            True if the test depends on at least one changed package
        """
        if not changed_packages or not self.external_packages:
            return False
        return bool(self.external_packages & changed_packages)

    def add_file(self, file_id: int) -> None:
        """Add a file dependency."""
        self.file_ids.add(file_id)

    def add_package(self, package_name: str) -> None:
        """Add an external package dependency."""
        self.external_packages.add(package_name)

    @property
    def file_count(self) -> int:
        """Number of file dependencies."""
        return len(self.file_ids)

    def get_file_ids_set(self) -> Set[int]:
        """Get file IDs as a Python set."""
        return set(self.file_ids)

    def serialize_external_packages(self) -> str:
        """Serialize external packages as comma-separated string."""
        return ','.join(sorted(self.external_packages)) if self.external_packages else ''


def find_affected_tests(
    all_deps: List[TestDeps],
    changed_file_ids: Set[int],
    changed_packages: Optional[Set[str]] = None,
) -> List[int]:
    """Find all tests affected by file or package changes.

    This is the main query operation - given a set of changed files (by ID)
    and optionally changed packages, return the test IDs that need to run.

    Args:
        all_deps: List of TestDeps for all tests
        changed_file_ids: Set of file IDs that have changed
        changed_packages: Set of package names that changed (optional)

    Returns:
        List of test IDs that are affected by the changes
    """
    affected = []
    changed_packages = changed_packages or set()

    # Special case: Python version changed - all tests must re-run
    if "__python_version_changed__" in changed_packages:
        return [deps.test_id for deps in all_deps]

    for deps in all_deps:
        # Check file dependencies (bitmap intersection - very fast)
        if deps.depends_on_any(changed_file_ids):
            affected.append(deps.test_id)
            continue

        # Check package dependencies
        if deps.depends_on_packages(changed_packages):
            affected.append(deps.test_id)

    return affected


def build_changed_file_ids(
    file_id_map: Dict[str, int],
    changed_paths: Set[str],
) -> Set[int]:
    """Convert changed file paths to file IDs.

    Args:
        file_id_map: Dict mapping file paths to their IDs
        changed_paths: Set of file paths that changed

    Returns:
        Set of file IDs for the changed files
    """
    return {
        file_id_map[path]
        for path in changed_paths
        if path in file_id_map
    }


def compute_storage_size(deps_list: List[TestDeps]) -> Tuple[int, int]:
    """Compute storage sizes for comparison.

    Returns:
        Tuple of (compressed_size, uncompressed_size) in bytes
    """
    compressed = sum(len(deps.serialize()) for deps in deps_list)
    uncompressed = sum(len(deps.file_ids.serialize()) for deps in deps_list)
    return compressed, uncompressed
