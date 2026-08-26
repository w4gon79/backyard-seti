"""Confirm Joel's quoted 6-scan cross-epoch numbers straight from the DB row."""
import json
import sqlite3

conn = sqlite3.connect(r'G:\seti\data\seti_hits.db')
conn.row_factory = sqlite3.Row
cols = [r[1] for r in conn.execute('PRAGMA table_info(cross_epoch_results)')]
print('cols:', cols)
rows = conn.execute('SELECT * FROM cross_epoch_results ORDER BY rowid DESC').fetchall()
for r in rows:
    d = dict(r)
    for k, v in d.items():
        s = str(v)
        print(f'  {k} = {s[:300]}{"..." if len(s) > 300 else ""}')
    print('---')
