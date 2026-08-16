#!/usr/bin/env python3
"""
bl_search.py - Search the Breakthrough Listen Open Data API.

Usage:
    python bl_search.py --target PROXCEN
    python bl_search.py --target PROXCEN --telescope Parkes --filetype filterbank
    python bl_search.py --find proxima          # fuzzy search target names
    python bl_search.py --target PROXCEN --cadence   # show on/off pairs grouped by session
    python bl_search.py --target PROXCEN --download data/  # download files to directory
"""

import argparse
import os
import re
import sys
import urllib.request
import json
from collections import defaultdict

API = "http://seti.berkeley.edu/opendata/api"
DOWNLOAD_BASE = "http://blpd8.ssl.berkeley.edu/dl/"

# Parkes grammar: Tel_MJD_SEQ_TARGET_[SR]_RES.h5 (position marker optional;
# bare forms exist: Parkes_57770_78921_ALPHACEN_fine.h5)
PARKES_PAT = re.compile(
    r"(Parkes|GBT|APF)_(\d+)_(\d+)_([A-Za-z0-9+\-.]+?)(?:_([SR]))?_"
    r"(fine|mid|time)\.h5$")
# GBT grammar: (spliced_)blcNN_guppi_MJD_SEQ_TARGET_SCAN.PRODUCT.TIER.h5
# PRODUCT: rawspec = Berkeley high-res products, gpuspec = other instruments.
# TIER 0000 is the fine-res product. No S/R markers: RFI reference is ABACAD
# companion targets in the same session (see download_gbt.py).
GBT_PAT = re.compile(
    r"(?:spliced_)?blc\d+_guppi_(\d+)_(\d+)_([A-Za-z0-9+\-.]+?)_(\d+)"
    r"\.(rawspec|gpuspec)\.(\d+)\.h5$")


def api_get(path):
    """GET an API endpoint and return parsed JSON."""
    url = f"{API}/{path}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())


def find_target(partial):
    """Search for target names matching a partial string."""
    targets = api_get("list-targets")
    matches = []
    for t in targets:
        name = t[0] if isinstance(t, list) else str(t)
        if partial.lower() in name.lower():
            matches.append(name)
    return matches


def query_files(target, telescope=None, filetype=None):
    """Query the BL API for files matching the given criteria."""
    params = [f"target={target}"]
    if telescope:
        params.append(f"telescope={telescope}")
    if filetype:
        params.append(f"file-type={filetype}")
    query = "&".join(params)
    data = api_get(f"query-files?{query}")
    if isinstance(data, list):
        return data
    return data.get("data", data.get("files", data.get("results", [])))


def parse_filename(url):
    """Extract metadata from a BL filename. Two grammars exist:
    Parkes: Parkes_57791_72989_PROXCEN_S_fine.h5 (S=on, R=off)
    GBT:    blc00_guppi_59433_42615_HIP29806_0041.rawspec.0000.h5
    """
    fname = url.split("/")[-1]
    info = {"filename": fname, "url": url}
    m = PARKES_PAT.search(fname)
    if m:
        tel, mjd, seq, tgt, pos, res = m.groups()
        info.update(telescope=tel, mjd=mjd, sequence=seq, target=tgt,
                    position=pos, resolution=res, grammar="parkes")
        return info
    m = GBT_PAT.search(fname)
    if m:
        mjd, seq, tgt, scan, prod, tier = m.groups()
        res = ("fine" if (prod, tier) == ("rawspec", "0000")
               else "hires" if prod == "rawspec" else prod)
        info.update(telescope="GBT", mjd=mjd, sequence=seq, target=tgt,
                    position=None, scan=scan, product=prod, tier=tier,
                    resolution=res, grammar="gbt")
        return info
    return info


def group_cadence(files):
    """Group files into observation sessions (by MJD) and identify on/off pairs."""
    sessions = defaultdict(list)
    for f in files:
        info = parse_filename(f.get("url", ""))
        info["filesize"] = f.get("filesize", f.get("size", 0))
        sessions[info.get("mjd", "unknown")].append(info)

    # Sort each session by sequence number
    for mjd in sessions:
        sessions[mjd].sort(key=lambda x: int(x.get("sequence", 0)))

    return sessions


def format_size(size_bytes):
    """Format bytes as human-readable size."""
    if not size_bytes or size_bytes == 0:
        return "?"
    gb = size_bytes / 1e9
    if gb >= 1:
        return f"{gb:.1f} GB"
    return f"{size_bytes / 1e6:.0f} MB"


