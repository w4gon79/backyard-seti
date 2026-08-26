"""Fixed probe: exact-name filter, session interleave, sizes, product types."""
import json
import re
import urllib.request
import urllib.parse
from collections import defaultdict

BL_API = 'https://seti.berkeley.edu/opendata/api'
_UA = {'User-Agent': 'BackyardSETI/1.0'}

# blc00_guppi_MJD_SEQ_TARGET_SCAN.rawspec.0000.h5
PAT = re.compile(r'(?:spliced_)?blc\d+_guppi_(\d+)_(\d+)_([A-Za-z0-9+\-.]+?)_(\d+)\.'
                 r'(rawspec|gpuspec)\.(\d+)\.h5$')


def query(target, telescope='GBT', ftype='HDF5'):
    url = BL_API + '/query-files?target=' + urllib.parse.quote(target) + \
          '&telescope=' + telescope + '&file-type=' + urllib.parse.quote(ftype)
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        resp = json.load(r)
    if isinstance(resp, dict):
        for v in resp.values():
            if isinstance(v, list):
                return v
    return resp


files = query('HIP2')
rows = []
for f in files:
    m = PAT.search(f.get('url', '').rsplit('/', 1)[-1])
    if not m:
        continue
    sz = f.get('size') or f.get('filesize') or 0
    rows.append({'mjd': int(m.group(1)), 'seq': int(m.group(2)),
                 'target': m.group(3), 'scan': m.group(4),
                 'prod': m.group(5), 'res': m.group(6),
                 'size': sz, 'cf': f.get('center_freq', 0)})

print(f"parsed {len(rows)} GBT guppi files from HIP2* response")
by_t = defaultdict(list)
for r in rows:
    by_t[r['target']].append(r)

exact = [t for t in by_t if t.upper() == 'HIP2']
print(f"exact 'HIP2' match present: {bool(exact)}")
print(f"top targets by file count (prefix pollution):")
for t, v in sorted(by_t.items(), key=lambda kv: -len(kv[1]))[:10]:
    sess = sorted({r['mjd'] for r in v})
    tot = sum(r['size'] for r in v) / 1e9
    print(f"  {t:14s} files={len(v):5d} sessions={len(sess):3d} {tot:8.1f} GB")

# Session interleave evidence: pick one session with several targets
by_sess = defaultdict(list)
for r in rows:
    by_sess[r['mjd']].append(r)
best = max(by_sess, key=lambda m: len({r['target'] for r in by_sess[m]}))
mix = [r for r in by_sess[best] if r['prod'] == 'rawspec' and r['res'] == '0000']
seen, order = set(), []
for r in sorted(mix, key=lambda x: x['seq']):
    k = (r['target'], r['scan'])
    if k not in seen:
        seen.add(k)
        order.append(r)
print(f"\n=== MJD {best} SEQ interleave (rawspec.0000 only) ===")
for r in order[:20]:
    print(f"  seq {r['seq']:6d}  {r['target']:12s} scan {r['scan']}  cf={r['cf']:.0f}MHz")

# What does ONE target look like: pick the busiest, count per-session files + sizes
big = max(by_t, key=lambda t: len(by_t[t]))
t_rows = by_t[big]
sess_count = defaultdict(list)
for r in t_rows:
    sess_count[r['mjd']].append(r)
mjd = max(sess_count, key=lambda m: len(sess_count[m]))
one = sess_count[mjd]
print(f"\n=== {big} busiest session MJD {mjd}: {len(one)} files ===")
prods = defaultdict(int)
for r in one:
    prods[f"{r['prod']}.{r['res']}"] += 1
for k, v in sorted(prods.items()):
    print(f"  {k}: {v} files")
cf = sorted({r['cf'] for r in one if r['res'] == '0000'})
print(f"  rawspec.0000 center freqs: {[f'{c:.0f}' for c in cf]}")
tot = sum(r['size'] for r in one if r['res'] == '0000') / 1e9
print(f"  rawspec.0000 total: {tot:.1f} GB")
