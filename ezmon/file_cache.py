import os
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Dict, Iterable, Optional, Set, Tuple

from ezmon.process_code import bytes_to_string_and_fsha, compute_file_checksum


@dataclass
class FileContent:
    source: str
    fsha: str
    mtime: Optional[float]
    checksum: Optional[int] = None


class FileInfoCache:
    """Cache git metadata and file content/metadata for tracked files."""

    def __init__(self, rootdir: str):
        self.rootdir = os.path.realpath(rootdir)
        self._lock = threading.RLock()
        self._loaded = False
        self._git_available = True

        self._head_shas: Dict[str, str] = {}
        self._index_shas: Dict[str, str] = {}
        self._modified: Set[str] = set()
        self._deleted: Set[str] = set()
        self._tracked: Set[str] = set()

        self._content_cache: Dict[str, FileContent] = {}
        self._norm_cache: Dict[str, str] = {}
        self._is_tracked_cache: Dict[str, bool] = {}

    def _normalize_path(self, path: str) -> str:
        try:
            return self._norm_cache[path]
        except KeyError:
            pass
        if os.path.isabs(path):
            relpath = os.path.relpath(path, self.rootdir)
        else:
            relpath = path
        result = relpath.replace(os.sep, "/")
        self._norm_cache[path] = result
        return result

    def _run_git(self, args: Iterable[str]) -> Optional[str]:
        try:
            result = subprocess.run(
                list(args),
                cwd=self.rootdir,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except Exception:
            return None
        if result.returncode != 0:
            return None
        return result.stdout

    def _parse_git_list(self, output: str) -> Dict[str, str]:
        shas: Dict[str, str] = {}
        if not output:
            return shas
        for entry in output.split("\0"):
            if not entry:
                continue
            try:
                meta, path = entry.split("\t", 1)
            except ValueError:
                continue
            parts = meta.split()
            sha = None
            if len(parts) >= 3 and parts[1] == "blob":
                sha = parts[2]
            elif len(parts) >= 2:
                sha = parts[1]
            if sha:
                shas[path] = sha
        return shas

    def refresh(self) -> None:
        with self._lock:
            head_output = self._run_git(["git", "ls-tree", "-r", "--full-tree", "-z", "HEAD"])
            index_output = self._run_git(["git", "ls-files", "-s", "-z"])

            self._git_available = head_output is not None or index_output is not None

            self._head_shas = self._parse_git_list(head_output or "")
            self._index_shas = self._parse_git_list(index_output or "")

            modified = set()
            deleted = set()

            diff_outputs = [
                self._run_git(["git", "diff", "--name-only", "-z"]),
                self._run_git(["git", "diff", "--name-only", "--cached", "-z"]),
            ]
            for output in diff_outputs:
                if not output:
                    continue
                modified.update(p for p in output.split("\0") if p)

            deleted_outputs = [
                self._run_git(["git", "diff", "--name-only", "--diff-filter=D", "-z"]),
                self._run_git(["git", "diff", "--name-only", "--cached", "--diff-filter=D", "-z"]),
            ]
            for output in deleted_outputs:
                if not output:
                    continue
                deleted.update(p for p in output.split("\0") if p)

            self._modified = modified
            self._deleted = deleted
            self._tracked = set(self._head_shas) | set(self._index_shas) | deleted

            self._loaded = True

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.refresh()

    def is_tracked(self, path: str) -> bool:
        try:
            return self._is_tracked_cache[path]
        except KeyError:
            pass
        norm = self._normalize_path(path)
        with self._lock:
            self._ensure_loaded()
            if not self._git_available:
                result = True
            else:
                result = norm in self._tracked
            self._is_tracked_cache[path] = result
            return result

    def get_head_sha(self, path: str) -> Optional[str]:
        norm = self._normalize_path(path)
        with self._lock:
            self._ensure_loaded()
            if not self._git_available:
                return None
            return self._head_shas.get(norm)

    def get_tracked_sha(self, path: str) -> Optional[str]:
        norm = self._normalize_path(path)
        with self._lock:
            self._ensure_loaded()
            if not self._git_available:
                return None
            return self._head_shas.get(norm) or self._index_shas.get(norm)

    def batch_get_head_shas(self, paths: Iterable[str]) -> Dict[str, Optional[str]]:
        self._ensure_loaded()
        if not self._git_available:
            return {}
        results: Dict[str, Optional[str]] = {}
        for path in paths:
            norm = self._normalize_path(path)
            if norm in self._tracked:
                results[norm] = self._head_shas.get(norm)
        return results

    def batch_get_tracked_shas(self, paths: Iterable[str]) -> Dict[str, Optional[str]]:
        self._ensure_loaded()
        if not self._git_available:
            return {}
        results: Dict[str, Optional[str]] = {}
        for path in paths:
            norm = self._normalize_path(path)
            if norm in self._tracked:
                results[norm] = self._head_shas.get(norm) or self._index_shas.get(norm)
        return results

    def get_modified_paths(self) -> Set[str]:
        with self._lock:
            self._ensure_loaded()
            return set(self._modified)

    def _read_file(self, norm: str) -> Optional[FileContent]:
        abs_path = os.path.join(self.rootdir, norm)
        try:
            with open(abs_path, "rb") as handle:
                data = handle.read()
            source, fsha = bytes_to_string_and_fsha(data)
            try:
                mtime = os.path.getmtime(abs_path)
            except OSError:
                mtime = None
            return FileContent(source=source, fsha=fsha, mtime=mtime)
        except (OSError, IOError):
            return None

    def get_source_and_fsha(self, path: str) -> Tuple[Optional[str], Optional[str], Optional[float]]:
        norm = self._normalize_path(path)
        with self._lock:
            self._ensure_loaded()

            if not self._git_available:
                content = self._read_file(norm)
                if content is None:
                    return None, None, None
                self._content_cache[norm] = content
                return content.source, content.fsha, content.mtime

            if norm not in self._tracked:
                return None, None, None

            if norm in self._content_cache:
                cached = self._content_cache[norm]
                return cached.source, cached.fsha, cached.mtime

            needs_read = norm in self._modified or norm in self._deleted or norm not in self._head_shas
            if needs_read:
                content = self._read_file(norm)
                if content is None:
                    return None, None, None
                self._content_cache[norm] = content
                return content.source, content.fsha, content.mtime

            return None, self._head_shas.get(norm), None

    def get_fsha(self, path: str) -> Optional[str]:
        norm = self._normalize_path(path)
        with self._lock:
            self._ensure_loaded()

            if not self._git_available:
                content = self._read_file(norm)
                if content is None:
                    return None
                self._content_cache[norm] = content
                return content.fsha

            if norm not in self._tracked:
                return None

            if norm in self._content_cache:
                return self._content_cache[norm].fsha

            needs_read = norm in self._modified or norm in self._deleted or norm not in self._head_shas
            if needs_read:
                content = self._read_file(norm)
                if content is None:
                    return None
                self._content_cache[norm] = content
                return content.fsha

            return self._head_shas.get(norm)

    def batch_get_fshas(self, paths: Iterable[str], parallel: bool = False, max_workers: Optional[int] = None) -> Dict[str, Optional[str]]:
        self._ensure_loaded()
        normalized = [self._normalize_path(p) for p in paths]

        results: Dict[str, Optional[str]] = {}
        to_read = []

        with self._lock:
            for norm in normalized:
                if not self._git_available:
                    to_read.append(norm)
                    continue
                if norm not in self._tracked:
                    continue
                if norm in self._content_cache:
                    results[norm] = self._content_cache[norm].fsha
                    continue
                needs_read = norm in self._modified or norm in self._deleted or norm not in self._head_shas
                if needs_read:
                    to_read.append(norm)
                else:
                    results[norm] = self._head_shas.get(norm)

        if to_read:
            if parallel and len(to_read) > 1:
                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    for norm, content in zip(to_read, executor.map(self._read_file, to_read)):
                        if content is None:
                            results[norm] = None
                            continue
                        with self._lock:
                            self._content_cache[norm] = content
                        results[norm] = content.fsha
            else:
                for norm in to_read:
                    content = self._read_file(norm)
                    if content is None:
                        results[norm] = None
                        continue
                    with self._lock:
                        self._content_cache[norm] = content
                    results[norm] = content.fsha

        return results

    def batch_get_checksums(
        self,
        paths: Iterable[str],
        parallel: bool = False,
        max_workers: Optional[int] = None,
    ) -> Dict[str, Optional[int]]:
        self._ensure_loaded()
        normalized = [self._normalize_path(p) for p in paths]

        def compute_one(norm: str) -> Tuple[str, Optional[int]]:
            if not self.is_tracked(norm):
                return norm, None
            source, fsha, mtime = self.get_source_and_fsha(norm)

            # For unmodified files, get_source_and_fsha returns source=None as an
            # optimization (we can get fsha from git without disk I/O). But we need
            # the actual source to compute the checksum, so read the file directly.
            # This is safe because unmodified means disk == HEAD.
            if source is None:
                content = self._read_file(norm)
                if content is None:
                    return norm, None
                source = content.source
                fsha = content.fsha
                mtime = content.mtime
                with self._lock:
                    self._content_cache[norm] = content

            ext = norm.rsplit(".", 1)[-1] if "." in norm else ""
            checksum = compute_file_checksum(source, ext)
            with self._lock:
                cached = self._content_cache.get(norm)
                if cached:
                    cached.checksum = checksum
                else:
                    self._content_cache[norm] = FileContent(
                        source=source,
                        fsha=fsha or "",
                        mtime=mtime,
                        checksum=checksum,
                    )
            return norm, checksum

        results: Dict[str, Optional[int]] = {}

        if parallel and len(normalized) > 1:
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                for norm, checksum in executor.map(compute_one, normalized):
                    results[norm] = checksum
        else:
            for norm in normalized:
                _, checksum = compute_one(norm)
                results[norm] = checksum

        return results
