#!/usr/bin/env python3
"""Download one fine-res PROXCEN cadence for testing."""
import urllib.request, os

# MJD 57791: 3 ON + 3 OFF, fine-res only, ~12.4 GB each
FILES = [
    "http://blpd8.ssl.berkeley.edu/dl/Parkes_57791_72989_PROXCEN_S_fine.h5",
    "http://blpd8.ssl.berkeley.edu/dl/Parkes_57791_73331_PROXCEN_R_fine.h5",
    "http://blpd8.ssl.berkeley.edu/dl/Parkes_57791_73670_PROXCEN_S_fine.h5",
    "http://blpd8.ssl.berkeley.edu/dl/Parkes_57791_74011_PROXCEN_R_fine.h5",
    "http://blpd8.ssl.berkeley.edu/dl/Parkes_57791_74349_PROXCEN_S_fine.h5",
    "http://blpd8.ssl.berkeley.edu/dl/Parkes_57791_74689_PROXCEN_R_fine.h5",
]

OUT_DIR = "G:\\seti\\data\\fine"
os.makedirs(OUT_DIR, exist_ok=True)

total_gb = 0
for url in FILES:
    fname = url.split("/")[-1]
    dest = os.path.join(OUT_DIR, fname)
    if os.path.exists(dest):
        print(f"SKIP (exists): {fname}")
        continue
    # Get file size first
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req) as resp:
        size = int(resp.headers.get("Content-Length", 0))
    gb = size / 1e9
    total_gb += gb
    print(f"Downloading: {fname} ({gb:.1f} GB)")

    urllib.request.urlretrieve(url, dest)
    actual = os.path.getsize(dest) / 1e9
    print(f"  Done: {actual:.1f} GB")

print(f"\nTotal downloaded: {total_gb:.1f} GB")
print("Done.")
