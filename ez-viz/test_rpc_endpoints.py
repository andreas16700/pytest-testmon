#!/usr/bin/env python3
"""
Test script for RPC endpoints.

Usage:
    python test_rpc_endpoints.py [--url URL]

Examples:
    python test_rpc_endpoints.py
    python test_rpc_endpoints.py --url https://your-server.com
"""
import argparse
import requests
import json
import sys
import time

DEFAULT_URL = "http://localhost:8004"


class RPCEndpointTester:
    """Test RPC endpoints against a running server."""

    def __init__(self, base_url: str, auth_token: str = "test-token"):
        self.base_url = base_url.rstrip("/")
        self.auth_token = auth_token
        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {auth_token}",
            "X-Repo-ID": "test/rpc-endpoint-test",
            "X-Job-ID": f"rpc-test-{int(time.time())}",
        }
        self.session_id = None
        self.exec_id = None

    def _post(self, endpoint: str, data: dict, extra_headers: dict = None) -> requests.Response:
        """Make POST request."""
        headers = {**self.headers}
        if extra_headers:
            headers.update(extra_headers)
        return requests.post(
            f"{self.base_url}{endpoint}",
            headers=headers,
            json=data,
            timeout=30,
        )

    def _get(self, endpoint: str, params: dict = None) -> requests.Response:
        """Make GET request."""
        return requests.get(
            f"{self.base_url}{endpoint}",
            headers=self.headers,
            params=params,
            timeout=30,
        )

    def test_health(self) -> bool:
        """Test health endpoint."""
        try:
            r = requests.get(f"{self.base_url}/health", timeout=5)
            if r.status_code != 200:
                print(f"  ✗ Health check failed: {r.status_code} {r.text}")
                return False
            print("  ✓ Health check passed")
            return True
        except requests.exceptions.ConnectionError:
            print(f"  ✗ Cannot connect to {self.base_url}")
            return False

    def test_auth_required(self) -> bool:
        """Test that auth is required."""
        r = requests.post(
            f"{self.base_url}/api/rpc/session/initiate",
            headers={"Content-Type": "application/json"},  # No auth
            json={"repo_id": "test/repo", "job_id": "test-job"},
            timeout=10,
        )
        if r.status_code != 401:
            print(f"  ✗ Expected 401, got {r.status_code}")
            return False
        print("  ✓ Auth required check passed")
        return True

    def test_session_initiate(self) -> bool:
        """Test session initiation."""
        r = self._post(
            "/api/rpc/session/initiate",
            {
                "repo_id": self.headers["X-Repo-ID"],
                "job_id": self.headers["X-Job-ID"],
                "environment_name": "default",
                "system_packages": "pytest 7.0.0, requests 2.28.0",
                "python_version": "3.11.0",
            },
        )
        if r.status_code != 200:
            print(f"  ✗ Session initiate failed: {r.status_code} {r.text}")
            return False

        data = r.json()
        self.session_id = data.get("session_id")
        self.exec_id = data.get("exec_id")

        if not self.session_id or not self.exec_id:
            print(f"  ✗ Missing session_id or exec_id: {data}")
            return False

        print(f"  ✓ Session initiated: exec_id={self.exec_id}")
        return True

    def test_batch_insert(self) -> bool:
        """Test batch insert of test results."""
        if not self.exec_id:
            print("  ✗ No exec_id (run test_session_initiate first)")
            return False

        test_data = {
            "tests/test_example.py::TestClass::test_one": {
                "duration": 0.5,
                "failed": False,
                "forced": True,
                "deps": [
                    {
                        "filename": "src/example.py",
                        "fsha": "abc123def456",
                        "mtime": 1700000000.0,
                        "method_checksums": "01020304",  # hex-encoded
                    }
                ],
                "file_deps": [],
                "external_deps": [],
            },
            "tests/test_example.py::TestClass::test_two": {
                "duration": 1.2,
                "failed": True,
                "forced": True,
                "deps": [
                    {
                        "filename": "src/example.py",
                        "fsha": "abc123def456",
                        "mtime": 1700000000.0,
                        "method_checksums": "05060708",
                    },
                    {
                        "filename": "src/helper.py",
                        "fsha": "xyz789",
                        "mtime": 1700000001.0,
                        "method_checksums": "090a0b0c",
                    },
                ],
                "file_deps": [],
                "external_deps": [],
            },
        }

        r = self._post(
            "/api/rpc/test_execution/batch_insert",
            {"exec_id": self.exec_id, "tests": test_data},
        )

        if r.status_code != 200:
            print(f"  ✗ Batch insert failed: {r.status_code} {r.text}")
            return False

        data = r.json()
        if data.get("inserted") != 2:
            print(f"  ✗ Expected 2 inserted, got {data}")
            return False

        print(f"  ✓ Batch insert: {data['inserted']} tests inserted")
        return True

    def test_get_all_tests(self) -> bool:
        """Test retrieving all test executions."""
        if not self.exec_id:
            print("  ✗ No exec_id (run test_session_initiate first)")
            return False

        r = self._get("/api/rpc/tests/all", {"exec_id": self.exec_id})

        if r.status_code != 200:
            print(f"  ✗ Get all tests failed: {r.status_code} {r.text}")
            return False

        data = r.json()
        tests = data.get("tests", {})

        if len(tests) < 2:
            print(f"  ✗ Expected at least 2 tests, got {len(tests)}")
            return False

        print(f"  ✓ Get all tests: {len(tests)} tests found")
        return True

    def test_files_list(self) -> bool:
        """Test retrieving file list."""
        if not self.exec_id:
            print("  ✗ No exec_id (run test_session_initiate first)")
            return False

        r = self._get("/api/rpc/files/list", {"exec_id": self.exec_id})

        if r.status_code != 200:
            print(f"  ✗ Files list failed: {r.status_code} {r.text}")
            return False

        data = r.json()
        filenames = data.get("filenames", [])
        print(f"  ✓ Files list: {len(filenames)} files")
        return True

    def test_files_fingerprints(self) -> bool:
        """Test retrieving fingerprints."""
        if not self.exec_id:
            print("  ✗ No exec_id (run test_session_initiate first)")
            return False

        r = self._get("/api/rpc/files/fingerprints", {"exec_id": self.exec_id})

        if r.status_code != 200:
            print(f"  ✗ Files fingerprints failed: {r.status_code} {r.text}")
            return False

        data = r.json()
        fingerprints = data.get("fingerprints", [])
        print(f"  ✓ Files fingerprints: {len(fingerprints)} entries")
        return True

    def test_metadata_write_read(self) -> bool:
        """Test metadata write and read."""
        if not self.exec_id:
            print("  ✗ No exec_id (run test_session_initiate first)")
            return False

        # Write metadata
        test_value = {"key": "value", "number": 42}
        r = self._post(
            "/api/rpc/metadata/write",
            {
                "attribute": "test_attribute",
                "data": test_value,
                "exec_id": self.exec_id,
            },
        )

        if r.status_code != 200:
            print(f"  ✗ Metadata write failed: {r.status_code} {r.text}")
            return False

        # Read metadata
        r = self._get(
            "/api/rpc/metadata/read",
            {"attribute": "test_attribute", "exec_id": self.exec_id},
        )

        if r.status_code != 200:
            print(f"  ✗ Metadata read failed: {r.status_code} {r.text}")
            return False

        data = r.json().get("data")
        if data != test_value:
            print(f"  ✗ Metadata mismatch: expected {test_value}, got {data}")
            return False

        print("  ✓ Metadata write/read passed")
        return True

    def test_session_finish(self) -> bool:
        """Test session finish."""
        if not self.exec_id:
            print("  ✗ No exec_id (run test_session_initiate first)")
            return False

        extra_headers = {}
        if self.session_id:
            extra_headers["X-Session-ID"] = self.session_id

        r = self._post(
            "/api/rpc/session/finish",
            {"exec_id": self.exec_id, "select": True},
            extra_headers=extra_headers,
        )

        if r.status_code != 200:
            print(f"  ✗ Session finish failed: {r.status_code} {r.text}")
            return False

        data = r.json()
        print(f"  ✓ Session finished: saved_tests={data.get('run_saved_tests')}")
        return True

    def run_all_tests(self) -> bool:
        """Run all tests in sequence."""
        print(f"\nTesting RPC endpoints at {self.base_url}\n")

        tests = [
            ("Health Check", self.test_health),
            ("Auth Required", self.test_auth_required),
            ("Session Initiate", self.test_session_initiate),
            ("Batch Insert", self.test_batch_insert),
            ("Get All Tests", self.test_get_all_tests),
            ("Files List", self.test_files_list),
            ("Files Fingerprints", self.test_files_fingerprints),
            ("Metadata Write/Read", self.test_metadata_write_read),
            ("Session Finish", self.test_session_finish),
        ]

        passed = 0
        failed = 0

        for name, test_func in tests:
            print(f"[{name}]")
            try:
                if test_func():
                    passed += 1
                else:
                    failed += 1
            except Exception as e:
                print(f"  ✗ Exception: {e}")
                failed += 1

        print(f"\n{'='*40}")
        print(f"Results: {passed} passed, {failed} failed")
        print(f"{'='*40}")

        return failed == 0


def main():
    parser = argparse.ArgumentParser(description="Test RPC endpoints")
    parser.add_argument(
        "--url",
        default=DEFAULT_URL,
        help=f"Server URL (default: {DEFAULT_URL})",
    )
    parser.add_argument(
        "--token",
        default="test-token",
        help="Auth token (default: test-token)",
    )
    args = parser.parse_args()

    tester = RPCEndpointTester(args.url, args.token)
    success = tester.run_all_tests()

    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
