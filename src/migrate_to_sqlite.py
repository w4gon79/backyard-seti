#!/usr/bin/env python3
"""
migrate_to_sqlite.py - Import existing JSON scan data into SQLite.

Walks the results/ directory, finds all scan directories with scan_meta.json,
and imports their hits into the SQLite database.

Idempotent: skips scans that are already imported (checked by hit count).
"""

import os
import sys
import time

# Add src to path
SETI_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(SETI_ROOT, 'src'))

from db import init_db, import_scan_from_json, db_stats, DB_PATH

RESULTS_DIR = os.path.join(SETI_ROOT, 'results')


def main():
    print(f"=" * 60)
    print(f"SETI SQLite Migration")
    print(f"=" * 60)
    print(f"  DB: {DB_PATH}")
    print(f"  Results: {RESULTS_DIR}")
    print()

    # Initialize schema
    print("Initializing database schema...")
    init_db()
    print("  Done.\n")

    # Find all scan directories
    if not os.path.isdir(RESULTS_DIR):
        print("No results directory found. Nothing to migrate.")
        return

    scan_dirs = []
    for entry in sorted(os.listdir(RESULTS_DIR)):
        full_path = os.path.join(RESULTS_DIR, entry)
        if not os.path.isdir(full_path):
            continue
        # Check if it looks like a scan directory
        meta_path = os.path.join(full_path, 'scan_meta.json')
        has_hits = any(
            f.endswith('_hits.json')
            for root, dirs, files in os.walk(full_path)
            for f in files
        )
        if os.path.isfile(meta_path) or has_hits or entry == 'validation_50mhz':
            scan_dirs.append(full_path)

    if not scan_dirs:
        print("No scan directories found to migrate.")
        return

    print(f"Found {len(scan_dirs)} scan directories to import.\n")

    total_imported = 0
    total_skipped = 0
    total_errors = 0

    for scan_dir in scan_dirs:
        scan_name = os.path.basename(scan_dir)
        print(f"  Importing: {scan_name}")
        t0 = time.time()

        try:
            stats = import_scan_from_json(scan_dir)
            elapsed = time.time() - t0

            if stats.get('skipped'):
                print(f"    SKIPPED (already in DB: {stats.get('hits_in_db', 0):,} hits)")
                total_skipped += 1
            else:
                n = stats.get('hits_imported', 0)
                b = stats.get('bary_updated', 0)
                print(f"    Imported {n:,} hits" +
                      (f", {b:,} barycentric corrections" if b else "") +
                      f" ({elapsed:.1f}s)")
                total_imported += 1
        except Exception as e:
            print(f"    ERROR: {e}")
            import traceback
            traceback.print_exc()
            total_errors += 1

    print(f"\n{'=' * 60}")
    print(f"Migration complete: {total_imported} imported, {total_skipped} skipped, {total_errors} errors")

    # Print DB stats
    s = db_stats()
    print(f"\nDatabase stats:")
    print(f"  Scans: {s['scans']}")
    print(f"  Hits: {s['hits']:,}")
    print(f"  Cross-epoch results: {s['cross_epoch_results']}")
    print(f"  DB size: {s['db_size_mb']} MB")


if __name__ == '__main__':
    main()
