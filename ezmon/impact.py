#!/usr/bin/env python3
"""
Impact Estimation Tool for ezmon.

Estimates which tests would be affected by local code changes,
using fingerprint data from the remote ezmon server.

Usage:
    python -m ezmon.impact [--repo REPO_ID] [--jobs JOB1,JOB2,...] [path]

Examples:
    # Estimate impact in current directory (auto-detect repo)
    python -m ezmon.impact

    # Estimate impact for specific repo
    python -m ezmon.impact --repo matplotlib/matplotlib

    # Estimate impact for specific job variants only
    python -m ezmon.impact --jobs macos-14-py3.11,ubuntu-22.04-py3.12

    # Estimate impact in a different directory
    python -m ezmon.impact /path/to/repo

Coverage Analysis:
    # Show files with least test coverage (modules/functions least linked to tests)
    python -m ezmon.impact --coverage --repo matplotlib/matplotlib

    # Coverage analysis for specific job variant
    python -m ezmon.impact --coverage --repo matplotlib/matplotlib --job macos-14-py3.11

    # Show top 100 least covered files matching a pattern
    python -m ezmon.impact --coverage --limit 100 --pattern "lib/matplotlib"

    # Show most covered files (descending order)
    python -m ezmon.impact --coverage --order desc

    # Filter to files with at least 1 test but no more than 10
    python -m ezmon.impact --coverage --min-tests 1 --max-tests 10
"""

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import requests
from dotenv import load_dotenv

from ezmon.process_code import (
    bytes_to_string_and_fsha,
    checksums_to_blob,
)
from ezmon.testmon_core import SourceTree
from ezmon.common import get_logger

logger = get_logger(__name__)


@dataclass
class FileCoverage:
    """Coverage data for a single file."""
    filename: str
    test_count: int
    execution_count: int = 0


@dataclass
class CoverageAnalysis:
    """Coverage analysis result for a job variant."""
    repo_id: str
    job_id: str
    files: List[FileCoverage] = field(default_factory=list)
    total_files: int = 0
    total_tests: int = 0
    error: Optional[str] = None

    def summary(self, show_files: int = 20) -> str:
        """Generate a summary string."""
        if self.error:
            return f"Coverage Analysis for {self.repo_id}/{self.job_id}: ERROR - {self.error}"

        lines = [
            f"Coverage Analysis for {self.repo_id}/{self.job_id}",
            "=" * 60,
            f"Total files tracked: {self.total_files}",
            f"Total tests: {self.total_tests}",
            "",
        ]

        if self.files:
            lines.append(f"Files with least test coverage (showing {min(show_files, len(self.files))}):")
            lines.append("-" * 50)
            lines.append(f"{'File':<50} {'Tests':>8}")
            lines.append("-" * 50)

            for fc in self.files[:show_files]:
                # Truncate filename if too long
                fname = fc.filename
                if len(fname) > 48:
                    fname = "..." + fname[-45:]
                lines.append(f"{fname:<50} {fc.test_count:>8}")

            if len(self.files) > show_files:
                lines.append(f"  ... and {len(self.files) - show_files} more files")

        return "\n".join(lines)


@dataclass
class ImpactResult:
    """Result of impact estimation for a single job variant."""
    job_id: str
    affected_tests: List[str] = field(default_factory=list)
    failing_tests: List[str] = field(default_factory=list)
    changed_files: List[str] = field(default_factory=list)
    error: Optional[str] = None


@dataclass
class ImpactReport:
    """Complete impact report across all variants."""
    repo_id: str
    changed_files: List[str]
    results: List[ImpactResult] = field(default_factory=list)

    def total_affected(self) -> int:
        """Total unique affected tests across all variants."""
        all_affected = set()
        for r in self.results:
            if not r.error:
                all_affected.update(r.affected_tests)
        return len(all_affected)

    def summary(self) -> str:
        """Generate a summary string."""
        lines = [
            f"Impact Report for {self.repo_id}",
            "=" * 60,
            f"Changed files: {len(self.changed_files)}",
        ]
        for f in self.changed_files[:10]:
            lines.append(f"  - {f}")
        if len(self.changed_files) > 10:
            lines.append(f"  ... and {len(self.changed_files) - 10} more")

        lines.append("")
        lines.append("Impact by variant:")
        lines.append("-" * 40)

        for result in self.results:
            if result.error:
                lines.append(f"  {result.job_id}: ERROR - {result.error}")
            else:
                lines.append(
                    f"  {result.job_id}: {len(result.affected_tests)} affected, "
                    f"{len(result.failing_tests)} currently failing"
                )

        lines.append("")
        lines.append(f"Total unique affected tests: {self.total_affected()}")

        return "\n".join(lines)


