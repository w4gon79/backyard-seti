#!/usr/bin/env python3
"""Search for BLC1 Proxima Centauri files from April 2019 (MJD 58551-58558)."""
import urllib.request, json

API = "http://seti.berkeley.edu/opendata/api"

# BLC1 was detected April 29, 2019 = MJD 58551
# The follow-up observations were MJD 58551-58558
# Try querying with different parameters to get files from that date range

# First try: query-files might accept date params
test_urls = [
    f"{API}/query-files?target=PROXCEN&telescope=Parkes&file-type=filterbank",
    f"{API}/query-files?target=PROXCEN&telescope=Parkes",
    f"{API}/query-files?target=PROXCEN",
]

all_mjds = set()
all_files = []

for url in test_urls:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
        files = data if isinstance(data, list) else data.get("data", [])
        print(f"Query: {url}")
        print(f"  Got {len(files)} files")

        for f in files:
            u = f.get("url", "")
            if not u:
                continue
            fname = u.split("/")[-1]
            parts = fname.replace(".h5", "").split("_")
            if len(parts) >= 2:
                try:
                    mjd = int(parts[1])
                    all_mjds.add(mjd)
                    if mjd >= 58540 and mjd <= 58570:
                        all_files.append((fname, u, f.get("filesize", 0)))
                except ValueError:
                    pass
        print(f"  Unique MJDs so far: {sorted(all_mjds)}")
        print()
    except Exception as e:
        print(f"  Error: {e}")

print(f"\nAll MJDs found: min={min(all_mjds) if all_mjds else 'none'}, max={max(all_mjds) if all_mjds else 'none'}")
print(f"Total unique MJDs: {len(all_mjds)}")

if all_files:
    print(f"\n=== BLC1-era files (MJD 58540-58570) ===")
    for fname, url, size in sorted(all_files):
        print(f"  {fname} ({size/1e9:.1f} GB)")
        print(f"    {url}")
else:
    print("\nNo files found from BLC1 era (MJD 58540-58570)")
    print("The BLC1 data may be in a separate archive or under a different target name.")
    print(f"Highest MJD available: {max(all_mjds) if all_mjds else 'none'}")
    print(f"MJD 58551 = April 29, 2019 (BLC1 detection date)")

    # Try alternate target names
    print("\n=== Trying alternate target names ===")
    alt_names = ["PROXIMA", "PROXIMACEN", "PROXIMA_CEN", "GLIESE551", "GJ551", "HIP70890",
                 "ALPHA_CEN_C", "ALPHACENC"]
    for name in alt_names:
        url = f"{API}/query-files?target={name}&telescope=Parkes"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
            files = data if isinstance(data, list) else data.get("data", [])
            if files:
                print(f"  {name}: {len(files)} files!")
        except:
            pass
