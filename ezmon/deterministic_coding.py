import os
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, Iterable, List

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


def encode_packages(packages: Iterable[str], package_code_map: Dict[str, str]) -> List[str]:
    return [package_code_map[p] for p in packages if p in package_code_map]
