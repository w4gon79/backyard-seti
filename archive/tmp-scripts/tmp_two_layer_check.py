"""Two-layer job results: did the 2-epoch coincidences survive OFF veto?"""
import sqlite3

conn = sqlite3.connect(r'G:\seti\data\seti_hits.db')
conn.row_factory = sqlite3.Row
cols = [r[1] for r in conn.execute('PRAGMA table_info(two_layer_jobs)')]
print('cols:', cols)
for r in conn.execute('SELECT * FROM two_layer_jobs WHERE id IN (6, 9)'):
    d = dict(r)
    for k, v in d.items():
        if isinstance(v, str) and len(v) > 120:
            v = v[:120] + '...'
        print(f'  {k} = {v}')
    print('---')