class ImpactEstimator:
    """
    Estimates test impact using remote ezmon server data.
    """

    def __init__(
        self,
        server_url: str,
        auth_token: Optional[str] = None,
        repo_path: Optional[str] = None,
    ):
        self.server_url = server_url.rstrip("/")
        self.auth_token = auth_token
        self.repo_path = Path(repo_path or os.getcwd()).resolve()

        # HTTP session
        self._session = requests.Session()
        if auth_token:
            self._session.headers["Authorization"] = f"Bearer {auth_token}"

    def _make_request(self, method: str, endpoint: str, data: dict = None) -> dict:
        """Make an HTTP request to the server."""
        url = f"{self.server_url}{endpoint}"
        headers = {"Content-Type": "application/json"}

        try:
            if method == "GET":
                resp = self._session.get(url, headers=headers, timeout=30)
            else:
                resp = self._session.post(
                    url, headers=headers, json=data, timeout=30
                )
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            raise RuntimeError(f"Server request failed: {e}")

    def get_repo_id(self) -> Optional[str]:
        """Auto-detect repository ID from git remote."""
        try:
            result = subprocess.run(
                ["git", "remote", "get-url", "origin"],
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                check=True,
            )
            url = result.stdout.strip()

            # Parse GitHub-style URLs
            # https://github.com/owner/repo.git
            # git@github.com:owner/repo.git
            if "github.com" in url:
                if url.startswith("git@"):
                    # git@github.com:owner/repo.git
                    path = url.split(":")[-1]
                else:
                    # https://github.com/owner/repo.git
                    path = url.split("github.com/")[-1]

                # Remove .git suffix
                if path.endswith(".git"):
                    path = path[:-4]
                return path

            return None
        except subprocess.CalledProcessError:
            return None

    def get_changed_files(self, base_ref: str = "HEAD") -> List[str]:
        """
        Get list of files changed relative to base_ref.

        Uses git diff to detect:
        - Uncommitted changes (staged and unstaged)
        - Changes between HEAD and base_ref
        """
        changed = set()

        try:
            # Uncommitted changes (staged + unstaged)
            result = subprocess.run(
                ["git", "diff", "--name-only", "HEAD"],
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                check=True,
            )
            for line in result.stdout.strip().split("\n"):
                if line:
                    changed.add(line)

            # Staged changes
            result = subprocess.run(
                ["git", "diff", "--name-only", "--cached"],
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                check=True,
            )
            for line in result.stdout.strip().split("\n"):
                if line:
                    changed.add(line)

            # Untracked files (new files)
            result = subprocess.run(
                ["git", "ls-files", "--others", "--exclude-standard"],
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                check=True,
            )
            for line in result.stdout.strip().split("\n"):
                if line and line.endswith(".py"):
                    changed.add(line)

        except subprocess.CalledProcessError as e:
            logger.warning(f"Git command failed: {e}")

        # Filter to Python files only for now
        return sorted([f for f in changed if f.endswith(".py")])

    def compute_fingerprints(
        self, files: List[str]
    ) -> Tuple[Dict[str, str], Dict[str, List[int]]]:
        """
        Compute file SHAs and method checksums for the given files.

        Returns:
            Tuple of (files_fshas, files_mhashes)
        """
        files_fshas = {}
        files_mhashes = {}

        source_tree = SourceTree(rootdir=str(self.repo_path))

        for filepath in files:
            full_path = self.repo_path / filepath
            if not full_path.exists():
                # File was deleted
                files_fshas[filepath] = None
                files_mhashes[filepath] = None
                continue

            try:
                # Read file and compute SHA
                content = full_path.read_bytes()
                source_code, fsha = bytes_to_string_and_fsha(content)
                files_fshas[filepath] = fsha

                # Get method checksums using SourceTree
                # The module.method_checksums property returns integer CRC32 hashes
                module = source_tree.get_file(filepath)
                if module and hasattr(module, 'method_checksums'):
                    checksums = module.method_checksums
                    files_mhashes[filepath] = checksums
                else:
                    files_mhashes[filepath] = []

            except Exception as e:
                logger.warning(f"Failed to process {filepath}: {e}")
                files_fshas[filepath] = None
                files_mhashes[filepath] = None

        return files_fshas, files_mhashes

    def list_variants(self, repo_id: str, include_incomplete: bool = False) -> Tuple[List[str], List[dict]]:
        """
        Get list of job variants (job_ids) available for a repository.

        Args:
            repo_id: Repository identifier
            include_incomplete: If True, include variants without complete test data

        Returns:
            Tuple of (variant_ids, variant_details)
        """
        try:
            url = f"/api/rpc/repo/variants?repo_id={repo_id}"
            if include_incomplete:
                url += "&include_incomplete=true"

            response = self._make_request("GET", url)
            variants = response.get("variants", [])
            details = response.get("variants_detail", [])

            # Log summary if verbose
            total = response.get("total_variants", len(variants))
            complete = response.get("complete_variants", len(variants))
            if total != complete:
                logger.info(
                    f"Found {total} variants, {complete} with complete data"
                )

            return variants, details
        except Exception as e:
            logger.warning(f"Failed to list variants: {e}")
            return [], []

    def estimate_impact_for_variant(
        self,
        repo_id: str,
        job_id: str,
        files_fshas: Dict[str, str],
        files_mhashes: Dict[str, List[int]],
    ) -> ImpactResult:
        """
        Estimate impact for a single job variant.
        """
        result = ImpactResult(
            job_id=job_id,
            changed_files=list(files_fshas.keys()),
        )

        try:
            # Serialize method checksums for transport
            serialized_mhashes = {}
            for filename, mhashes in files_mhashes.items():
                if mhashes is not None:
                    serialized_mhashes[filename] = checksums_to_blob(mhashes).hex()
                else:
                    serialized_mhashes[filename] = None

            # Call impact estimation endpoint
            response = self._make_request(
                "POST",
                "/api/rpc/impact/estimate",
                data={
                    "repo_id": repo_id,
                    "job_id": job_id,
                    "files_fshas": files_fshas,
                    "files_mhashes": serialized_mhashes,
                },
            )

            result.affected_tests = response.get("affected", [])
            result.failing_tests = response.get("failing", [])

        except Exception as e:
            result.error = str(e)

        return result

    def estimate_impact(
        self,
        repo_id: Optional[str] = None,
        job_ids: Optional[List[str]] = None,
        include_incomplete: bool = False,
    ) -> ImpactReport:
        """
        Estimate test impact for all (or specified) variants.

        Args:
            repo_id: Repository identifier (auto-detected if not provided)
            job_ids: Specific job variants to check (all complete variants if not provided)
            include_incomplete: If True, include variants without complete test data

        Returns:
            ImpactReport with results for each variant
        """
        # Auto-detect repo if needed
        if not repo_id:
            repo_id = self.get_repo_id()
            if not repo_id:
                raise ValueError(
                    "Could not auto-detect repository ID. "
                    "Please specify with --repo"
                )

        # Get changed files
        changed_files = self.get_changed_files()
        if not changed_files:
            return ImpactReport(
                repo_id=repo_id,
                changed_files=[],
                results=[],
            )

        # Compute fingerprints
        files_fshas, files_mhashes = self.compute_fingerprints(changed_files)

        # Get variants to check
        if not job_ids:
            job_ids, variants_detail = self.list_variants(repo_id, include_incomplete)
            if not job_ids:
                # Provide helpful message about why no variants
                if not include_incomplete:
                    raise ValueError(
                        f"No variants with complete data found for {repo_id}. "
                        "Use --all to include incomplete variants, or specify with --jobs."
                    )
                else:
                    raise ValueError(
                        f"No variants found for {repo_id}. "
                        "Specify with --jobs or ensure data exists on server."
                    )

        # Estimate impact for each variant
        report = ImpactReport(
            repo_id=repo_id,
            changed_files=changed_files,
        )

        for job_id in job_ids:
            result = self.estimate_impact_for_variant(
                repo_id, job_id, files_fshas, files_mhashes
            )
            report.results.append(result)

        return report

    def analyze_coverage(
        self,
        repo_id: str,
        job_id: str,
        order: str = "asc",
        limit: int = 50,
        min_tests: int = 0,
        max_tests: Optional[int] = None,
        pattern: str = "",
    ) -> CoverageAnalysis:
        """
        Analyze test coverage to find files with least/most test dependencies.

        Args:
            repo_id: Repository identifier
            job_id: Job variant identifier
            order: 'asc' for least covered first, 'desc' for most covered
            limit: Maximum number of files to return
            min_tests: Minimum test count to include
            max_tests: Maximum test count to include (None for unlimited)
            pattern: Filename pattern filter

        Returns:
            CoverageAnalysis with file coverage data
        """
        result = CoverageAnalysis(repo_id=repo_id, job_id=job_id)

        try:
            url = (
                f"/api/rpc/coverage/analysis"
                f"?repo_id={repo_id}"
                f"&job_id={job_id}"
                f"&order={order}"
                f"&limit={limit}"
                f"&min_tests={min_tests}"
            )
            if max_tests is not None:
                url += f"&max_tests={max_tests}"
            if pattern:
                url += f"&pattern={pattern}"

            response = self._make_request("GET", url)

            result.total_files = response.get("total_files", 0)
            result.total_tests = response.get("total_tests", 0)
            result.files = [
                FileCoverage(
                    filename=f["filename"],
                    test_count=f["test_count"],
                    execution_count=f.get("execution_count", 0),
                )
                for f in response.get("files", [])
            ]

        except Exception as e:
            result.error = str(e)

        return result

    def get_tests_for_file(
        self,
        repo_id: str,
        job_id: str,
        filename: str,
        limit: int = 500,
    ) -> Tuple[List[str], int, Optional[str]]:
        """
        Get the list of tests that depend on a specific file.

        Args:
            repo_id: Repository identifier
            job_id: Job variant identifier
            filename: The file to query (exact match or pattern with % wildcard)
            limit: Maximum number of test names to return

        Returns:
            Tuple of (test_names, total_count, error)
        """
        try:
            from urllib.parse import quote
            url = (
                f"/api/rpc/coverage/tests"
                f"?repo_id={quote(repo_id)}"
                f"&job_id={quote(job_id)}"
                f"&filename={quote(filename)}"
                f"&limit={limit}"
            )

            response = self._make_request("GET", url)
            tests = response.get("tests", [])
            total_count = response.get("total_count", len(tests))
            return tests, total_count, None

        except Exception as e:
            return [], 0, str(e)


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Estimate test impact of local code changes",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "path",
        nargs="?",
        default=".",
        help="Path to the git repository (default: current directory)",
    )
    parser.add_argument(
        "--repo",
        dest="repo_id",
        help="Repository identifier (e.g., 'owner/repo'). Auto-detected if not provided.",
    )
    parser.add_argument(
        "--jobs",
        dest="job_ids",
        help="Comma-separated list of job variants to check (default: all with complete data)",
    )
    parser.add_argument(
        "--all",
        dest="include_incomplete",
        action="store_true",
        help="Include variants without complete test data (by default only complete variants are used)",
    )
    parser.add_argument(
        "--server",
        dest="server_url",
        help="Ezmon server URL (default: from TESTMON_SERVER env var or .env)",
    )
    parser.add_argument(
        "--token",
        dest="auth_token",
        help="Authentication token (default: from TESTMON_AUTH_TOKEN env var or .env)",
    )
    parser.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help="Output results as JSON",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Show verbose output including affected test names",
    )

    # Coverage analysis arguments
    parser.add_argument(
        "--coverage",
        action="store_true",
        help="Analyze test coverage instead of estimating impact. Shows files with least test coverage.",
    )
    parser.add_argument(
        "--job",
        dest="single_job",
        help="Single job variant for coverage analysis (required with --coverage)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Maximum number of files to show in coverage analysis (default: 50)",
    )
    parser.add_argument(
        "--order",
        choices=["asc", "desc"],
        default="asc",
        help="Sort order for coverage: 'asc' for least covered first (default), 'desc' for most covered",
    )
    parser.add_argument(
        "--min-tests",
        type=int,
        default=0,
        help="Minimum test count to include in coverage analysis (default: 0)",
    )
    parser.add_argument(
        "--max-tests",
        type=int,
        default=None,
        help="Maximum test count to include in coverage analysis",
    )
    parser.add_argument(
        "--pattern",
        default="",
        help="Filename pattern filter for coverage analysis (e.g., 'lib/matplotlib')",
    )
    parser.add_argument(
        "--tests-for",
        dest="tests_for_file",
        help="Get list of tests that depend on a specific file (e.g., 'lib/matplotlib/_afm.py')",
    )

    args = parser.parse_args()

    # Load .env file if present
    env_path = Path(args.path).resolve() / ".env"
    if env_path.exists():
        load_dotenv(env_path)
    else:
        # Try loading from ezmon package directory
        package_env = Path(__file__).parent.parent / ".env"
        if package_env.exists():
            load_dotenv(package_env)

    # Get configuration
    server_url = args.server_url or os.getenv("TESTMON_SERVER")
    auth_token = args.auth_token or os.getenv("TESTMON_AUTH_TOKEN")

    if not server_url:
        print("Error: No server URL provided. Set TESTMON_SERVER or use --server", file=sys.stderr)
        sys.exit(1)

    # Parse job IDs
    job_ids = None
    if args.job_ids:
        job_ids = [j.strip() for j in args.job_ids.split(",")]

    # Run estimation or coverage analysis
    try:
        estimator = ImpactEstimator(
            server_url=server_url,
            auth_token=auth_token,
            repo_path=args.path,
        )

        # Coverage analysis mode
        if args.coverage:
            # Get repo_id
            repo_id = args.repo_id or estimator.get_repo_id()
            if not repo_id:
                print("Error: Could not auto-detect repository ID. Please specify with --repo", file=sys.stderr)
                sys.exit(1)

            # Get job_id - either specified or pick first available
            job_id = args.single_job
            if not job_id:
                variants, _ = estimator.list_variants(repo_id, args.include_incomplete)
                if not variants:
                    print(f"Error: No variants found for {repo_id}. Specify with --job", file=sys.stderr)
                    sys.exit(1)
                job_id = variants[0]
                print(f"Using variant: {job_id}", file=sys.stderr)

            analysis = estimator.analyze_coverage(
                repo_id=repo_id,
                job_id=job_id,
                order=args.order,
                limit=args.limit,
                min_tests=args.min_tests,
                max_tests=args.max_tests,
                pattern=args.pattern,
            )

            if args.json_output:
                output = {
                    "repo_id": analysis.repo_id,
                    "job_id": analysis.job_id,
                    "total_files": analysis.total_files,
                    "total_tests": analysis.total_tests,
                    "files": [
                        {"filename": f.filename, "test_count": f.test_count, "execution_count": f.execution_count}
                        for f in analysis.files
                    ],
                    "error": analysis.error,
                }
                print(json.dumps(output, indent=2))
            else:
                print(analysis.summary(show_files=args.limit))

            if analysis.error:
                sys.exit(1)
            sys.exit(0)

        # Tests-for-file mode
        if args.tests_for_file:
            # Get repo_id
            repo_id = args.repo_id or estimator.get_repo_id()
            if not repo_id:
                print("Error: Could not auto-detect repository ID. Please specify with --repo", file=sys.stderr)
                sys.exit(1)

            # Get job_id - either specified or pick first available
            job_id = args.single_job
            if not job_id:
                variants, _ = estimator.list_variants(repo_id, args.include_incomplete)
                if not variants:
                    print(f"Error: No variants found for {repo_id}. Specify with --job", file=sys.stderr)
                    sys.exit(1)
                job_id = variants[0]
                print(f"Using variant: {job_id}", file=sys.stderr)

            tests, total_count, error = estimator.get_tests_for_file(
                repo_id=repo_id,
                job_id=job_id,
                filename=args.tests_for_file,
                limit=args.limit,
            )

            if error:
                print(f"Error: {error}", file=sys.stderr)
                sys.exit(1)

            if args.json_output:
                output = {
                    "repo_id": repo_id,
                    "job_id": job_id,
                    "filename": args.tests_for_file,
                    "tests": tests,
                    "count": len(tests),
                    "total_count": total_count,
                }
                print(json.dumps(output, indent=2))
            else:
                print(f"Tests depending on: {args.tests_for_file}")
                print(f"Variant: {job_id}")
                print(f"Total tests: {total_count}")
                print("-" * 60)
                for test in tests:
                    print(f"  {test}")
                if len(tests) < total_count:
                    print(f"  ... and {total_count - len(tests)} more (use --limit to see more)")

            sys.exit(0)

        # Impact estimation mode
        report = estimator.estimate_impact(
            repo_id=args.repo_id,
            job_ids=job_ids,
            include_incomplete=args.include_incomplete,
        )

        if args.json_output:
            # JSON output
            output = {
                "repo_id": report.repo_id,
                "changed_files": report.changed_files,
                "total_affected": report.total_affected(),
                "variants": [
                    {
                        "job_id": r.job_id,
                        "affected_count": len(r.affected_tests),
                        "failing_count": len(r.failing_tests),
                        "affected_tests": r.affected_tests if args.verbose else [],
                        "error": r.error,
                    }
                    for r in report.results
                ],
            }
            print(json.dumps(output, indent=2))
        else:
            # Human-readable output
            print(report.summary())

            if args.verbose and report.results:
                print("\nAffected tests by variant:")
                print("-" * 40)
                for result in report.results:
                    if result.error:
                        continue
                    print(f"\n{result.job_id}:")
                    for test in sorted(result.affected_tests)[:50]:
                        print(f"  - {test}")
                    if len(result.affected_tests) > 50:
                        print(f"  ... and {len(result.affected_tests) - 50} more")

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
