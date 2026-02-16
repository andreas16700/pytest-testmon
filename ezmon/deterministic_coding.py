import os
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from .trie import TrieEncoder


def git_tracked_files(rootdir: str) -> List[str]:
    """Return all git-tracked files at HEAD (committed)."""
    result = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", "-z", "HEAD"],
        cwd=rootdir,
        capture_output=True,
    )
    if result.returncode != 0:
        return []
    return [p for p in result.stdout.decode("utf-8", "replace").split("\0") if p]


def build_file_code_map(
    tracked_files: Iterable[str],
    rootdir: Optional[str] = None,
    encoder: Optional[TrieEncoder] = None,
) -> Dict[str, str]:
    """Encode git-tracked files using TrieEncoder ordering."""
    if encoder is None:
        raise ValueError("encoder required")
    mapping: Dict[str, str] = {}
    for path in tracked_files:
        mapping[path] = encoder.encode(path)
    return mapping


def build_package_code_map(packages: Iterable[str]) -> Dict[str, str]:
    """Encode package names using TrieEncoder ordering."""
    names = set(packages)
    if not names:
        return {}
    with tempfile.TemporaryDirectory(prefix="ezmon_pkg_encoding_") as tmpdir:
        root = Path(tmpdir)
        for name in names:
            os.makedirs(root / name, exist_ok=True)
        encoder = TrieEncoder(root)
        return {name: encoder.encode(name) for name in names}


def invert_map(mapping: Dict[str, str]) -> Dict[str, str]:
    return {v: k for k, v in mapping.items()}


def encode_files(paths: Iterable[str], file_code_map: Dict[str, str]) -> List[str]:
    return [file_code_map[p] for p in paths if p in file_code_map]


def encode_packages(packages: Iterable[str], package_code_map: Dict[str, str]) -> List[str]:
    return [package_code_map[p] for p in packages if p in package_code_map]
