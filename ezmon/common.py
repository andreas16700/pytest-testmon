import logging
import os
import re

try:
    # Python >= 3.8
    import importlib.metadata

    def get_system_packages_raw():
        return (
            (pkg.metadata["Name"], pkg.version)
            for pkg in importlib.metadata.distributions()
        )

except ImportError:
    # Python < 3.7
    import pkg_resources

    def get_system_packages_raw():
        return (
            (pkg.project_name, pkg.version)
            for pkg in pkg_resources.working_set  # pylint: disable=not-an-iterable
        )


from pathlib import Path

import sys
from typing import List, Dict

# TypedDict was added to typing in Python 3.8
if sys.version_info >= (3, 8):
    from typing import TypedDict
else:
    from typing_extensions import TypedDict


class FileFp(TypedDict):
    """File fingerprint data for test dependencies.

    With the simplified single-checksum model, each file has one checksum
    representing the entire file's code (excluding comments and docstrings).
    """
    filename: str
    file_checksum: int = None  # Single checksum for the entire file
    mtime: float = None  # optimization helper, not really a part of the data structure fundamentally
    fsha: str = None  # optimization helper, git blob SHA for fast change detection
    fingerprint_id: int = None  # optimization helper


TestName = str

TestFileFps = Dict[TestName, List[FileFp]]

Duration = float
Failed = bool


class DepsNOutcomes(TypedDict):
    deps: List[FileFp]
    failed: Failed
    duration: Duration
    forced: bool = None


TestExecutions = Dict[TestName, DepsNOutcomes]


def dummy():
    pass


def get_logger(name):
    formatter = logging.Formatter("%(levelname)s: %(message)s")
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)

    # Configure the logger
    tm_logger = logging.getLogger(name)
    tm_logger.setLevel(logging.INFO)
    tm_logger.addHandler(handler)
    return tm_logger


logger = get_logger(__name__)


def is_local_package(pkg_name: str, rootdir: str) -> bool:
    """
    Check if a package name corresponds to a local project package.

    This handles the case where a package is installed (e.g., pip install -e .)
    but is actually the project being tested. For example, when testing
    matplotlib, 'matplotlib' should NOT be treated as an external dependency
    because matplotlib IS the local project.

    We detect this by checking if there's a Python package directory
    in the project that matches the package name.
    """
    if not rootdir:
        return False

    # Normalize package name (pip uses - but directories use _)
    normalized_name = pkg_name.replace('-', '_').lower()

    # Look for a package directory matching this name anywhere in the project
    # Common patterns:
    # - src/packagename/
    # - lib/packagename/
    # - packagename/
    search_dirs = ['', 'src', 'lib', 'source', 'packages']

    for search_dir in search_dirs:
        if search_dir:
            pkg_path = os.path.join(rootdir, search_dir, pkg_name)
            pkg_path_normalized = os.path.join(rootdir, search_dir, normalized_name)
        else:
            pkg_path = os.path.join(rootdir, pkg_name)
            pkg_path_normalized = os.path.join(rootdir, normalized_name)

        # Check both original and normalized names
        for path in [pkg_path, pkg_path_normalized]:
            if os.path.isdir(path):
                # Check for regular package (with __init__.py)
                init_file = os.path.join(path, '__init__.py')
                if os.path.exists(init_file):
                    return True

                # Check for namespace package (Python 3.3+)
                # A directory with Python files or subdirectories with __init__.py
                try:
                    for entry in os.listdir(path):
                        entry_path = os.path.join(path, entry)
                        if entry.endswith('.py'):
                            return True
                        if os.path.isdir(entry_path):
                            sub_init = os.path.join(entry_path, '__init__.py')
                            if os.path.exists(sub_init):
                                return True
                except OSError:
                    pass

            # Also check for single-file modules
            py_file = path + '.py'
            if os.path.exists(py_file):
                return True

    return False


def get_system_packages(ignore=None, rootdir=None):
    """
    Get system packages as a string, excluding ignored and local packages.

    Args:
        ignore: Set of package names to ignore
        rootdir: Project root directory - packages found here are auto-ignored
    """
    if not ignore:
        ignore = set(("pytest-ezmon", "pytest-testmon"))
    else:
        ignore = set(ignore)

    packages = []
    local_packages_found = []  # Debug: track which packages were detected as local
    for package, version in get_system_packages_raw():
        if package in ignore:
            continue
        # Skip local packages (the project being tested)
        if rootdir and is_local_package(package, rootdir):
            local_packages_found.append(package)
            continue
        packages.append(f"{package} {version}")

    # Debug output
    if rootdir:
        logger.info(f"get_system_packages: rootdir={rootdir}, local_packages={local_packages_found}")

    return ", ".join(sorted(set(packages)))


