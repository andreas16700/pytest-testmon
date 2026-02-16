"""
NetDB - Network-based database interface for pytest-ezmon.

This class implements the same interface as db.DB but makes HTTP calls
to a remote server instead of local SQLite operations. Used for CI/CD
ephemeral environments where no local .testmondata file is needed.
"""
import gzip
import json
import os
import time
from functools import lru_cache

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from typing import Dict, List, Optional, Any, Set

from ezmon.common import TestExecutions, get_logger
from ezmon.bitmap_deps import TestDeps

logger = get_logger(__name__)


class NetDBException(Exception):
    """Exception raised for NetDB errors."""
    pass


class NetDB:
    """
    Network database implementation that communicates with a remote server
    via RPC-style API calls instead of local SQLite operations.

    Implements the same interface as db.DB for seamless swapping.
    """

    # Minimum payload size (bytes) to apply gzip compression
    GZIP_THRESHOLD = 1024

    # Default session timeout in seconds
    SESSION_TTL = 1800  # 30 minutes

    def __init__(
        self,
        server_url: str,
        repo_id: str,
        job_id: str,
        auth_token: Optional[str] = None,
        run_id: Optional[str] = None,
        readonly: bool = False,
    ):
        """
        Initialize NetDB connection.

        Args:
            server_url: Base URL of the server (e.g., 'https://your-server.com')
            repo_id: Repository identifier (e.g., 'owner/repo')
            job_id: Job/variant identifier (e.g., 'test-py311')
            auth_token: Authentication token for API calls
            run_id: CI run ID for linking to CI system
            readonly: Whether this is a readonly session
        """
        self.server_url = server_url.rstrip('/')
        self.repo_id = repo_id
        self.job_id = job_id
        self.auth_token = auth_token
        self.run_id = run_id
        self._readonly = readonly

        # Session state
        self.session_id: Optional[str] = None
        self.exec_id: Optional[int] = None
        self.file_created = False  # Always False for NetDB (server manages DB)

        # Setup HTTP session with retry logic
        self._http_session = self._create_http_session()

        # Client-side caches (match DB behavior)
        self._fingerprint_cache: Dict[tuple, int] = {}
        self._file_dependency_cache: Dict[tuple, int] = {}

    def _create_http_session(self) -> requests.Session:
        """Create HTTP session with connection pooling and retry logic."""
        session = requests.Session()

        # Configure retry strategy with exponential backoff
        # Include Cloudflare error codes (520-524) which indicate transient origin issues
        retry_strategy = Retry(
            total=3,
            backoff_factor=0.5,
            status_forcelist=[429, 500, 502, 503, 504, 520, 521, 522, 523, 524],
            allowed_methods=["GET", "POST"],
        )
        adapter = HTTPAdapter(
            max_retries=retry_strategy,
            pool_connections=10,
            pool_maxsize=10,
        )
        session.mount("http://", adapter)
        session.mount("https://", adapter)

        return session

    def _get_headers(self, content_type: str = "application/json") -> Dict[str, str]:
        """Get standard headers for API requests."""
        headers = {
            "Content-Type": content_type,
            "Accept": "application/json",
            "X-Repo-ID": self.repo_id,
            "X-Job-ID": self.job_id,
        }
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"
        if self.session_id:
            headers["X-Session-ID"] = self.session_id
        return headers

    def _make_request(
        self,
        method: str,
        endpoint: str,
        data: Optional[Dict] = None,
        compress: bool = True,
        timeout: int = 60,
    ) -> Dict:
        """
        Make an HTTP request to the server.

        Args:
            method: HTTP method ('GET' or 'POST')
            endpoint: API endpoint path (e.g., '/api/rpc/session/initiate')
            data: Request body data (for POST)
            compress: Whether to gzip compress large payloads
            timeout: Request timeout in seconds

        Returns:
            Parsed JSON response

        Raises:
            NetDBException: On network or API errors
        """
        url = f"{self.server_url}{endpoint}"
        headers = self._get_headers()

        try:
            if method.upper() == "GET":
                response = self._http_session.get(
                    url,
                    headers=headers,
                    params=data,
                    timeout=timeout,
                )
            else:
                body = json.dumps(data) if data else None

                # Apply gzip compression for large payloads
                if compress and body and len(body) > self.GZIP_THRESHOLD:
                    body = gzip.compress(body.encode('utf-8'))
                    headers["Content-Encoding"] = "gzip"
                    headers["Content-Type"] = "application/json"
                elif body:
                    body = body.encode('utf-8')

                response = self._http_session.post(
                    url,
                    headers=headers,
                    data=body,
                    timeout=timeout,
                )

            response.raise_for_status()
            return response.json()

        except requests.exceptions.Timeout as e:
            logger.error(f"Request timeout: {endpoint}")
            raise NetDBException(f"Request timeout: {e}")
        except requests.exceptions.ConnectionError as e:
            logger.error(f"Connection error: {endpoint}")
            raise NetDBException(f"Connection error: {e}")
        except requests.exceptions.HTTPError as e:
            logger.error(f"HTTP error {response.status_code}: {endpoint}")
            raise NetDBException(f"HTTP error: {e}")
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON response: {endpoint}")
            raise NetDBException(f"Invalid JSON response: {e}")

    def __enter__(self):
        """Context manager entry - no-op for NetDB."""
        return self

    def __exit__(self, *args, **kwargs):
        """Context manager exit - no-op for NetDB."""
        pass

    def version_compatibility(self):
        """Return the data version for compatibility checks."""
        return 18  # Match DB.DATA_VERSION

    def initiate_execution(
        self,
        environment_name: str,
        system_packages: str,
        python_version: str,
        execution_metadata: dict,
    ) -> Dict[str, Any]:
        """
        Start a new execution session with the server.

        Returns:
            Dict with exec_id, filenames, and packages_changed
        """
        response = self._make_request(
            "POST",
            "/api/rpc/session/initiate",
            data={
                "environment_name": environment_name,
                "system_packages": system_packages,
                "python_version": python_version,
                "execution_metadata": execution_metadata,
                "repo_id": self.repo_id,
                "job_id": self.job_id,
                "run_id": self.run_id,
            },
        )

        self.session_id = response.get("session_id")
        self.exec_id = response.get("exec_id")

        # Get changed packages (granular tracking) or convert legacy boolean
        changed_packages = response.get("changed_packages", set())
        if isinstance(changed_packages, list):
            changed_packages = set(changed_packages)
        packages_changed = response.get("packages_changed", False)

        return {
            "exec_id": response["exec_id"],
            "filenames": response.get("filenames", []),
            "packages_changed": packages_changed or bool(changed_packages),
            "changed_packages": changed_packages,
            "previous_packages": response.get("previous_packages", ""),
            "previous_python": response.get("previous_python", ""),
            "current_packages": system_packages,
            "current_python": python_version,
        }

    def finish_execution(
        self,
        exec_id: int,
        duration: float = None,
        select: bool = True,
        commit_id: Optional[str] = None,
    ):
        """
        Finalize execution session and aggregate stats.
        """
        self._make_request(
            "POST",
            "/api/rpc/session/finish",
            data={
                "exec_id": exec_id,
                "duration": duration,
                "select": select,
                "commit_id": commit_id,
            },
        )

        # Clear caches
        self._fingerprint_cache.clear()
        self._file_dependency_cache.clear()

    def get_latest_run_commit_id(self) -> Optional[str]:
        """NetDB does not currently expose run commit IDs."""
        return None

    def fetch_unknown_files(self, files_fshas: Dict[str, str], exec_id: int) -> List[str]:
        """
        Find files whose SHA has changed from what's stored on the server.

        Args:
            files_fshas: Dict mapping filename to file SHA
            exec_id: Execution/environment ID

        Returns:
            List of filenames that have changed
        """
        response = self._make_request(
            "POST",
            "/api/rpc/files/fetch_unknown",
            data={
                "exec_id": exec_id,
                "files_fshas": files_fshas,
            },
        )
        return response.get("unknown_files", [])

    def determine_tests(
        self,
        exec_id: int,
        files_mhashes: Dict[str, Any],
        file_deps_shas: Optional[Dict[str, str]] = None,
        changed_packages: Optional[set] = None,
    ) -> Dict[str, List[str]]:
        """
        Determine which tests are affected by code changes.

        Args:
            exec_id: Execution/environment ID
            files_mhashes: Dict of {filename: file_checksum} (single int per file)
            file_deps_shas: Dict of {filename: sha} for non-Python dependencies
            changed_packages: Set of package names that changed (granular tracking)

        Returns:
            Dict with 'affected' and 'failing' test lists
        """
        response = self._make_request(
            "POST",
            "/api/rpc/tests/determine",
            data={
                "exec_id": exec_id,
                "files_checksums": files_mhashes,
                "file_deps_shas": file_deps_shas or {},
                "changed_packages": list(changed_packages) if changed_packages else [],
            },
        )
        return {
            "affected": response.get("affected", []),
            "failing": response.get("failing", []),
        }

    @lru_cache(1000)
    def fetch_or_create_file_fp(
        self,
        filename: str,
        fsha: str,
        file_checksum: int,
    ) -> int:
        """
        Fetch or create a fingerprint record.

        Uses client-side LRU cache to minimize network calls.
        With single-file checksums, file_checksum is just an integer.
        """
        cache_key = (filename, fsha, file_checksum)
        if cache_key in self._fingerprint_cache:
            return self._fingerprint_cache[cache_key]

        response = self._make_request(
            "POST",
            "/api/rpc/fingerprint/fetch_or_create",
            data={
                "exec_id": self.exec_id,
                "filename": filename,
                "fsha": fsha,
                "file_checksum": file_checksum,
            },
        )

        fingerprint_id = response["fingerprint_id"]
        self._fingerprint_cache[cache_key] = fingerprint_id
        return fingerprint_id

    @lru_cache(1000)
    def fetch_or_create_file_dependency(self, filename: str, sha: str) -> int:
        """Fetch or create a file dependency record."""
        cache_key = (filename, sha)
        if cache_key in self._file_dependency_cache:
            return self._file_dependency_cache[cache_key]

        response = self._make_request(
            "POST",
            "/api/rpc/file_dependency/fetch_or_create",
            data={
                "exec_id": self.exec_id,
                "filename": filename,
                "sha": sha,
            },
        )

        fd_id = response["file_dependency_id"]
        self._file_dependency_cache[cache_key] = fd_id
        return fd_id

    def insert_test_file_fps(self, tests_deps_n_outcomes: TestExecutions, exec_id: int = None):
        """
        Bulk insert test execution results and their fingerprints.

        This is the most performance-critical method - batches data for efficiency.
        """
        assert exec_id

        # Serialize the test execution data for transport
        serialized_data = {}
        for test_name, deps_n_outcomes in tests_deps_n_outcomes.items():
            serialized_test = {
                "duration": deps_n_outcomes.get("duration"),
                "failed": deps_n_outcomes.get("failed"),
                "forced": deps_n_outcomes.get("forced"),
                "deps": [],
                "file_deps": deps_n_outcomes.get("file_deps", []),
                "external_deps": deps_n_outcomes.get("external_deps", []),
            }

            for dep in deps_n_outcomes.get("deps", []):
                serialized_test["deps"].append({
                    "filename": dep["filename"],
                    "fsha": dep["fsha"],
                    "mtime": dep.get("mtime"),
                    "file_checksum": dep.get("file_checksum"),
                })

            serialized_data[test_name] = serialized_test

        self._make_request(
            "POST",
            "/api/rpc/test_execution/batch_insert",
            data={
                "exec_id": exec_id,
                "tests": serialized_data,
            },
            compress=True,
            timeout=120,
        )

    def insert_coverage_lines(self, exec_id: int, nodes_files_lines: Dict):
        """Insert coverage line data for tests."""
        if not nodes_files_lines:
            return

        # Serialize coverage data
        serialized = {}
        for test_name, files in nodes_files_lines.items():
            serialized[test_name] = {
                filename: list(lines) if isinstance(lines, set) else lines
                for filename, lines in files.items()
            }

        self._make_request(
            "POST",
            "/api/rpc/coverage/batch_insert",
            data={
                "exec_id": exec_id,
                "coverage": serialized,
            },
            compress=True,
            timeout=120,
        )

    def all_test_executions(self, exec_id: int) -> Dict[str, Dict]:
        """Get all test executions for an environment."""
        response = self._make_request(
            "GET",
            "/api/rpc/tests/all",
            data={"exec_id": exec_id},
        )
        return response.get("tests", {})

    def filenames(self, exec_id: int) -> List[str]:
        """Get all tracked filenames for an environment."""
        response = self._make_request(
            "GET",
            "/api/rpc/files/list",
            data={"exec_id": exec_id},
        )
        return response.get("filenames", [])

    def all_filenames(self) -> List[str]:
        """Get all filenames across all environments."""
        response = self._make_request(
            "GET",
            "/api/rpc/files/all",
        )
        return response.get("filenames", [])

    def filenames_fingerprints(self, exec_id: int) -> List[Dict]:
        """Get filename fingerprint details for an environment."""
        response = self._make_request(
            "GET",
            "/api/rpc/files/fingerprints",
            data={"exec_id": exec_id},
        )
        return response.get("fingerprints", [])

    def fetch_changed_file_data(
        self,
        changed_fingerprints: List[int],
        exec_id: int,
    ) -> List:
        """Get changed file data for fingerprint comparison."""
        response = self._make_request(
            "POST",
            "/api/rpc/files/changed_data",
            data={
                "exec_id": exec_id,
                "fingerprint_ids": list(changed_fingerprints),
            },
        )

        result = []
        for row in response.get("data", []):
            result.append([
                row["filename"],
                row["test_name"],
                row.get("file_checksum"),
                row["id"],
                row["failed"],
                row.get("duration"),
            ])
        return result

    def delete_test_executions(self, test_names: List[str], exec_id: int):
        """Delete test executions from the server."""
        if not test_names:
            return

        self._make_request(
            "POST",
            "/api/rpc/tests/delete",
            data={
                "exec_id": exec_id,
                "test_names": test_names,
            },
        )

    def get_file_dependency_filenames(self, exec_id: int) -> List[str]:
        """Get all file dependency filenames for an environment."""
        response = self._make_request(
            "GET",
            "/api/rpc/file_dependencies/list",
            data={"exec_id": exec_id},
        )
        return response.get("filenames", [])

    def update_mtimes(self, new_mtimes: List[tuple]):
        """Update file modification times (batch operation)."""
        if not new_mtimes:
            return

        self._make_request(
            "POST",
            "/api/rpc/files/update_mtimes",
            data={
                "updates": [
                    {"mtime": mtime, "fsha": fsha, "id": fp_id}
                    for mtime, fsha, fp_id in new_mtimes
                ],
            },
        )

    def fetch_saving_stats(self, exec_id: int, select: bool) -> tuple:
        """Fetch test savings statistics."""
        response = self._make_request(
            "GET",
            "/api/rpc/stats/savings",
            data={
                "exec_id": exec_id,
                "select": select,
            },
        )
        return (
            response.get("run_saved_time"),
            response.get("run_all_time"),
            response.get("run_saved_tests"),
            response.get("run_all_tests"),
            response.get("total_saved_time"),
            response.get("total_all_time"),
            response.get("total_saved_tests"),
            response.get("total_all_tests"),
        )

    def write_attribute(self, attribute: str, data: Any, exec_id: int = None):
        """Write a metadata attribute."""
        self._make_request(
            "POST",
            "/api/rpc/metadata/write",
            data={
                "attribute": attribute,
                "data": data,
                "exec_id": exec_id,
            },
        )

    def fetch_attribute(self, attribute: str, default: Any = None, exec_id: int = None) -> Any:
        """Fetch a metadata attribute."""
        response = self._make_request(
            "GET",
            "/api/rpc/metadata/read",
            data={
                "attribute": attribute,
                "exec_id": exec_id,
            },
        )
        return response.get("data", default)

    def close(self):
        """Close the HTTP session."""
        if self._http_session:
            self._http_session.close()

    # ==========================================================================
    # New Roaring Bitmap-based methods for simplified dependency storage
    # ==========================================================================

    def get_or_create_file_id(
        self,
        path: str,
        checksum: int = None,
        fsha: str = None,
        file_type: str = 'python'
    ) -> int:
        """Get or create a stable file ID for a given path.

        Args:
            path: Relative file path
            checksum: AST checksum (Python) or content hash (data files)
            fsha: Git blob SHA for fast change detection
            file_type: 'python' or 'data'

        Returns:
            Integer file ID
        """
        response = self._make_request(
            "POST",
            "/api/rpc/files/get_or_create_id",
            data={
                "exec_id": self.exec_id,
                "path": path,
                "checksum": checksum,
                "fsha": fsha,
                "file_type": file_type,
            },
        )
        return response["file_id"]

    def get_file_id_map(self, exec_id: int = None) -> Dict[str, int]:
        """Get a mapping of file paths to their IDs.

        Args:
            exec_id: Optional environment ID

        Returns:
            Dict mapping file path to integer ID
        """
        response = self._make_request(
            "GET",
            "/api/rpc/files/id_map",
            data={"exec_id": exec_id or self.exec_id},
        )
        return response.get("file_id_map", {})

    def get_file_checksums(self) -> Dict[str, int]:
        """Get current checksums for all files."""
        response = self._make_request(
            "GET",
            "/api/rpc/files/checksums",
        )
        return response.get("checksums", {})

    def update_file_checksum(self, path: str, checksum: int, fsha: str = None) -> None:
        """Update the checksum for a file."""
        self._make_request(
            "POST",
            "/api/rpc/files/update_checksum",
            data={
                "path": path,
                "checksum": checksum,
                "fsha": fsha,
            },
        )

    def get_file_ids_for_paths(self, paths: Set[str]) -> Set[int]:
        """Return file IDs for known file paths."""
        if not paths:
            return set()
        response = self._make_request(
            "POST",
            "/api/rpc/files/ids_for_paths",
            data={"paths": list(paths)},
        )
        return set(response.get("file_ids", []))

    def get_or_create_test_id(
        self,
        exec_id: int,
        test_name: str,
        duration: float = None,
        failed: bool = False,
        test_file: Optional[str] = None,
    ) -> int:
        """Get or create a test ID for a given test name."""
        response = self._make_request(
            "POST",
            "/api/rpc/tests/get_or_create_id",
            data={
                "exec_id": exec_id,
                "test_name": test_name,
                "duration": duration,
                "failed": failed,
                "test_file": test_file,
            },
        )
        return response["test_id"]

    def get_test_files_for_tests(self, exec_id: int, test_names: Set[str]) -> Set[str]:
        """Get test files for a set of test names."""
        if not test_names:
            return set()
        response = self._make_request(
            "POST",
            "/api/rpc/tests/test_files",
            data={
                "exec_id": exec_id,
                "test_names": list(test_names),
            },
        )
        return set(response.get("test_files", []))

    def get_all_test_files(self, exec_id: int) -> Set[str]:
        """Get all known test files for an environment."""
        response = self._make_request(
            "GET",
            "/api/rpc/tests/all_files",
            data={"exec_id": exec_id},
        )
        return set(response.get("test_files", []))

    def get_failing_tests_bitmap(self, exec_id: int) -> List[str]:
        """Get names of tests that failed in their last run."""
        response = self._make_request(
            "GET",
            "/api/rpc/tests/failing",
            data={"exec_id": exec_id},
        )
        return response.get("failing", [])

    def save_test_deps(self, test_id: int, deps: TestDeps) -> None:
        """Save test dependencies as a compressed Roaring bitmap via RPC.

        Args:
            test_id: Test ID
            deps: TestDeps object with file IDs and external packages
        """
        import base64
        blob = deps.serialize()
        self._make_request(
            "POST",
            "/api/rpc/test_deps/save",
            data={
                "exec_id": self.exec_id,
                "test_id": test_id,
                "file_bitmap": base64.b64encode(blob).decode('ascii'),
                "external_packages": deps.serialize_external_packages(),
            },
        )

    def get_all_test_deps(self, exec_id: int) -> List[TestDeps]:
        """Get all test dependencies for an environment via RPC.

        Args:
            exec_id: Environment ID

        Returns:
            List of TestDeps objects
        """
        import base64
        response = self._make_request(
            "GET",
            "/api/rpc/test_deps/all",
            data={"exec_id": exec_id},
        )

        deps_list = []
        for item in response.get("deps", []):
            blob = base64.b64decode(item["file_bitmap"])
            deps = TestDeps.deserialize(
                item["test_id"],
                blob,
                item.get("external_packages")
            )
            deps_list.append(deps)

        return deps_list

    def find_affected_tests_bitmap(
        self,
        exec_id: int,
        changed_file_ids: Set[int],
        changed_packages: Optional[Set[str]] = None
    ) -> List[str]:
        """Find tests affected by file or package changes via RPC.

        Args:
            exec_id: Environment ID
            changed_file_ids: Set of file IDs that changed
            changed_packages: Set of package names that changed

        Returns:
            List of affected test names
        """
        response = self._make_request(
            "POST",
            "/api/rpc/tests/find_affected_bitmap",
            data={
                "exec_id": exec_id,
                "changed_file_ids": list(changed_file_ids),
                "changed_packages": list(changed_packages) if changed_packages else [],
            },
        )
        return response.get("affected", [])

    def get_changed_file_ids(self, files_checksums: Dict[str, int]) -> Set[int]:
        """Find file IDs for files whose checksums have changed via RPC.

        Args:
            files_checksums: Dict of {path: current_checksum}

        Returns:
            Set of file IDs whose checksums differ
        """
        response = self._make_request(
            "POST",
            "/api/rpc/files/get_changed_ids",
            data={
                "exec_id": self.exec_id,
                "files_checksums": files_checksums,
            },
        )
        return set(response.get("changed_ids", []))

    def fetch_unknown_files(
        self,
        files_fshas: Dict[str, str],
        exec_id: int,
        restrict_to_known: bool = False,
    ) -> List[str]:
        """Return unknown files list from server or empty on error."""
        response = self._make_request(
            "POST",
            "/api/rpc/files/fetch_unknown",
            data={
                "exec_id": exec_id,
                "files_fshas": files_fshas,
                "restrict_to_known": restrict_to_known,
            },
        )
        return response.get("unknown_files", [])


