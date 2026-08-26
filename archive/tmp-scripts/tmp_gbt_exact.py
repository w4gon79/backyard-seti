"""Exact-name counts for HIP2 and GJ1 (prefix-pollution check)."""
import json
import re
import urllib.request
import urllib.parse
from collections import defaultdict

BL_API = 'https://seti.berkeley.edu/opendata/api'
_UA = {'User-Agent': 'BackyardSETI/1.0'}
PAT = re.compile(r'(?:spliced_)?blc\d+_guppi_(\d+)_(\d+)_([A-Za-z0-9+\-.]+?)_(\d+)\.'
                 r'(rawspec|gpuspec)\.(\d+)\.h5$')


def query(target):
    url = BL_API + '/query-files?target=' + urllib.parse.quote(target) + \
          '&telescope=GBT&file-type=HDF5'
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        resp = json.load(r)
    if isinstance(resp, dict):
        for v in resp.values():
            if isinstance(v, list):
                return v
    return resp


for name in ['HIP2', 'GJ1']:
    files = query(name)
    by_t = defaultdict(set)   # target -> session MJDs (fine products only)
    nfiles = defaultdict(int)
    for f in files:
        m = PAT.search(f.get('url', '').rsplit('/', 1)[-1])
        if m and m.group(5) in ('rawspec', 'gpuspec') and m.group(6) == '0000':
            by_t[m.group(3)].add(m.group(1))
            nfiles[m.group(3)] += 1
    fam = sorted(by_t, key=lambda t: -nfiles[t])
    print(f"\n{name}: response={len(files)} files, {len(by_t)} distinct exact targets")
    for t in fam[:6]:
        print(f"   {t:12s} fine0000 files={nfiles[t]:4d} sessions={len(by_t[t]):3d}")
    exact = nfiles.get(name, 0)
    print(f"   EXACT {name}: {exact} fine files across {len(by_t.get(name, set()))} sessions")