def drop_patch_version(system_packages):
    return re.sub(
        r"\b([\w_-]+\s\d+\.\d+)\.\w+\b",  # extract (Package M.N).P / drop .patch
        r"\1",
        system_packages,
    )


def parse_system_packages(packages_str: str) -> Dict[str, str]:
    """
    Parse system_packages string into a dict of {package_name: version}.

    Example: "numpy 1.21, requests 2.28" -> {"numpy": "1.21", "requests": "2.28"}
    """
    if not packages_str:
        return {}

    packages = {}
    for item in packages_str.split(", "):
        item = item.strip()
        if not item:
            continue
        # Split on last space to handle packages with spaces in names
        parts = item.rsplit(" ", 1)
        if len(parts) == 2:
            packages[parts[0]] = parts[1]
        elif len(parts) == 1:
            packages[parts[0]] = ""
    return packages


def compute_changed_packages(old_packages_str: str, new_packages_str: str) -> set:
    """
    Compute which packages changed between two package strings.

    Returns a set of package names that were:
    - Added (in new but not old)
    - Removed (in old but not new)
    - Updated (version changed)

    This enables granular external dependency tracking - only tests that
    used a changed package need to be re-run.
    """
    old_pkgs = parse_system_packages(old_packages_str)
    new_pkgs = parse_system_packages(new_packages_str)

    changed = set()

    # Added packages
    for pkg in new_pkgs:
        if pkg not in old_pkgs:
            changed.add(pkg)

    # Removed packages
    for pkg in old_pkgs:
        if pkg not in new_pkgs:
            changed.add(pkg)

    # Updated packages (version changed)
    for pkg in old_pkgs:
        if pkg in new_pkgs and old_pkgs[pkg] != new_pkgs[pkg]:
            changed.add(pkg)

    return changed


def compute_package_diff(old_packages_str: str, new_packages_str: str):
    """Return (added, removed, changed) package name sets."""
    old_pkgs = parse_system_packages(old_packages_str)
    new_pkgs = parse_system_packages(new_packages_str)

    added = set()
    removed = set()
    changed = set()

    for pkg in new_pkgs:
        if pkg not in old_pkgs:
            added.add(pkg)
        elif old_pkgs[pkg] != new_pkgs[pkg]:
            changed.add(pkg)

    for pkg in old_pkgs:
        if pkg not in new_pkgs:
            removed.add(pkg)

    return added, removed, changed


#
# .git utilities
#
def git_path(start_path=None):  # parent dirs only
    start_path = Path(start_path or os.getcwd()).resolve()
    current_path = start_path
    while current_path != current_path.parent:  # '/'.parent == '/'
        path = current_path / ".git"
        if path.exists() and path.is_dir():
            return str(path)
        current_path = current_path.parent
    return None


def git_current_branch(path=None):
    path = git_path(path)
    if not path:
        return None
    git_head_file = os.path.join(path, "HEAD")
    try:
        with open(git_head_file, "r", encoding="utf8") as head_file:
            head_content = head_file.read().strip()
        if head_content.startswith("ref:"):
            return head_content.split("/")[-1]  # e.g. ref: refs/heads/master
    except FileNotFoundError:
        pass
    return None


def git_current_head(path=None):
    path = git_path(path)
    if not path:
        return None
    git_head_file = os.path.join(path, "HEAD")
    try:
        with open(git_head_file, "r", encoding="utf8") as f:
            head_content = f.read().strip()
    except FileNotFoundError:
        return None

    if not head_content.startswith("ref:"):
        return head_content if len(head_content) == 40 else None

    ref = head_content[len("ref: "):].strip()   # e.g. "refs/heads/master"
    branch = ref.split("/")[-1]

    # Try loose ref first
    loose = os.path.join(path, "refs", "heads", branch)
    try:
        with open(loose, "r", encoding="utf8") as f:
            return f.read().strip()
    except FileNotFoundError:
        pass

    # Fall back to packed-refs (used by actions/checkout@v4)
    packed = os.path.join(path, "packed-refs")
    try:
        with open(packed, "r", encoding="utf8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("#"):
                    continue
                parts = line.split(" ", 1)
                if len(parts) == 2 and parts[1] == ref:
                    return parts[0]
    except FileNotFoundError:
        pass

    return None