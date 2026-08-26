"""Probe GBT file structure for a target: group by MJD, look for same-session companions."""
import json
import sys
import urllib.request
import urllib.parse
from collections import defaultdict

sys.path.insert(0, 'G:/seti/src')
BL_API = 'https://seti.berkeley.edu/opendata/api'
_UA = {'User-Agent': 'Mozilla/5.0 (seti-pipeline)'}


def query(target, telescope='GBT', ftype='HDF5'):
    url = BL_API + '/query-files?target=' + urllib.parse.quote(target) + \
          '&telescope=' + telescope + '&file-type=' + urllib.parse.quote(ftype)
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


# 1) HIP2 at GBT
files = query('HIP2')
print(f"HIP2 GBT HDF5 files returned: {len(files)}")
if files:
    print("sample:", json.dumps(files[0], indent=1)[:300])

by_mjd = defaultdict(list)
for f in files:
    name = f.get('url', '').rsplit('/', 1)[-1]
    parts = name.replace('.h5', '').split('_')
    # guppi_MJD_SEQ_TARGET_...
    if len(parts) >= 4 and parts[0] == 'guppi':
        by_mjd[parts[1]].append((int(parts[2]), name, f.get('filesize', 0)))

print(f"\ndistinct MJDs: {len(by_mjd)}")
for mjd in sorted(by_mjd, reverse=True)[:5]:
    scans = sorted(by_mjd[mjd])
    seqs = [s[0] for s in scans]
    tot = sum(s[2] for s in scans) / 1e9
    print(f"MJD {mjd}: {len(scans)} files, seq {min(seqs)}..{max(seqs)}, {tot:.1f} GB")
    for s in scans[:8]:
        print(f"   seq {s[0]:6d}  {s[1]}")

# 2) Companion probe: check which other GBT targets share a session MJD
probe_mjd = sorted(by_mjd, reverse=True)[0] if by_mjd else None
if probe_mjd:
    print(f"\n=== companion probe for HIP2 MJD {probe_mjd} ===")
    candidates = ['HIP39', 'HIP42', 'HIP69', 'HIP1133', 'HIP5645', 'HIP57443',
                  'GJ1', 'LHS138', 'HIP15510', 'HIP22738', 'HIP24186', 'HIP2762']
    for c in candidates:
        try:
            cf = query(c)
        except Exception as e:
            print(f"{c}: error {e}")
            continue
        hits = []
        for f in cf:
            name = f.get('url', '').rsplit('/', 1)[-1]
            parts = name.replace('.h5', '').split('_')
            if len(parts) >= 4 and parts[0] == 'guppi' and parts[1] == probe_mjd:
                hits.append((int(parts[2]), name))
        if hits:
            print(f"{c}: {len(hits)} files in MJD {probe_mjd}")
            for h in sorted(hits):
                print(f"   seq {h[0]:6d}  {h[1]}")
        else:
            print(f"{c}: no files in that MJD")
