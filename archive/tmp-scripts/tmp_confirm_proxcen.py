"""Confirm the full PROXCEN state: stack jobs, two-layer jobs, rejection funnel."""
import glob
import json
import sqlite3

conn = sqlite3.connect(r'G:\seti\data\seti_hits.db')
conn.row_factory = sqlite3.Row

print('=== stack_jobs ===')
for r in conn.execute('SELECT * FROM stack_jobs ORDER BY id DESC LIMIT 6'):
    d = dict(r)
    keep = {k: d.get(k) for k in ('id', 'target', 'status', 'n_epochs',
                                  'freq_center_mhz', 'width_mhz', 'n_sigma',
                                  'n_peaks', 'created_at')}
    print(keep)

print()
print('=== two_layer_jobs ===')
try:
    for r in conn.execute('SELECT * FROM two_layer_jobs ORDER BY id DESC LIMIT 6'):
        d = dict(r)
        keep = {k: d.get(k) for k in list(d)[:10]}
        print(keep)
except Exception as e:
    print('err:', e)

print()
print('=== rejection funnel per scan ===')
for sid in ['PROXCEN_2026-08-07_1911', 'PROXCEN_2026-08-08_2333',
            'PROXCEN_2026-08-10_0936', 'PROXCEN_2026-08-11_2122',
            'PROXCEN_2026-08-13_1139', 'PROXCEN_2026-08-14_2149']:
    found = glob.glob(rf'G:\seti\results\{sid}\**\rejection_results.json',
                      recursive=True)
    if found:
        with open(found[0]) as f:
            d = json.load(f)
        keep = {k: d[k] for k in d if not isinstance(d[k], (list, dict))}
        print(f'{sid}: {keep}')
    else:
        print(f'{sid}: no rejection file')

print()
print('=== raw hit totals ===')
row = conn.execute("""
    SELECT COUNT(*) c, SUM(CASE WHEN on_off='ON' THEN 1 ELSE 0 END) on_h,
           SUM(CASE WHEN on_off='OFF' THEN 1 ELSE 0 END) off_h
    FROM hits WHERE scan_id LIKE '%PROXCEN%'""").fetchone()
print(f'total hits={row["c"]:,} ON={row["on_h"]:,} OFF={row["off_h"]:,}')
snr8 = conn.execute("""
    SELECT COUNT(*) c FROM hits WHERE scan_id LIKE '%PROXCEN%'
    AND on_off='ON' AND snr >= 8""").fetchone()
print(f'ON hits SNR>=8: {snr8["c"]:,}')
