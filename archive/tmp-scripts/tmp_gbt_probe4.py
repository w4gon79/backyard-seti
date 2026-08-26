"""Final GBT probe: exact-name filtering, session composition, product types, real sizes."""
import json
import sys
import urllib.request
import urllib.parse
from collections import defaultdict

BL_API = 'https://seti.berkeley.edu/opendata/api'
_UA = {'User-Agent': 'BackyardSETI/1.0'}


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
        return []
    return resp


files = query('HIP2')
print(f"HIP2 query returned: {len(files)} files")

# exact-name filter: the target token in the filename
def target_token(fn):
    # blc00_guppi_59433_42615_HIP29806_0041.rawspec.0000.h5
    parts = fn.split('_')
    # find 'guppi' index, target is the 2 tokens after seq (target + scan)
    try:
        i = parts.index('guppi') if 'guppi' in parts else \
            next(j for j, p in enumerate(parts) if 'guppi' in p)
        # node_guppi_MJD_SEQ_TARGET_SCAN
        toks = parts[i + 3:-1]  # strip trailing product tokens
        return '_'.join(t for t in toks if 'guppi' not in t and not t.startswith('rawspec')
                        and not t.startswith('gpuspec') and not t.endswith('.h5'))
    except (StopIteration, ValueError):
        return None

exact = defaultdict(list)
for f in files:
    fn = f.get('url', '').rsplit('/', 1)[-1]
    tt = target_token(fn)
    exact[tt].append(f)

print(f"\ndistinct targets in HIP2* response: {len(exact)}")
for name in sorted(exact, key=lambda n: -len(exact[n]))[:8]:
    sess = {int(f['mjd']) for f in exact[name]}
    tot = sum(f.get('size') or 0 for f in exact[name]) / 1e9
    print(f"  {name:12s} files={len(exact[name]):5d} sessions={len(sess):3d} size={tot:8.1f} GB")

# session composition for one target
probe_t = 'HIP29806'
if probe_t in exact:
    by_sess = defaultdict(list)
    for f in exact[probe_t]:
        by_sess[int(f['mjd'])].append(f)
    mjd = max(by_sess)
    sess = sorted(by_sess[mjd], key=lambda x: x['url'])
    print(f"\n=== {probe_t} session MJD {mjd}: {len(sess)} files ===")
    for f in sess:
        fn = f['url'].rsplit('/', 1)[-1]
        sz = (f.get('size') or 0) / 1e9
        print(f"  {fn}  {sz:.2f} GB")

# SEQ interleaving across targets in that session (ABACAD evidence)
all_sess = defaultdict(list)
for f in files:
    all_sess[int(f['mjd'])].append(f)
mjd = max(by_sess) if probe_t in exact else max(all_sess)
mix = sorted(all_sess[mjd], key=lambda x: x['url'])
print(f"\n=== ALL targets in HIP2* response, MJD {mjd}, sorted by SEQ ===")
def seq_of(f):
    fn = f['url'].rsplit('/', 1)[-1]
    parts = fn.split('_')
    try:
        i = next(j for j, p in enumerate(parts) if 'guppi' in p)
        return int(parts[i + 2])
    except (StopIteration, ValueError):
        return 0
seen = set()
for f in sorted(mix, key=seq_of):
    fn = f['url'].rsplit('/', 1)[-1]
    tt = target_token(fn)
    key = (tt, seq_of(f))
    if 'spliced' not in fn and key not in seen:
        seen.add(key)
        print(f"  seq {seq_of(f):6d}  {tt}")
