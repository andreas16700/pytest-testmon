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
from typing import Dict, List, Optional, Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ezmon.process_code import blob_to_checksums, checksums_to_blob
from ezmon.common import TestExecutions, get_logger

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
        retry_strategy = Retry(
            total=3,
            backoff_factor=0.5,
            status_forcelist=[429, 500, 502, 503, 504],
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
        return 16  # Match DB.DATA_VERSION

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
        }

    def finish_execution(self, exec_id: int, duration: float = None, select: bool = True):
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
            },
        )

        # Clear caches
        self._fingerprint_cache.clear()
        self._file_dependency_cache.clear()

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
            files_mhashes: Dict of {filename: method_checksums}
            file_deps_shas: Dict of {filename: sha} for non-Python dependencies
            changed_packages: Set of package names that changed (granular tracking)

        Returns:
            Dict with 'affected' and 'failing' test lists
        """
        # Convert method_checksums to base64 for transport
        serialized_mhashes = {}
        for filename, mhashes in files_mhashes.items():
            if mhashes is not None:
                serialized_mhashes[filename] = checksums_to_blob(mhashes).hex()
            else:
                serialized_mhashes[filename] = None

        response = self._make_request(
            "POST",
            "/api/rpc/tests/determine",
            data={
                "exec_id": exec_id,
                "files_mhashes": serialized_mhashes,
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
        method_checksums: bytes,
    ) -> int:
        """
        Fetch or create a fingerprint record.

        Uses client-side LRU cache to minimize network calls.
        """
        cache_key = (filename, fsha, method_checksums)
        if cache_key in self._fingerprint_cache:
            return self._fingerprint_cache[cache_key]

        response = self._make_request(
            "POST",
            "/api/rpc/fingerprint/fetch_or_create",
            data={
                "exec_id": self.exec_id,
                "filename": filename,
                "fsha": fsha,
                "method_checksums": method_checksums.hex() if method_checksums else None,
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
                    "method_checksums": checksums_to_blob(dep["method_checksums"]).hex()
                        if dep.get("method_checksums") else None,
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

        # Deserialize method_checksums from hex
        result = []
        for row in response.get("data", []):
            result.append([
                row["filename"],
                row["test_name"],
                blob_to_checksums(bytes.fromhex(row["method_checksums"]))
                    if row.get("method_checksums") else [],
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

    def insert_dependency_graph_edges(self, edges: List[tuple], exec_id: int):
        """Insert dependency graph edges via RPC.

        Args:
            edges: List of tuples (source_file, target_file, target_package, edge_type)
            exec_id: Execution ID to associate edges with
        """
        if not edges:
            return

        # Serialize edges for transport
        serialized_edges = [
            {
                "source_file": src,
                "target_file": tgt,
                "target_package": pkg,
                "edge_type": etype,
            }
            for src, tgt, pkg, etype in edges
        ]

        self._make_request(
            "POST",
            "/api/rpc/dependency_graph/batch_insert",
            data={
                "exec_id": exec_id,
                "edges": serialized_edges,
            },
            compress=True,
            timeout=120,
        )

    def get_dependency_graph(self, run_uid: int = None) -> List[Dict]:
        """Retrieve dependency graph edges via RPC.

        Args:
            run_uid: Optional run UID to filter by.

        Returns:
            List of dicts with keys: source_file, target_file, target_package, edge_type
        """
        response = self._make_request(
            "GET",
            "/api/rpc/dependency_graph/get",
            data={"run_uid": run_uid} if run_uid else {},
        )
        return response.get("edges", [])

    def close(self):
        """Close the HTTP session."""
        if self._http_session:
            self._http_session.close()


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
