#!/usr/bin/env python3
"""Find PROXCEN observation sessions with proper ON/OFF cadence."""
import urllib.request, json
from collections import defaultdict

API = "http://seti.berkeley.edu/opendata/api"

def api_get(path):
    url = f"{API}/{path}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())

data = api_get("query-files?target=PROXCEN")
files = data["data"]
print(f"Total PROXCEN files: {len(files)}")

# Group by MJD day and resolution
sessions = defaultdict(lambda: {"S_mid": [], "R_mid": [], "S_fine": [], "R_fine": []})
for f in files:
    url = f.get("url", "")
    fname = url.split("/")[-1]
    if not fname.endswith(".h5"):
        continue
    parts = fname.replace(".h5", "").split("_")
    if len(parts) < 6:
        continue
    mjd_day = parts[1]
    pos = parts[4]  # S or R
    res = parts[5]  # mid, fine, etc
    key = f"{pos}_{res}"
    if key in sessions[mjd_day]:
        sessions[mjd_day][key].append((fname, url, f.get("size", 0)))

print("\n=== Sessions with full mid-res ABABAB cadence (3+ ON, 3+ OFF) ===")
for mjd in sorted(sessions.keys()):
    s = sessions[mjd]
    if len(s["S_mid"]) >= 3 and len(s["R_mid"]) >= 3:
        total = sum(sz for _, _, sz in s["S_mid"] + s["R_mid"])
        print(f"\nMJD {mjd}: {len(s['S_mid'])} ON_mid, {len(s['R_mid'])} OFF_mid ({total/1e9:.1f} GB)")
        for fname, url, sz in sorted(s["S_mid"] + s["R_mid"]):
            tag = "ON " if "_S_" in fname else "OFF"
            print(f"  [{tag}] {fname} ({sz/1e6:.0f} MB)")
            print(f"        {url}")

print("\n=== Sessions with fine-res only (no mid-res) ===")
for mjd in sorted(sessions.keys()):
    s = sessions[mjd]
    if len(s["S_mid"]) == 0 and len(s["S_fine"]) >= 3 and len(s["R_fine"]) >= 3:
        total = sum(sz for _, _, sz in s["S_fine"] + s["R_fine"])
        print(f"  MJD {mjd}: {len(s['S_fine'])} ON, {len(s['R_fine'])} OFF ({total/1e9:.1f} GB)")

# Check what we already have
import os
existing = set()
if os.path.exists("data"):
    for f in os.listdir("data"):
        if f.endswith(".h5"):
            existing.add(f)
print(f"\n=== Already downloaded: {len(existing)} files ===")
for f in sorted(existing):
    print(f"  {f}")
