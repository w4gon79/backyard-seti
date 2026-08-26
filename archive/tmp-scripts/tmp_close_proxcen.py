"""Close out the PROXCEN funnel: correct remaining scans, run 6-epoch
cross-epoch search, collect rejection numbers for the Reddit draft."""
import glob
import json
import time
import urllib.request

BASE = 'http://localhost:8070'
RA_H, DEC = 14.4966, -62.6795   # Proxima Centauri (registry values)

def post(path, obj, timeout=1800):
    req = urllib.request.Request(BASE + path,
                                 data=json.dumps(obj).encode(),
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)

TO_CORRECT = ['PROXCEN_2026-08-11_2122',
              'PROXCEN_2026-08-13_1139',
              'PROXCEN_2026-08-14_2149']
ALL6 = ['PROXCEN_2026-08-07_1911',
        'PROXCEN_2026-08-08_2333',
        'PROXCEN_2026-08-10_0936',
        'PROXCEN_2026-08-11_2122',
        'PROXCEN_2026-08-13_1139',
        'PROXCEN_2026-08-14_2149']

for sid in TO_CORRECT:
    t0 = time.time()
    try:
        r = post('/api/barycentric/correct',
                 {'scan_id': sid, 'ra_hours': RA_H, 'dec_deg': DEC,
                  'telescope': 'parkes'}, timeout=600)
        print(f'correct {sid}: OK {time.time()-t0:.0f}s '
              f'({json.dumps(r)[:160]})', flush=True)
    except Exception as e:
        print(f'correct {sid}: FAILED {e}', flush=True)

print('--- cross-epoch (6 scans, 10 Hz, min 2 epochs, SNR 10) ---', flush=True)
t0 = time.time()
try:
    r = post('/api/barycentric/cross-epoch',
             {'scan_ids': ALL6, 'freq_tolerance_hz': 10,
              'min_epochs': 2, 'min_snr': 10, 'force_rerun': True})
    print(f'cross-epoch done in {time.time()-t0:.0f}s', flush=True)
    cands = r.get('candidates', r if isinstance(r, list) else [])
    print(f'candidates: {len(cands)}', flush=True)
    for c in cands[:20]:
        print('  ', json.dumps(c)[:200], flush=True)
    print('keys:', list(r.keys()) if isinstance(r, dict) else 'list', flush=True)
    if isinstance(r, dict):
        for k in ('n_candidates', 'summary', 'total_candidates', 'stats'):
            if k in r:
                print(k, '=', json.dumps(r[k])[:300], flush=True)
except Exception as e:
    print(f'cross-epoch FAILED: {e}', flush=True)

print('--- rejection results on disk (funnel stage 2) ---', flush=True)
for sid in ALL6:
    pats = [rf'G:\\seti\\results\\{sid}\\rejection\\rejection_results.json',
            rf'G:\\seti\\results\\{sid}\\**\\rejection_results.json']
    found = []
    for p in pats:
        found.extend(glob.glob(p, recursive=True))
    if found:
        with open(found[0]) as f:
            d = json.load(f)
        keep = {k: d[k] for k in d if not isinstance(d[k], (list, dict))}
        print(f'{sid}: {keep}', flush=True)
    else:
        print(f'{sid}: no rejection file', flush=True)
print('DONE', flush=True)
