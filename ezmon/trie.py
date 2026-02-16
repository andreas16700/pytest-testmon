"""Trie structure implementation for path encoding."""

from pathlib import Path
from typing import Any, Optional, Union

from ezmon.process_code import bytes_to_string_and_fsha, compute_file_checksum


# Singleton registry: rootdir -> TrieEncoder instance
_encoder_instances: dict[str, "TrieEncoder"] = {}


def get_encoder(rootdir: Union[str, Path]) -> "TrieEncoder":
    """Get the singleton TrieEncoder for a given rootdir.

    This ensures all components share the same encoder instance,
    maximizing cache efficiency.
    """
    key = str(Path(rootdir).resolve())
    if key not in _encoder_instances:
        _encoder_instances[key] = TrieEncoder(rootdir)
    return _encoder_instances[key]


def clear_encoder_cache() -> None:
    """Clear all singleton encoder instances (useful for testing)."""
    _encoder_instances.clear()


class TrieNode:
    __slots__ = ("children", "sorted_names", "name_to_idx")

    def __init__(self):
        self.children: dict[str, "TrieNode"] = {}
        self.sorted_names: Optional[list[str]] = None
        self.name_to_idx: Optional[dict[str, int]] = None

    def populate(self, names: list[str]) -> None:
        self.sorted_names = names
        self.name_to_idx = {name: idx for idx, name in enumerate(names)}


class TrieEncoder:
    """Path encoder using a trie structure with lazy population."""

    def __init__(self, root: Union[str, Path], children_map: Optional[dict[str, list[str]]] = None):
        self.root = Path(root).resolve()
        self._children_map = children_map
        self._trie_root = TrieNode()
        self._cache_hits = 0
        self._cache_misses = 0
        self._file_info_cache: dict[str, tuple] = {}
        self._file_id_cache: dict[str, int] = {}
        self._db: Any = None

    def set_db(self, db: Any) -> None:
        """Set the database reference for file ID lookups."""
        self._db = db

    def _ensure_populated(self, node: TrieNode, dir_path: Path) -> None:
        if node.sorted_names is not None:
            self._cache_hits += 1
            return

        self._cache_misses += 1

        if self._children_map is not None:
            children = self._children_map.get(str(dir_path), [])
        else:
            children = sorted(e.name for e in dir_path.iterdir())

        node.populate(children)

    def encode(self, path: Union[str, Path]) -> str:
        path = Path(path)
        if not path.is_absolute():
            path = self.root / path
        path = path.resolve()

        rel_parts = path.relative_to(self.root).parts
        if not rel_parts:
            return ""

        indices = []
        node = self._trie_root
        current = self.root

        for part in rel_parts:
            self._ensure_populated(node, current)
            idx = node.name_to_idx[part]
            indices.append(str(idx))

            if part not in node.children:
                node.children[part] = TrieNode()
            node = node.children[part]
            current = current / part

        return "/".join(indices)

    def decode(self, encoded: str) -> Path:
        if not encoded:
            return self.root

        indices = [int(x) for x in encoded.split("/")]
        node = self._trie_root
        current = self.root

        for idx in indices:
            self._ensure_populated(node, current)
            name = node.sorted_names[idx]

            if name not in node.children:
                node.children[name] = TrieNode()
            node = node.children[name]
            current = current / name

        return current

    def clear_cache(self) -> None:
        self._trie_root = TrieNode()
        self._cache_hits = 0
        self._cache_misses = 0
        self._file_info_cache.clear()
        self._file_id_cache.clear()

    def get_cached_file_info(self, encoded: str):
        return self._file_info_cache.get(encoded)

    def set_cached_file_info(self, encoded: str, value) -> None:
        self._file_info_cache[encoded] = value

    def _compute_file_info(self, path: Path):
        try:
            data = path.read_bytes()
        except OSError:
            return (None, None, None)
        source, fsha = bytes_to_string_and_fsha(data)
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = None
        ext = path.suffix[1:] if path.suffix else ""
        checksum = compute_file_checksum(source, ext if ext else "txt")
        return (checksum, fsha, mtime)

    def get_file_info(self, encoded: str, compute_fn=None):
        """Return (checksum, fsha, mtime) for an encoded path, cached by encoded key."""
        cached = self._file_info_cache.get(encoded)
        if cached is not None:
            return cached
        path = self.decode(encoded)
        if compute_fn is None:
            value = self._compute_file_info(path)
        else:
            value = compute_fn(path)
        self._file_info_cache[encoded] = value
        return value

    def get_checksum(self, encoded: str, compute_fn=None):
        info = self.get_file_info(encoded, compute_fn=compute_fn)
        if not info:
            return None
        return info[0]

    def get_file_id(
        self,
        encoded: str,
        checksum: Optional[int] = None,
        fsha: Optional[str] = None,
        file_type: str = 'python',
    ) -> Optional[int]:
        """Get the database file ID for an encoded path.

        Args:
            encoded: The encoded path string
            checksum: Optional checksum (if not provided, computed from file)
            fsha: Optional file SHA (if not provided, computed from file)
            file_type: 'python' or 'data'

        Returns cached ID if available, otherwise queries/creates via database.
        Requires set_db() to have been called first.
        """
        cached = self._file_id_cache.get(encoded)
        if cached is not None:
            return cached

        if self._db is None:
            return None

        # Use provided values or get from file info cache
        if checksum is None:
            info = self.get_file_info(encoded)
            checksum, fsha, _ = info if info else (None, None, None)

        # Decode to get the relative path for the database
        path = self.decode(encoded)
        try:
            relpath = str(path.relative_to(self.root))
        except ValueError:
            relpath = str(path)

        # Get or create file ID in database
        file_id = self._db.get_or_create_file_id(
            relpath, checksum=checksum, fsha=fsha, file_type=file_type
        )
        self._file_id_cache[encoded] = file_id
        return file_id

    def set_file_id(self, encoded: str, file_id: int) -> None:
        """Manually set the file ID cache for an encoded path."""
        self._file_id_cache[encoded] = file_id

    def prefill_file_info(self, paths, compute_fn=None) -> None:
        """Prefill cache for a set of paths using compute_fn(path)."""
        for path in paths:
            encoded = self.encode(path)
            if encoded in self._file_info_cache:
                continue
            if compute_fn is None:
                self._file_info_cache[encoded] = self._compute_file_info(Path(path))
            else:
                self._file_info_cache[encoded] = compute_fn(path)

    @property
    def cache_stats(self) -> dict:
        def count(n):
            c = len(n.sorted_names) if n.sorted_names else 0
            for child in n.children.values():
                c += count(child)
            return c

        return {
            "directories_cached": self._cache_misses,
            "total_entries": count(self._trie_root),
            "cache_hits": self._cache_hits,
            "cache_misses": self._cache_misses,
        }
