"""Check cross-epoch data storage, scans schema, and barycentric freq distribution."""
import sqlite3
import json

DB = r'G:\seti\data\seti_hits.db'
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
c = conn.cursor()

# Scans schema
c.execute('PRAGMA table_info(scans)')
print('scans columns:', [(r[1], r[2]) for r in c.fetchall()])

# PROXCEN scans
c.execute("SELECT * FROM scans WHERE target = 'PROXCEN' ORDER BY rowid")
scans = c.fetchall()
print(f'\nPROXCEN scans ({len(scans)}):')
for s in scans:
    d = dict(s)
    print(f'  scan_id={d.get("scan_id", "?")}, mjd={d.get("mjd", "?")}')

# Cross-epoch result detail
c.execute('SELECT * FROM cross_epoch_results')
xep = c.fetchall()
print(f'\nCross-epoch searches ({len(xep)}):')
for r in xep:
    d = dict(r)
    result = json.loads(d.get('result_json', '{}'))
    summary = result.get('summary', {})
    print(f'  ID {d["id"]}: tolerance={d["tolerance_hz"]}Hz, min_epochs={d["min_epochs"]}, '
          f'min_snr={d["min_snr"]}, candidates={d["candidate_count"]}')
    print(f'    Summary: {summary}')

# Check barycentric freq distribution per epoch
# Group hits by scan_id, get barycentric freq range
c.execute("""
    SELECT h.scan_id, COUNT(*) as n_hits,
           MIN(h.barycentric_freq) as bc_min,
           MAX(h.barycentric_freq) as bc_max,
           AVG(h.barycentric_freq) as bc_mean
    FROM hits h
    WHERE h.barycentric_freq IS NOT NULL AND h.barycentric_freq != 0
    GROUP BY h.scan_id
    ORDER BY h.scan_id
""")
print('\nBarycentric freq by scan:')
for row in c.fetchall():
    d = dict(row)
    print(f'  {d["scan_id"]}: {d["n_hits"]} hits, '
          f'bc_freq {d["bc_min"]:.6f} - {d["bc_max"]:.6f} (mean {d["bc_mean"]:.6f})')

# Check ON-only hits and their barycentric freq clusters
# How many distinct barycentric frequencies appear in 2+ ON scans?
print('\n=== Multi-epoch barycentric freq analysis ===')

# Get all ON hits with their scan_id, group by barycentric freq (rounded)
# First check how many ON scans we have
c.execute("""
    SELECT DISTINCT h.scan_id 
    FROM hits h 
    JOIN scans s ON h.scan_id = s.scan_id 
    WHERE s.target = 'PROXCEN' AND h.on_off = 'ON'
    ORDER BY h.scan_id
""")
on_scans = [r['scan_id'] for r in c.fetchall()]
print(f'ON scans: {on_scans}')

# For each pair of ON scans from different epochs, check freq overlap
# Round barycentric freq to ~1 Hz resolution and find matches
for tol in [10, 50, 100, 500, 1000]:
    c.execute(f"""
        SELECT ROUND(h1.barycentric_freq, 6) as bf, COUNT(DISTINCT h1.scan_id) as n_scans
        FROM hits h1
        JOIN scans s1 ON h1.scan_id = s1.scan_id
        WHERE s1.target = 'PROXCEN' AND h1.on_off = 'ON'
        AND h1.snr >= 8
        GROUP BY ROUND(h1.barycentric_freq, 6)
        HAVING n_scans >= 2
        LIMIT 5
    """)
    matches = c.fetchall()
    c.execute(f"""
        SELECT COUNT(*) FROM (
            SELECT ROUND(h1.barycentric_freq, 6) as bf, COUNT(DISTINCT h1.scan_id) as n_scans
            FROM hits h1
            JOIN scans s1 ON h1.scan_id = s1.scan_id
            WHERE s1.target = 'PROXCEN' AND h1.on_off = 'ON'
            AND h1.snr >= 8
            GROUP BY ROUND(h1.barycentric_freq, 6)
            HAVING n_scans >= 2
        )
    """)
    total_multi = c.fetchone()[0]
    print(f'  Exact bc_freq match in 2+ ON scans (SNR>=8): {total_multi}')

conn.close()
