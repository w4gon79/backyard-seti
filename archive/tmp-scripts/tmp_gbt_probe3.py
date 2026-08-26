"""Fix probe: use API mjd field, dump full filenames/fields for HIP2 GBT."""
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
print(f"HIP2 GBT files: {len(files)}")
print("fields:", sorted(files[0].keys()))
for f in files[:6]:
    print(f"  url={f.get('url','?').rsplit('/',1)[-1]}  mjd={f.get('mjd')}  "
          f"cf={f.get('center_freq')}  ra={f.get('ra')}  dec={f.get('dec')}  "
          f"size={f.get('filesize',0)/1e9:.2f}GB")

# group by integer session MJD
by_sess = defaultdict(list)
for f in files:
    key = int(f.get('mjd', 0))
    by_sess[key].append(f)

print(f"\ndistinct session MJDs: {len(by_sess)}")
for mjd in sorted(by_sess, reverse=True)[:4]:
    scans = by_sess[mjd]
    tot = sum(f.get('filesize', 0) for f in scans) / 1e9
    cfs = sorted({round(f.get('center_freq', 0)) for f in scans})
    print(f"MJD {mjd}: {len(scans)} files, {tot:.1f} GB, center freqs: {cfs[:8]}")
    # file name grammar
    for f in sorted(scans, key=lambda x: x.get('mjd', 0))[:5]:
        print(f"   {f.get('url','?').rsplit('/',1)[-1]}")
