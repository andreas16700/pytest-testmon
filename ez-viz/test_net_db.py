#!/usr/bin/env python3
"""
Test NetDB client against a running server.

Usage:
    python test_net_db.py [--url URL]

Examples:
    python test_net_db.py
    python test_net_db.py --url https://your-server.com
"""
import argparse
import os
import sys
import time

# Add parent directory to path for ezmon imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ezmon.net_db import NetDB, NetDBException
from ezmon.process_code import checksums_to_blob

DEFAULT_URL = "http://localhost:8004"


class NetDBTester:
    """Test NetDB client functionality."""

    def __init__(self, server_url: str, auth_token: str = "test-token"):
        self.server_url = server_url
        self.auth_token = auth_token
        self.repo_id = "test/netdb-client-test"
        self.job_id = f"netdb-test-{int(time.time())}"
        self.net_db = None
        self.exec_id = None

    def setup(self):
        """Create NetDB instance."""
        self.net_db = NetDB(
            server_url=self.server_url,
            repo_id=self.repo_id,
            job_id=self.job_id,
            auth_token=self.auth_token,
        )
        print(f"  Created NetDB instance for {self.repo_id}/{self.job_id}")

    def teardown(self):
        """Close NetDB connection."""
        if self.net_db:
            self.net_db.close()

    def test_initiate_execution(self) -> bool:
        """Test initiate_execution method."""
        try:
            result = self.net_db.initiate_execution(
                environment_name="default",
                system_packages="pytest 7.0.0, requests 2.28.0",
                python_version="3.11.0",
                execution_metadata={"test": True},
            )

            self.exec_id = result.get("exec_id")
            if not self.exec_id:
                print(f"  ✗ No exec_id in response: {result}")
                return False

            print(f"  ✓ initiate_execution: exec_id={self.exec_id}")
            return True
        except NetDBException as e:
            print(f"  ✗ initiate_execution failed: {e}")
            return False

    def test_insert_test_file_fps(self) -> bool:
        """Test insert_test_file_fps method."""
        if not self.exec_id:
            print("  ✗ No exec_id (run test_initiate_execution first)")
            return False

        try:
            # Create test data with proper fingerprint format
            test_data = {
                "tests/test_netdb.py::TestNetDB::test_connection": {
                    "duration": 0.75,
                    "failed": False,
                    "forced": True,
                    "deps": [
                        {
                            "filename": "src/connection.py",
                            "fsha": "sha256_connection_hash",
                            "mtime": 1700000000.0,
                            "method_checksums": [123456789, 987654321],
                        }
                    ],
                    "file_deps": [
                        {"filename": "config.json", "sha": "config_hash_123"},
                    ],
                    "external_deps": ["requests"],
                },
                "tests/test_netdb.py::TestNetDB::test_query": {
                    "duration": 1.25,
                    "failed": True,
                    "forced": True,
                    "deps": [
                        {
                            "filename": "src/connection.py",
                            "fsha": "sha256_connection_hash",
                            "mtime": 1700000000.0,
                            "method_checksums": [123456789],
                        },
                        {
                            "filename": "src/query.py",
                            "fsha": "sha256_query_hash",
                            "mtime": 1700000001.0,
                            "method_checksums": [111111111, 222222222, 333333333],
                        },
                    ],
                    "file_deps": [],
                    "external_deps": [],
                },
            }

            self.net_db.insert_test_file_fps(test_data, exec_id=self.exec_id)
            print("  ✓ insert_test_file_fps: 2 tests inserted")
            return True
        except NetDBException as e:
            print(f"  ✗ insert_test_file_fps failed: {e}")
            return False

    def test_all_test_executions(self) -> bool:
        """Test all_test_executions method."""
        if not self.exec_id:
            print("  ✗ No exec_id (run test_initiate_execution first)")
            return False

        try:
            tests = self.net_db.all_test_executions(self.exec_id)

            if not isinstance(tests, dict):
                print(f"  ✗ Expected dict, got {type(tests)}")
                return False

            if len(tests) < 2:
                print(f"  ✗ Expected at least 2 tests, got {len(tests)}")
                return False

            # Verify test data
            test_names = list(tests.keys())
            print(f"  ✓ all_test_executions: {len(tests)} tests")
            for name in test_names[:2]:
                print(f"      - {name}")
            return True
        except NetDBException as e:
            print(f"  ✗ all_test_executions failed: {e}")
            return False

    def test_filenames(self) -> bool:
        """Test filenames method."""
        if not self.exec_id:
            print("  ✗ No exec_id (run test_initiate_execution first)")
            return False

        try:
            filenames = self.net_db.filenames(self.exec_id)

            if not isinstance(filenames, list):
                print(f"  ✗ Expected list, got {type(filenames)}")
                return False

            print(f"  ✓ filenames: {len(filenames)} files")
            return True
        except NetDBException as e:
            print(f"  ✗ filenames failed: {e}")
            return False

    def test_filenames_fingerprints(self) -> bool:
        """Test filenames_fingerprints method."""
        if not self.exec_id:
            print("  ✗ No exec_id (run test_initiate_execution first)")
            return False

        try:
            fps = self.net_db.filenames_fingerprints(self.exec_id)

            if not isinstance(fps, list):
                print(f"  ✗ Expected list, got {type(fps)}")
                return False

            print(f"  ✓ filenames_fingerprints: {len(fps)} entries")
            return True
        except NetDBException as e:
            print(f"  ✗ filenames_fingerprints failed: {e}")
            return False

    def test_write_fetch_attribute(self) -> bool:
        """Test write_attribute and fetch_attribute methods."""
        if not self.exec_id:
            print("  ✗ No exec_id (run test_initiate_execution first)")
            return False

        try:
            # Write attribute
            test_data = {"custom_key": "custom_value", "count": 42}
            self.net_db.write_attribute("test_attr", test_data, exec_id=self.exec_id)

            # Fetch attribute
            fetched = self.net_db.fetch_attribute("test_attr", exec_id=self.exec_id)

            if fetched != test_data:
                print(f"  ✗ Attribute mismatch: expected {test_data}, got {fetched}")
                return False

            print("  ✓ write_attribute/fetch_attribute passed")
            return True
        except NetDBException as e:
            print(f"  ✗ write/fetch attribute failed: {e}")
            return False

    def test_fetch_unknown_files(self) -> bool:
        """Test fetch_unknown_files method."""
        if not self.exec_id:
            print("  ✗ No exec_id (run test_initiate_execution first)")
            return False

        try:
            # Test with a file SHA that doesn't match
            files_fshas = {
                "src/connection.py": "different_sha",  # Changed
                "src/query.py": "sha256_query_hash",  # Same
            }

            unknown = self.net_db.fetch_unknown_files(files_fshas, self.exec_id)

            if not isinstance(unknown, list):
                print(f"  ✗ Expected list, got {type(unknown)}")
                return False

            print(f"  ✓ fetch_unknown_files: {len(unknown)} unknown files")
            return True
        except NetDBException as e:
            print(f"  ✗ fetch_unknown_files failed: {e}")
            return False

    def test_determine_tests(self) -> bool:
        """Test determine_tests method."""
        if not self.exec_id:
            print("  ✗ No exec_id (run test_initiate_execution first)")
            return False

        try:
            # Simulate changed files
            files_mhashes = {
                "src/connection.py": [999999999],  # Different checksums
            }

            result = self.net_db.determine_tests(
                self.exec_id,
                files_mhashes,
                file_deps_shas={},
            )

            if "affected" not in result or "failing" not in result:
                print(f"  ✗ Missing keys in response: {result}")
                return False

            print(f"  ✓ determine_tests: {len(result['affected'])} affected, {len(result['failing'])} failing")
            return True
        except NetDBException as e:
            print(f"  ✗ determine_tests failed: {e}")
            return False

    def test_finish_execution(self) -> bool:
        """Test finish_execution method."""
        if not self.exec_id:
            print("  ✗ No exec_id (run test_initiate_execution first)")
            return False

        try:
            self.net_db.finish_execution(self.exec_id)
            print("  ✓ finish_execution completed")
            return True
        except NetDBException as e:
            print(f"  ✗ finish_execution failed: {e}")
            return False

    def run_all_tests(self) -> bool:
        """Run all tests in sequence."""
        print(f"\nTesting NetDB client against {self.server_url}\n")

        self.setup()

        tests = [
            ("Initiate Execution", self.test_initiate_execution),
            ("Insert Test File FPs", self.test_insert_test_file_fps),
            ("All Test Executions", self.test_all_test_executions),
            ("Filenames", self.test_filenames),
            ("Filenames Fingerprints", self.test_filenames_fingerprints),
            ("Write/Fetch Attribute", self.test_write_fetch_attribute),
            ("Fetch Unknown Files", self.test_fetch_unknown_files),
            ("Determine Tests", self.test_determine_tests),
            ("Finish Execution", self.test_finish_execution),
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
                import traceback
                traceback.print_exc()
                failed += 1

        self.teardown()

        print(f"\n{'='*40}")
        print(f"Results: {passed} passed, {failed} failed")
        print(f"{'='*40}")

        return failed == 0


def main():
    parser = argparse.ArgumentParser(description="Test NetDB client")
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

    tester = NetDBTester(args.url, args.token)
    success = tester.run_all_tests()

    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