def main():
    parser = argparse.ArgumentParser(description="Search Breakthrough Listen Open Data API.")
    parser.add_argument("--target", "-t", help="BL target name (e.g., PROXCEN)")
    parser.add_argument("--find", "-f", help="Fuzzy search for target name")
    parser.add_argument("--telescope", choices=["GBT", "Parkes", "APF"], help="Filter by telescope")
    parser.add_argument("--filetype", choices=["filterbank", "HDF5", "data", "baseband data"],
                        help="Filter by file type")
    parser.add_argument("--cadence", "-c", action="store_true", help="Group results into on/off cadence sessions")
    parser.add_argument("--limit", "-n", type=int, default=20, help="Max files to display (default 20)")
    parser.add_argument("--raw", action="store_true",
                        help="Skip exact-target filtering. The API prefix-matches "
                             "target names (HIP2 also returns HIP26, HIP225, ...); "
                             "default hides those, this shows everything")
    parser.add_argument("--download", "-d", metavar="DIR", help="Download files to this directory")
    args = parser.parse_args()

    # Fuzzy target search
    if args.find:
        print(f"Searching for targets matching '{args.find}'...")
        matches = find_target(args.find)
        if matches:
            print(f"Found {len(matches)} matches:")
            for m in matches[:30]:
                print(f"  {m}")
        else:
            print("No matches found.")
        return

    if not args.target:
        parser.error("--target or --find is required")

    # Query files
    print(f"Querying BL API for target={args.target}", end="")
    if args.telescope:
        print(f", telescope={args.telescope}", end="")
    if args.filetype:
        print(f", filetype={args.filetype}", end="")
    print("...")

    files = query_files(args.target, args.telescope, args.filetype)
    print(f"Found {len(files)} files")

    if not files:
        print("No files found. Try using --find to locate the correct target name.")
        return

    # Exact-target filter: the API prefix-matches ?target= against every
    # name, so HIP2 returns HIP2 plus HIP225, HIP26, HIP29806, ... Keep only
    # files whose filename target token exactly matches (case-insensitive).
    if not args.raw:
        exact = [f for f in files
                 if parse_filename(f.get("url", "")).get("target", "").upper()
                 == args.target.upper()]
        hidden = len(files) - len(exact)
        if hidden:
            print(f"  ({hidden} prefix-matched files from other targets hidden; "
                  f"--raw shows everything)")
        files = exact
        print(f"Exact match: {len(files)} files\n")
        if not files:
            return

    # Parse file info
    parsed = []
    for f in files:
        info = parse_filename(f.get("url", ""))
        info["filesize"] = f.get("filesize", f.get("size", 0))
        parsed.append(info)

    if args.cadence:
        sessions = group_cadence(files)
        print(f"=== Cadence Sessions ({len(sessions)} sessions) ===\n")
        for mjd in sorted(sessions.keys())[:10]:
            entries = sessions[mjd]
            on_files = [e for e in entries if e.get("position") == "S"]
            off_files = [e for e in entries if e.get("position") == "R"]
            total_size = sum(e.get("filesize", 0) for e in entries)
            print(f"MJD {mjd}: {len(on_files)} ON, {len(off_files)} OFF ({format_size(total_size)} total)")
            for e in entries[:6]:
                if e.get("grammar") == "gbt":
                    tag = "A  "  # GBT: target-pointed scan; offs are
                                # ABACAD companion targets, not S/R files
                elif e.get("position") == "S":
                    tag = "ON "
                elif e.get("position") == "R":
                    tag = "OFF"
                else:
                    tag = "?  "
                print(f"  [{tag}] {e['filename']} ({format_size(e.get('filesize', 0))})")
                print(f"        {e['url']}")
            if len(entries) > 6:
                print(f"  ... and {len(entries) - 6} more")
            print()
    else:
        print(f"=== Files (showing {min(args.limit, len(parsed))} of {len(parsed)}) ===\n")
        for info in parsed[:args.limit]:
            print(f"  {info['filename']}")
            print(f"    Size: {format_size(info.get('filesize', 0))}")
            print(f"    URL:  {info['url']}")
            print()

    # Download
    if args.download:
        os.makedirs(args.download, exist_ok=True)
        print(f"\n=== Downloading {min(args.limit, len(parsed))} files to {args.download} ===\n")
        for info in parsed[:args.limit]:
            filepath = os.path.join(args.download, info["filename"])
            if os.path.exists(filepath):
                print(f"  SKIP (exists): {info['filename']}")
                continue
            print(f"  Downloading: {info['filename']} ({format_size(info.get('filesize', 0))})")
            urllib.request.urlretrieve(info["url"], filepath)
            print(f"  Done: {filepath}")
        print("\nDownload complete.")


if __name__ == "__main__":
    main()
