#!/usr/bin/env python
"""
CLI for querying ezmon dependency data.

Usage:
    python -m ezmon.query impact [--limit N]
    python -m ezmon.query deps <test_name>
    python -m ezmon.query dependents <file_path>
    python -m ezmon.query affected <file_path> [<file_path> ...]
"""
import argparse
import sqlite3
import sys
from pathlib import Path

from ezmon.bitmap_deps import TestDeps, BitMap


def get_connection(db_path=".testmondata"):
    """Get database connection."""
    if not Path(db_path).exists():
        print(f"Error: Database not found: {db_path}", file=sys.stderr)
        print("Run 'pytest --ezmon' first to build the dependency database.", file=sys.stderr)
        sys.exit(1)
    return sqlite3.connect(db_path)


def cmd_impact(args):
    """Show high-impact files (files that affect the most tests)."""
    conn = get_connection(args.db)
    files = {r[0]: r[1] for r in conn.execute('SELECT id, path FROM files')}

    # Count tests per file using bitmaps
    file_counts = {fid: 0 for fid in files}
    for test_id, blob in conn.execute('SELECT test_id, file_bitmap FROM test_deps'):
        deps = TestDeps.deserialize(test_id, blob)
        for fid in deps.file_ids:
            if fid in file_counts:
                file_counts[fid] += 1

    # Sort and display
    sorted_files = sorted(file_counts.items(), key=lambda x: -x[1])[:args.limit]

    print(f"{'Tests':>6}  File")
    print("-" * 60)
    for fid, count in sorted_files:
        print(f"{count:>6}  {files[fid]}")


def cmd_deps(args):
    """Show dependencies of a test."""
    conn = get_connection(args.db)
    files = {r[0]: r[1] for r in conn.execute('SELECT id, path FROM files')}

    # Find test by name (supports partial match)
    if '::' in args.test_name:
        # Exact match
        row = conn.execute('''
            SELECT t.name, td.file_bitmap FROM test_deps td
            JOIN tests t ON td.test_id = t.id
            WHERE t.name = ?
        ''', (args.test_name,)).fetchone()
    else:
        # Partial match
        row = conn.execute('''
            SELECT t.name, td.file_bitmap FROM test_deps td
            JOIN tests t ON td.test_id = t.id
            WHERE t.name LIKE ?
            LIMIT 1
        ''', (f'%{args.test_name}%',)).fetchone()

    if not row:
        print(f"Test not found: {args.test_name}", file=sys.stderr)
        # Show suggestions
        suggestions = conn.execute('''
            SELECT name FROM tests WHERE name LIKE ? LIMIT 5
        ''', (f'%{args.test_name}%',)).fetchall()
        if suggestions:
            print("\nDid you mean:", file=sys.stderr)
            for (name,) in suggestions:
                print(f"  {name}", file=sys.stderr)
        sys.exit(1)

    test_name, blob = row
    deps = TestDeps.deserialize(0, blob)

    print(f"Test: {test_name}")
    print(f"Dependencies ({len(deps.file_ids)} files):")
    for fid in sorted(deps.file_ids):
        if fid in files:
            print(f"  {files[fid]}")


def cmd_dependents(args):
    """Show tests that depend on a file."""
    conn = get_connection(args.db)
    files = {r[1]: r[0] for r in conn.execute('SELECT id, path FROM files')}
    tests = {r[0]: r[1] for r in conn.execute('SELECT id, name FROM tests')}

    # Find file (supports partial match)
    file_id = files.get(args.file_path)
    if file_id is None:
        # Try partial match
        matches = [p for p in files.keys() if args.file_path in p]
        if len(matches) == 1:
            args.file_path = matches[0]
            file_id = files[args.file_path]
        elif len(matches) > 1:
            print(f"Ambiguous file path: {args.file_path}", file=sys.stderr)
            print("Matches:", file=sys.stderr)
            for m in matches[:10]:
                print(f"  {m}", file=sys.stderr)
            sys.exit(1)
        else:
            print(f"File not found in dependencies: {args.file_path}", file=sys.stderr)
            sys.exit(1)

    # Find all tests that depend on this file
    dependents = []
    for test_id, blob in conn.execute('SELECT test_id, file_bitmap FROM test_deps'):
        deps = TestDeps.deserialize(test_id, blob)
        if file_id in deps.file_ids:
            dependents.append(tests[test_id])

    print(f"File: {args.file_path}")
    print(f"Dependent tests ({len(dependents)}):")
    for test in sorted(dependents)[:args.limit]:
        print(f"  {test}")
    if len(dependents) > args.limit:
        print(f"  ... and {len(dependents) - args.limit} more")


def cmd_affected(args):
    """Show tests affected by changes to given files."""
    conn = get_connection(args.db)
    files = {r[1]: r[0] for r in conn.execute('SELECT id, path FROM files')}
    tests = {r[0]: r[1] for r in conn.execute('SELECT id, name FROM tests')}

    # Resolve file paths to IDs
    file_ids = set()
    for path in args.files:
        fid = files.get(path)
        if fid is None:
            # Try partial match
            matches = [p for p in files.keys() if path in p]
            if len(matches) == 1:
                fid = files[matches[0]]
                print(f"Matched: {path} -> {matches[0]}", file=sys.stderr)
            elif len(matches) > 1:
                print(f"Ambiguous: {path} matches {len(matches)} files", file=sys.stderr)
                continue
            else:
                print(f"Not found: {path}", file=sys.stderr)
                continue
        file_ids.add(fid)

    if not file_ids:
        print("No valid files specified.", file=sys.stderr)
        sys.exit(1)

    # Convert to BitMap for fast intersection
    changed_bitmap = BitMap(file_ids)

    # Find affected tests (bitmap intersection)
    affected = []
    for test_id, blob in conn.execute('SELECT test_id, file_bitmap FROM test_deps'):
        deps = TestDeps.deserialize(test_id, blob)
        if deps.file_ids & changed_bitmap:  # Bitmap intersection
            affected.append(tests[test_id])

    print(f"Files changed: {len(file_ids)}")
    print(f"Affected tests: {len(affected)}")
    print()
    for test in sorted(affected)[:args.limit]:
        print(f"  {test}")
    if len(affected) > args.limit:
        print(f"  ... and {len(affected) - args.limit} more")


def main():
    parser = argparse.ArgumentParser(
        description="Query ezmon dependency data",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument('--db', default='.testmondata', help='Path to database')

    subparsers = parser.add_subparsers(dest='command', required=True)

    # impact command
    p_impact = subparsers.add_parser('impact', help='Show high-impact files')
    p_impact.add_argument('--limit', '-n', type=int, default=20, help='Max files to show')
    p_impact.set_defaults(func=cmd_impact)

    # deps command
    p_deps = subparsers.add_parser('deps', help='Show dependencies of a test')
    p_deps.add_argument('test_name', help='Test name (or partial match)')
    p_deps.set_defaults(func=cmd_deps)

    # dependents command
    p_dependents = subparsers.add_parser('dependents', help='Show tests that depend on a file')
    p_dependents.add_argument('file_path', help='File path')
    p_dependents.add_argument('--limit', '-n', type=int, default=50, help='Max tests to show')
    p_dependents.set_defaults(func=cmd_dependents)

    # affected command
    p_affected = subparsers.add_parser('affected', help='Show tests affected by file changes')
    p_affected.add_argument('files', nargs='+', help='Changed file paths')
    p_affected.add_argument('--limit', '-n', type=int, default=50, help='Max tests to show')
    p_affected.set_defaults(func=cmd_affected)

    args = parser.parse_args()
    args.func(args)


if __name__ == '__main__':
    main()
