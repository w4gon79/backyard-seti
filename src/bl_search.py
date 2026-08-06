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
import sys
import urllib.request
import json
from collections import defaultdict

API = "http://seti.berkeley.edu/opendata/api"
DOWNLOAD_BASE = "http://blpd8.ssl.berkeley.edu/dl/"


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
    """Extract metadata from a BL filename."""
    fname = url.split("/")[-1]
    parts = fname.replace(".h5", "").split("_")
    info = {"filename": fname, "url": url}
    if len(parts) >= 5:
        info["telescope"] = parts[0]
        info["mjd"] = parts[1]
        info["sequence"] = parts[2]
        info["target"] = parts[3]
        info["position"] = parts[4] if len(parts) > 4 else "?"  # S=on, R=off
        info["resolution"] = parts[5] if len(parts) > 5 else "?"
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
    print(f"Found {len(files)} files\n")

    if not files:
        print("No files found. Try using --find to locate the correct target name.")
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
                tag = "ON " if e.get("position") == "S" else "OFF"
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
