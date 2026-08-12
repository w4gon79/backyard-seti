"""Check ON/OFF frequency matches - fast temp table approach."""
import sqlite3
import time

DB = r'G:\seti\data\seti_hits.db'
conn = sqlite3.connect(DB)
c = conn.cursor()

# Schema
c.execute('PRAGMA table_info(hits)')
cols = [r[1] for r in c.fetchall()]
print('Columns:', cols)

# on_off values
c.execute('SELECT on_off, COUNT(*) FROM hits GROUP BY on_off')
print('\non_off counts:')
for val, cnt in c.fetchall():
    print(f'  {val}: {cnt}')

# Total
c.execute('SELECT COUNT(*) FROM hits')
print(f'\nTotal hits: {c.fetchone()[0]}')

# Build temp tables of rounded frequencies
print('\nBuilding temp tables...')
t0 = time.time()
c.execute('DROP TABLE IF EXISTS on_freqs')
c.execute('DROP TABLE IF EXISTS off_freqs')
c.execute('CREATE TEMP TABLE on_freqs AS SELECT DISTINCT ROUND(freq, 3) AS f FROM hits WHERE on_off LIKE "%ON%"')
c.execute('CREATE TEMP TABLE off_freqs AS SELECT DISTINCT ROUND(freq, 3) AS f FROM hits WHERE on_off LIKE "%OFF%"')
c.execute('CREATE INDEX IF NOT EXISTS idx_on_f ON on_freqs(f)')
c.execute('CREATE INDEX IF NOT EXISTS idx_off_f ON off_freqs(f)')
conn.commit()
print(f'  Done in {time.time()-t0:.1f}s')

# Count distinct shared frequencies
t0 = time.time()
c.execute('SELECT COUNT(*) FROM on_freqs INTERSECT SELECT COUNT(*) FROM off_freqs')
# Actually do the intersect properly
c.execute('SELECT COUNT(*) FROM (SELECT f FROM on_freqs INTERSECT SELECT f FROM off_freqs)')
shared = c.fetchone()[0]
print(f'Distinct frequencies in both ON+OFF: {shared} ({time.time()-t0:.1f}s)')

# ON hits at shared frequencies = confirmed RFI
t0 = time.time()
c.execute("""
    SELECT COUNT(*) FROM hits h
    WHERE h.on_off LIKE '%ON%'
    AND EXISTS (SELECT 1 FROM off_freqs o WHERE o.f = ROUND(h.freq, 3))
""")
on_rfi = c.fetchone()[0]
print(f'ON hits at RFI frequencies: {on_rfi} ({time.time()-t0:.1f}s)')

# OFF hits at shared frequencies
t0 = time.time()
c.execute("""
    SELECT COUNT(*) FROM hits h
    WHERE h.on_off LIKE '%OFF%'
    AND EXISTS (SELECT 1 FROM on_freqs o WHERE o.f = ROUND(h.freq, 3))
""")
off_rfi = c.fetchone()[0]
print(f'OFF hits at RFI frequencies: {off_rfi} ({time.time()-t0:.1f}s)')

# Clean candidates: ON-only, SNR >= 8, freq NOT in shared set
t0 = time.time()
c.execute("""
    SELECT COUNT(*) FROM hits h
    WHERE h.on_off LIKE '%ON%'
    AND h.snr >= 8
    AND NOT EXISTS (SELECT 1 FROM off_freqs o WHERE o.f = ROUND(h.freq, 3))
""")
clean_cands = c.fetchone()[0]
print(f'Clean candidates (ON-only, SNR>=8): {clean_cands} ({time.time()-t0:.1f}s)')

# Also count ON-only with SNR >= 5 for comparison
t0 = time.time()
c.execute("""
    SELECT COUNT(*) FROM hits h
    WHERE h.on_off LIKE '%ON%'
    AND h.snr >= 5
    AND NOT EXISTS (SELECT 1 FROM off_freqs o WHERE o.f = ROUND(h.freq, 3))
""")
on_only_5 = c.fetchone()[0]
print(f'ON-only SNR>=5 (all candidates): {on_only_5} ({time.time()-t0:.1f}s)')

# SNR distribution
c.execute('SELECT MIN(snr), MAX(snr), AVG(snr) FROM hits')
snr_min, snr_max, snr_avg = c.fetchone()
print(f'\nSNR range: {snr_min:.1f} - {snr_max:.1f}, avg {snr_avg:.1f}')

for threshold in [5, 6, 7, 8, 10, 15, 20]:
    c.execute('SELECT COUNT(*) FROM hits WHERE snr >= ?', (threshold,))
    print(f'  SNR >= {threshold}: {c.fetchone()[0]}')

# Cleanup
c.execute('DROP TABLE IF EXISTS on_freqs')
c.execute('DROP TABLE IF EXISTS off_freqs')
conn.commit()
conn.close()
print('\nDone.')
