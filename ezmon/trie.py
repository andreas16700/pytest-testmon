"""Trie structure implementation for package name encoding."""

from pathlib import Path
from typing import Optional, Union


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
    """Path encoder using a trie structure with lazy population.

    Used only for deterministic package name encoding.
    """

    def __init__(self, root: Union[str, Path], children_map: Optional[dict[str, list[str]]] = None):
        self.root = Path(root).resolve()
        self._children_map = children_map
        self._trie_root = TrieNode()

    def _ensure_populated(self, node: TrieNode, dir_path: Path) -> None:
        if node.sorted_names is not None:
            return

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
