"""PROXCEN post-readiness: correction state + rejection numbers + what's missing."""
import sqlite3

conn = sqlite3.connect(r'G:\seti\data\seti_hits.db')
conn.row_factory = sqlite3.Row

print('=== scans table columns ===')
cols = [r[1] for r in conn.execute('PRAGMA table_info(scans)')]
print(cols)

print()
print('=== PROXCEN scan states ===')
rows = conn.execute("""
    SELECT * FROM scans WHERE scan_id LIKE '%PROXCEN%' ORDER BY scan_id""").fetchall()
for r in rows:
    d = dict(r)
    keep = {k: d.get(k) for k in d if k in
            ('scan_id', 'status', 'bary_status', 'bary_corrected', 'corrected',
             'mjd', 'target', 'n_hits', 'created_at')}
    print(keep)
