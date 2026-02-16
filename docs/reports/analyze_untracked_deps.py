#!/usr/bin/env python3
"""
Analyze git history for untracked dependency files.

This script reads the untracked-dependencies.json file and queries git history
to find when those dependency files were modified. It outputs a CSV showing
all changes to untracked dependencies from most recent to oldest.

Usage:
    python analyze_untracked_deps.py [--repo matplotlib|pandas|scipy|all] [--limit N]
"""

import json
import subprocess
import csv
import argparse
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Optional


def get_git_log_for_file(repo_path: str, file_path: str, limit: Optional[int] = None) -> List[Dict[str, str]]:
    """
    Get git log entries for a specific file.

    Returns list of dicts with: commit_hash, date, author, message
    """
    # Check if file exists in repo (either now or historically)
    cmd = [
        "git", "log",
        "--pretty=format:%H|%aI|%an|%s",
        "--follow",  # Follow file renames
        "--"
    ]

    if limit:
        cmd.insert(2, f"-n{limit}")

    cmd.append(file_path)

    try:
        result = subprocess.run(
            cmd,
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=30
        )

        if result.returncode != 0:
            return []

        entries = []
        for line in result.stdout.strip().split('\n'):
            if not line:
                continue
            parts = line.split('|', 3)
            if len(parts) >= 4:
                entries.append({
                    'commit_hash': parts[0],
                    'date': parts[1],
                    'author': parts[2],
                    'message': parts[3][:200]  # Truncate long messages
                })

        return entries

    except subprocess.TimeoutExpired:
        print(f"  Timeout getting log for {file_path}")
        return []
    except Exception as e:
        print(f"  Error getting log for {file_path}: {e}")
        return []


def file_exists_in_repo(repo_path: str, file_path: str) -> bool:
    """Check if a file exists in the repo (either on disk or in git history)."""
    # Check on disk
    full_path = Path(repo_path) / file_path
    if full_path.exists():
        return True

    # Check in git history
    try:
        result = subprocess.run(
            ["git", "log", "-1", "--", file_path],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=10
        )
        return bool(result.stdout.strip())
    except:
        return False


def get_total_commits_for_file(repo_path: str, file_path: str) -> int:
    """Get total number of commits that touched this file."""
    try:
        result = subprocess.run(
            ["git", "rev-list", "--count", "HEAD", "--", file_path],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=30
        )
        if result.returncode == 0:
            return int(result.stdout.strip() or 0)
    except:
        pass
    return 0


def analyze_repo(repo_name: str, repo_path: str, dependencies: List[Dict], limit: Optional[int] = None) -> List[Dict]:
    """Analyze all untracked dependencies for a repository."""
    print(f"\nAnalyzing {repo_name} at {repo_path}...")

    results = []

    for dep in dependencies:
        dep_file = dep['dependency_file']
        test_file = dep['affected_test_file']
        bypass_method = dep['bypass_method']
        description = dep.get('description', '')

        # Skip if file doesn't exist
        if not file_exists_in_repo(repo_path, dep_file):
            print(f"  Skipping {dep_file} (not found)")
            continue

        print(f"  Checking {dep_file}...")

        # Get git log for this file
        log_entries = get_git_log_for_file(repo_path, dep_file, limit)
        total_commits = get_total_commits_for_file(repo_path, dep_file)

        if not log_entries:
            print(f"    No commits found")
            continue

        print(f"    Found {len(log_entries)} commits (total: {total_commits})")

        for entry in log_entries:
            results.append({
                'repo': repo_name,
                'dependency_file': dep_file,
                'affected_test_file': test_file,
                'bypass_method': bypass_method,
                'description': description,
                'commit_hash': entry['commit_hash'],
                'commit_date': entry['date'],
                'commit_author': entry['author'],
                'commit_message': entry['message'],
                'total_commits_to_file': total_commits
            })

    return results


def main():
    parser = argparse.ArgumentParser(description='Analyze git history for untracked dependencies')
    parser.add_argument('--repo', choices=['matplotlib', 'pandas', 'scipy', 'all'],
                        default='all', help='Repository to analyze')
    parser.add_argument('--limit', type=int, default=50,
                        help='Max commits per file (default: 50)')
    parser.add_argument('--output', type=str, default=None,
                        help='Output CSV path (default: <repo>_untracked_dep_changes.csv)')
    args = parser.parse_args()

    # Load dependency data
    script_dir = Path(__file__).parent
    deps_file = script_dir / 'untracked-dependencies.json'

    with open(deps_file, 'r') as f:
        all_deps = json.load(f)

    # Determine which repos to analyze
    if args.repo == 'all':
        repos_to_analyze = list(all_deps.keys())
    else:
        repos_to_analyze = [args.repo]

    # Analyze each repo
    for repo_name in repos_to_analyze:
        if repo_name not in all_deps:
            print(f"Unknown repo: {repo_name}")
            continue

        repo_data = all_deps[repo_name]
        repo_path = repo_data['repo_path']
        dependencies = repo_data['dependencies']

        # Check repo exists
        if not Path(repo_path).exists():
            print(f"Repo path does not exist: {repo_path}")
            continue

        results = analyze_repo(repo_name, repo_path, dependencies, args.limit)

        if not results:
            print(f"No results for {repo_name}")
            continue

        # Sort by date (most recent first)
        results.sort(key=lambda x: x['commit_date'], reverse=True)

        # Write CSV
        if args.output:
            output_path = Path(args.output)
        else:
            output_path = script_dir / f'{repo_name}_untracked_dep_changes.csv'

        fieldnames = [
            'repo', 'dependency_file', 'affected_test_file', 'bypass_method',
            'commit_hash', 'commit_date', 'commit_author', 'commit_message',
            'total_commits_to_file', 'description'
        ]

        with open(output_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(results)

        print(f"\nWrote {len(results)} entries to {output_path}")

        # Print summary
        print(f"\n{'='*60}")
        print(f"SUMMARY FOR {repo_name.upper()}")
        print(f"{'='*60}")

        # Group by dependency file
        by_file = {}
        for r in results:
            dep = r['dependency_file']
            if dep not in by_file:
                by_file[dep] = {
                    'count': 0,
                    'test_file': r['affected_test_file'],
                    'bypass': r['bypass_method'],
                    'latest_date': r['commit_date'],
                    'total': r['total_commits_to_file']
                }
            by_file[dep]['count'] += 1

        print(f"\nDependency files with most changes (in query window):")
        sorted_files = sorted(by_file.items(), key=lambda x: x[1]['total'], reverse=True)
        for dep_file, info in sorted_files[:15]:
            print(f"  {info['total']:4d} total commits | {dep_file}")
            print(f"       └── affects: {info['test_file']}")
            print(f"       └── bypass:  {info['bypass']}")

        # Recent changes
        print(f"\nMost recent changes to untracked dependencies:")
        seen_files = set()
        for r in results[:10]:
            if r['dependency_file'] not in seen_files:
                seen_files.add(r['dependency_file'])
                date = r['commit_date'][:10]
                print(f"  {date} | {r['dependency_file'][:50]}")
                print(f"           {r['commit_message'][:60]}...")


if __name__ == '__main__':
    main()