def create_net_db_from_env() -> Optional[NetDB]:
    """
    Create a NetDB instance from environment variables.

    Required environment variables:
        TESTMON_NET_ENABLED: Set to 'true' to enable
        TESTMON_SERVER: Server URL (e.g., 'https://your-server.com')
        REPO_ID or GITHUB_REPOSITORY: Repository identifier
        JOB_ID: Job/variant identifier

    Optional:
        TESTMON_AUTH_TOKEN: Authentication token
        RUN_ID or GITHUB_RUN_ID: CI run ID

    Returns:
        NetDB instance if enabled and configured, None otherwise
    """
    if os.environ.get("TESTMON_NET_ENABLED", "").lower() != "true":
        return None

    server_url = os.environ.get("TESTMON_SERVER")
    if not server_url:
        logger.warning("TESTMON_NET_ENABLED is true but TESTMON_SERVER is not set")
        return None
    if "ezmon.aloiz.ch" in server_url:
        logger.info("NetDB server is disabled for this endpoint; using local database.")
        return None

    repo_id = os.environ.get("REPO_ID") or os.environ.get("GITHUB_REPOSITORY")
    if not repo_id:
        logger.warning("TESTMON_NET_ENABLED is true but REPO_ID/GITHUB_REPOSITORY is not set")
        return None

    job_id = os.environ.get("JOB_ID")
    if not job_id:
        logger.warning("TESTMON_NET_ENABLED is true but JOB_ID is not set")
        return None

    auth_token = os.environ.get("TESTMON_AUTH_TOKEN")
    run_id = os.environ.get("RUN_ID") or os.environ.get("GITHUB_RUN_ID")

    return NetDB(
        server_url=server_url,
        repo_id=repo_id,
        job_id=job_id,
        auth_token=auth_token,
        run_id=run_id,
    )
