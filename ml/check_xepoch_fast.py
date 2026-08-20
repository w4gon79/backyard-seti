"""Fast cross-epoch barycentric freq analysis using temp tables."""
import sqlite3
import time

DB = r'G:\seti\data\seti_hits.db'
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
c = conn.cursor()

# Scans schema
c.execute('PRAGMA table_info(scans)')
print('scans columns:', [(r[1], r[2]) for r in c.fetchall()])

# PROXCEN scans
c.execute("SELECT * FROM scans WHERE target = 'PROXCEN' ORDER BY rowid LIMIT 20")
scans = c.fetchall()
print(f'\nPROXCEN scans ({len(scans)} shown):')
for s in scans:
    d = dict(s)
    print(f'  {d}')

# ON scan_ids
c.execute("""
    SELECT DISTINCT h.scan_id 
    FROM hits h 
    JOIN scans s ON h.scan_id = s.scan_id 
    WHERE s.target = 'PROXCEN' AND h.on_off = 'ON'
""")
on_scans = [r['scan_id'] for r in c.fetchall()]
print(f'\nON scans: {on_scans}')

# Build temp table of ON hits at SNR>=8 with rounded barycentric freq
print('\nBuilding temp table of ON hits (SNR>=8)...')
t0 = time.time()
c.execute('DROP TABLE IF EXISTS on_bc')
c.execute("""
    CREATE TEMP TABLE on_bc AS
    SELECT h.scan_id, h.barycentric_freq, h.snr, h.drift_rate,
           ROUND(h.barycentric_freq, 5) as bf_rounded
    FROM hits h
    JOIN scans s ON h.scan_id = s.scan_id
    WHERE s.target = 'PROXCEN' AND h.on_off = 'ON' AND h.snr >= 8
""")
c.execute('CREATE INDEX idx_on_bc_r ON on_bc(bf_rounded)')
c.execute('CREATE INDEX idx_on_bc_s ON on_bc(scan_id)')
conn.commit()
print(f'  Done in {time.time()-t0:.1f}s')

c.execute('SELECT COUNT(*) FROM on_bc')
print(f'  Total ON hits at SNR>=8: {c.fetchone()[0]}')

# Count distinct barycentric freqs per scan
c.execute("""
    SELECT scan_id, COUNT(*) as n, COUNT(DISTINCT bf_rounded) as distinct_bf
    FROM on_bc GROUP BY scan_id
""")
print('\nHits per ON scan:')
for r in c.fetchall():
    print(f'  {r["scan_id"]}: {r["n"]} hits, {r["distinct_bf"]} distinct bc_freqs')

# Find barycentric freqs appearing in 2+ scans (exact match at 5 decimal places ~ 10 Hz)
for label, decimals in [('~10 Hz', 5), ('~100 Hz', 4), ('~1 kHz', 3)]:
    t0 = time.time()
    col = f'bf_rounded'
    # Use the pre-rounded column for 10 Hz, otherwise re-round
    if decimals == 5:
        round_expr = 'bf_rounded'
    else:
        round_expr = f'ROUND(barycentric_freq, {decimals})'
    
    c.execute(f"""
        SELECT {round_expr} as bf, COUNT(DISTINCT scan_id) as n_scans,
               GROUP_CONCAT(DISTINCT scan_id) as scans,
               MAX(snr) as max_snr, AVG(drift_rate) as avg_drift
        FROM on_bc
        GROUP BY {round_expr}
        HAVING n_scans >= 2
        ORDER BY n_scans DESC, max_snr DESC
        LIMIT 10
    """)
    matches = c.fetchall()
    
    c.execute(f"""
        SELECT COUNT(*) FROM (
            SELECT {round_expr} as bf, COUNT(DISTINCT scan_id) as n_scans
            FROM on_bc
            GROUP BY {round_expr}
            HAVING n_scans >= 2
        )
    """)
    total = c.fetchone()[0]
    print(f'\n{label} tolerance: {total} barycentric freqs in 2+ ON scans ({time.time()-t0:.1f}s)')
    for m in matches[:5]:
        print(f'  {m["bf"]:.5f} MHz: {m["n_scans"]} scans [{m["scans"]}] SNR={m["max_snr"]:.1f} drift={m["avg_drift"]:.4f}')

# Also check 3+ scans
for label, decimals in [('~10 Hz', 5), ('~100 Hz', 4), ('~1 kHz', 3)]:
    round_expr = 'bf_rounded' if decimals == 5 else f'ROUND(barycentric_freq, {decimals})'
    c.execute(f"""
        SELECT COUNT(*) FROM (
            SELECT {round_expr} as bf, COUNT(DISTINCT scan_id) as n_scans
            FROM on_bc
            GROUP BY {round_expr}
            HAVING n_scans >= 3
        )
    """)
    total = c.fetchone()[0]
    print(f'\n{label} tolerance: {total} barycentric freqs in 3+ ON scans')

c.execute('DROP TABLE IF EXISTS on_bc')
conn.commit()
conn.close()
print('\nDone.')
