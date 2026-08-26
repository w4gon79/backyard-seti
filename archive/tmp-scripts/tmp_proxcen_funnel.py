"""PROXCEN funnel numbers from the DB + check which PROXCEN epochs exist in the BL archive."""
import json
import sqlite3
import urllib.request
import urllib.parse

conn = sqlite3.connect(r'G:\seti\data\seti_hits.db')
print('=== PROXCEN scans in DB ===')
rows = conn.execute("""
    SELECT s.scan_id, s.status, COUNT(h.id) AS hits,
           SUM(CASE WHEN h.on_off='ON' THEN 1 ELSE 0 END) AS on_h,
           SUM(CASE WHEN h.on_off='OFF' THEN 1 ELSE 0 END) AS off_h
    FROM scans s LEFT JOIN hits h ON h.scan_id = s.scan_id
    WHERE s.scan_id LIKE '%PROXCEN%'
    GROUP BY s.scan_id ORDER BY s.scan_id""").fetchall()
for r in rows:
    print(f'{r[0]:40s} {str(r[1]):10s} hits={r[2] or 0:7d} on={r[3] or 0} off={r[4] or 0}')

print()
print('=== rejection / cross-epoch tables ===')
try:
    rej = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    print('tables:', [t[0] for t in rej])
except Exception as e:
    print(e)

try:
    ce = conn.execute("SELECT COUNT(*) FROM cross_epoch_results").fetchone()
    print('cross_epoch_results rows:', ce[0])
    runs = conn.execute("SELECT DISTINCT created_at FROM cross_epoch_results ORDER BY created_at DESC LIMIT 5").fetchall()
    print('recent runs:', [r[0] for r in runs])
except Exception as e:
    print('ce:', e)

print()
print('=== PROXCEN fine epochs in BL archive (distinct MJDs) ===')
url = 'https://seti.berkeley.edu/opendata/api/query-files?target=' + urllib.parse.quote('PROXCEN')
req = urllib.request.Request(url, headers={'User-Agent': 'BackyardSETI/1.0'})
with urllib.request.urlopen(req, timeout=60) as r:
    data = json.loads(r.read().decode())
files = data.get('data', [])
mjds = {}
for f in files:
    fn = (f.get('url') or '').split('/')[-1]
    if '_S_fine' in fn or '_R_fine' in fn:
        mjd = fn.split('_')[1]
        mjds.setdefault(mjd, 0)
        mjds[mjd] += 1
for mjd in sorted(mjds):
    yr = 1858.0 + int(mjd) / 365.25
    print(f'MJD {mjd}: {mjds[mjd]} fine files  (~{int(yr)})')
print('total fine epochs on archive:', len(mjds))
