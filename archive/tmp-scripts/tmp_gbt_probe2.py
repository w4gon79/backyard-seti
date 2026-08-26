"""Probe GBT file structure: handle dict response, group by MJD, find session companions."""
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
    with urllib.request.urlopen(req, timeout=30) as r:
        resp = json.load(r)
    if isinstance(resp, dict):
        # response keyed by target name (or similar)
        for v in resp.values():
            if isinstance(v, list):
                return v
        return []
    return resp


def parse_gbt(name):
    # guppi_MJD_SEQ_TARGET_0002.0000.h5 style
    base = name.replace('.h5', '')
    parts = base.split('_')
    if len(parts) >= 4 and parts[0].endswith('guppi'):
        return parts[1], int(parts[2]), parts[3]
    return None


files = query('HIP2')
print(f"HIP2 GBT HDF5 files: {len(files)}")
if files:
    print("sample:", json.dumps(files[0])[:200])

by_mjd = defaultdict(list)
for f in files:
    name = f.get('url', '').rsplit('/', 1)[-1]
    p = parse_gbt(name)
    if p:
        by_mjd[p[0]].append((p[1], name, f.get('filesize', 0)))

print(f"distinct MJDs: {len(by_mjd)}")
for mjd in sorted(by_mjd, reverse=True)[:5]:
    scans = sorted(by_mjd[mjd])
    tot = sum(s[2] for s in scans) / 1e9
    print(f"\nMJD {mjd}: {len(scans)} HIP2 files, {tot:.1f} GB")
    for s in scans[:6]:
        print(f"   seq {s[0]:6d}  {s[1]}  {s[2]/1e9:.1f} GB")

# Companion probe: which other targets share the most recent session MJD?
if by_mjd:
    probe = sorted(by_mjd, reverse=True)[0]
    print(f"\n=== companion probe: HIP2 session MJD {probe} ===")
    candidates = ['HIP39', 'HIP42', 'HIP69', 'HIP1133', 'HIP5645', 'HIP57443',
                  'GJ1', 'LHS138', 'HIP15510', 'HIP22738', 'HIP24186', 'HIP2762',
                  'HIP29271', 'HIP27072', 'HIP47425']
    for c in candidates:
        try:
            cf = query(c)
        except Exception as e:
            print(f"{c}: error {e}")
            continue
        hits = sorted((parse_gbt(f.get('url', '').rsplit('/', 1)[-1]), f.get('filesize', 0))
                      for f in cf
                      if parse_gbt(f.get('url', '').rsplit('/', 1)[-1]) and
                         parse_gbt(f.get('url', '').rsplit('/', 1)[-1])[0] == probe)
        if hits:
            names = {h[0][2] for h in hits}
            tot = sum(h[1] for h in hits) / 1e9
            seqs = [h[0][1] for h in hits]
            print(f"{c} ({','.join(names)}): {len(hits)} files, seq {min(seqs)}..{max(seqs)}, {tot:.1f} GB")
        else:
            print(f"{c}: none")
