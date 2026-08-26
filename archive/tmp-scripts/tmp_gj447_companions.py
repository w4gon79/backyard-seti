"""Proximity companion discovery for GJ447 (Ross 128):
find which BL targets were observed in the same GBT sessions (MJD 57532,
57695) by querying the nearest catalog targets by sky distance."""
import json
import math
import sqlite3
import sys
import urllib.request
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, r'G:\seti\src')
from bl_catalog import _exact_target  # noqa: E402  (pattern reuse)

BL_API = 'https://seti.berkeley.edu/opendata/api'
_UA = {'User-Agent': 'BackyardSETI/1.0'}
import re
GBT_PAT = re.compile(
    r'(?:spliced_)?blc\d+_guppi_(\d+)_(\d+)_([A-Za-z0-9+\-.]+?)_(\d+)'
    r'\.(rawspec|gpuspec)\.(\d+)\.h5$')

TARGET = 'GJ447'
SESSION_MJDS = {'57532', '57695'}

# Ross 128 (from registry/BL catalog fallback)
RA_DEG, DEC_DEG = 176.9844, 0.8044

conn = sqlite3.connect(r'G:\seti\data\seti_hits.db')
conn.row_factory = sqlite3.Row
rows = conn.execute("""
    SELECT target, ra_hours, dec, telescopes, n_fine FROM bl_catalog
    WHERE ra_hours IS NOT NULL AND dec IS NOT NULL
      AND telescopes LIKE '%GBT%' AND n_fine > 0
""").fetchall()

def dist_deg(ra_h, dec):
    ra1, d1 = math.radians(ra_h * 15), math.radians(dec)
    ra2, d2 = math.radians(RA_DEG), math.radians(DEC_DEG)
    cosv = math.sin(d1) * math.sin(d2) + math.cos(d1) * math.cos(d2) * math.cos(ra1 - ra2)
    return math.degrees(math.acos(max(-1, min(1, cosv))))

cands = []
for r in rows:
    if r['target'].upper() == TARGET:
        continue
    d = dist_deg(r['ra_hours'], r['dec'])
    cands.append((d, r['target']))
cands.sort()
cands = cands[:16]
print(f'nearest GBT catalog targets to Ross 128:')
for d, t in cands:
    print(f'  {d:6.2f} deg  {t}')

def query(target):
    url = BL_API + '/query-files?target=' + urllib.parse.quote(target)
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.load(r)
    files = data.get('data', [])
    hits = []
    for f in files:
        fn = (f.get('url') or '').split('/')[-1]
        m = GBT_PAT.search(fn)
        if m and m.group(1) in SESSION_MJDS:
            hits.append((m.group(1), int(m.group(2)), m.group(3), m.group(5), m.group(6)))
    return target, hits

print()
print(f'querying {len(cands)} nearest targets for sessions {sorted(SESSION_MJDS)}...')
with ThreadPoolExecutor(max_workers=4) as ex:
    for target, hits in ex.map(query, [t for _, t in cands]):
        if hits:
            mjds = sorted({h[0] for h in hits})
            seqs = sorted(h[1] for h in hits)
            fine = [h for h in hits if h[4] == '0000']
            print(f'COMPANION: {target}: {len(hits)} session files '
                  f'(MJD {",".join(mjds)}), seq {min(seqs)}..{max(seqs)}, '
                  f'{len(fine)} fine')
        else:
            print(f'  {target}: none in those sessions')
