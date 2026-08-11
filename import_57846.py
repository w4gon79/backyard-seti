"""
Import all 6 files of epoch 57846 hit data into the DB.

The scan PROXCEN_2026-08-10_0936 only had 99,496 hits imported (from the first file).
This script imports all 902,492 hits from combined_corrected.json.

Strategy:
1. Delete existing hits for this scan (only 99K from first file)
2. Re-import from combined_corrected.json (has all 6 files with barycentric data)
3. Update scans table with correct counts
"""
import sys
import os
import json
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))
from db import get_db, DB_PATH, init_db, get_scan, count_hits, get_hit_stats

SCAN_ID = 'PROXCEN_2026-08-10_0936'
SCAN_DIR = os.path.join(os.path.dirname(__file__), 'results', SCAN_ID)
COMBINED_PATH = os.path.join(SCAN_DIR, 'barycentric', 'combined_corrected.json')


def main():
    init_db()

    # Show current state
    current = count_hits(SCAN_ID)
    print(f"Current DB state: {current:,} hits for {SCAN_ID}")

    existing_scan = get_scan(SCAN_ID)
    if existing_scan:
        print(f"  Scan record: total={existing_scan['total_hits']}, on={existing_scan['on_hits']}, off={existing_scan['off_hits']}")

    # Load combined data
    print(f"\nLoading {COMBINED_PATH}...")
    t0 = time.time()
    with open(COMBINED_PATH) as f:
        combined = json.load(f)
    hits = combined.get('hits', [])
    print(f"Loaded {len(hits):,} hits in {time.time()-t0:.1f}s")

    # Verify data quality
    from collections import Counter
    on_off = Counter(h.get('on_off', 'MISSING') for h in hits)
    print(f"  ON: {on_off.get('ON', 0):,}  OFF: {on_off.get('OFF', 0):,}")

    files = Counter(h.get('source_file', h.get('file', '?')) for h in hits)
    print(f"  Source files: {len(files)}")
    for sf, cnt in sorted(files.items()):
        print(f"    {sf}: {cnt:,}")

    # Delete existing hits for this scan
    conn = get_db()
    print(f"\nDeleting existing {current:,} hits...")
    t0 = time.time()
    conn.execute('DELETE FROM hits WHERE scan_id = ?', (SCAN_ID,))
    conn.commit()
    print(f"  Deleted in {time.time()-t0:.1f}s")

    # Bulk insert all hits
    print(f"\nInserting {len(hits):,} hits...")
    t0 = time.time()
    BATCH = 10000
    inserted = 0
    for i in range(0, len(hits), BATCH):
        batch = hits[i:i + BATCH]
        conn.executemany('''
            INSERT INTO hits (scan_id, source_file, on_off, freq, barycentric_freq,
                              drift_rate, snr, channel, sub_band, mjd)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', [
            (SCAN_ID,
             h.get('source_file', h.get('file', '')),
             h.get('on_off', ''),
             h.get('freq', 0),
             h.get('barycentric_freq'),
             h.get('drift_rate', 0),
             h.get('snr', 0),
             h.get('channel'),
             h.get('sub_band'),
             h.get('mjd'))
            for h in batch
        ])
        conn.commit()
        inserted += len(batch)
        if inserted % 100000 == 0 or inserted == len(hits):
            print(f"  {inserted:,}/{len(hits):,} ({100*inserted/len(hits):.1f}%) in {time.time()-t0:.1f}s")

    # Update scans table
    on_count = on_off.get('ON', 0)
    off_count = on_off.get('OFF', 0)
    conn.execute('''
        UPDATE scans SET
            total_hits = ?,
            on_hits = ?,
            off_hits = ?,
            bary_corrected = 1,
            bary_velocity = ?,
            bary_mjd = ?,
            ra_hours = ?,
            dec_deg = ?,
            telescope = ?
        WHERE scan_id = ?
    ''', (
        len(hits), on_count, off_count,
        hits[0].get('barycentric_velocity_mps', 0) if hits else 0,
        combined.get('mjd', 0),
        combined.get('ra_hours'),
        combined.get('dec_deg'),
        combined.get('telescope', 'parkes'),
        SCAN_ID
    ))
    conn.commit()
    conn.close()

    elapsed = time.time() - t0
    print(f"\nDone! Inserted {len(hits):,} hits in {elapsed:.1f}s")
    print(f"  ON: {on_count:,}  OFF: {off_count:,}")

    # Verify
    final = count_hits(SCAN_ID)
    stats = get_hit_stats(SCAN_ID)
    print(f"\nVerification:")
    print(f"  Total hits in DB: {final:,}")
    print(f"  Stats: total={stats['total_hits']:,}, on={stats['on_hits']:,}, off={stats['off_hits']:,}")
    assert final == len(hits), f"Mismatch: DB has {final}, expected {len(hits)}"
    print("  ✅ Counts match!")


if __name__ == '__main__':
    main()
