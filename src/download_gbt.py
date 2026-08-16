#!/usr/bin/env python3
"""download_gbt.py - Download GBT fine-res epochs from the BL open data archive.

GBT data works completely differently from Parkes (see README "Downloading
Observation Epochs"). There are no ON/OFF (S/R) markers. Instead:

  1. One GBT scan = ~26 rawspec.0000 files, one per compute node, together
     covering 1.1-11.9 GHz in 187.5 MHz sub-bands (~3.8 GB each, ~100 GB per
     scan). You can cherry-pick sub-bands by center frequency.
  2. A session (identified by MJD) interleaves your target's scans (A) with
     scans of OTHER nearby targets (B, C, D). This is the ABACAD cadence.
     Those companion scans are the RFI reference: a signal seen in your
     target AND in a companion is local RFI. For serious work, download the
     companion scans too (same sub-band filter).
  3. The BL API prefix-matches target names, so every result is filtered
     against the exact target token in the filename before anything else.
  4. Companion discovery is BEST-EFFORT: the API has no "query by session"
     endpoint, so companions can only be found among targets sharing your
     target's name prefix (which the prefix-match response includes). For
     HIP-style blocks this usually works; for isolated targets it finds
     nothing. Cross-epoch matching remains the primary RFI filter.

Usage:
    # List all sessions for a target (no download)
    python src/download_gbt.py --target HIP69

    # Show one session's full scan-by-scan layout, companions included
    python src/download_gbt.py --target HIP69 --session 58050

    # Download L-band (1.1-2.0 GHz) fine files for the newest session,
    # including ABACAD companion scans
    python src/download_gbt.py --target HIP69 --band 1400 --companions \\
        --download data/gbt

    # Download everything ever taken of a target in one band
    python src/download_gbt.py --target HIP69 --band 1400 --all-sessions \\
        --download data/gbt

    # Write a plain URL list (feed it to aria2c/curl or the dashboard)
    python src/download_gbt.py --target HIP69 --band 1400 --companions \\
        --session 58050 --list gbt_urls.txt
"""
import argparse
import re
import sys
import time
import urllib.parse
import urllib.request
import json
from collections import defaultdict

BL_API = "http://seti.berkeley.edu/opendata/api"
_UA = {"User-Agent": "BackyardSETI/1.0"}
DOWNLOAD_BASE = "http://blpd8.ssl.berkeley.edu/dl/"

# (spliced_)blcNN_guppi_MJD_SEQ_TARGET_SCAN.rawspec.TIER.h5
GBT_PAT = re.compile(
    r"(?:spliced_)?blc\d+_guppi_(\d+)_(\d+)_([A-Za-z0-9+\-.]+?)_(\d+)"
    r"\.(rawspec|gpuspec)\.(\d+)\.h5$")

# L-band sub-band centers (MHz). BL L-band receivers run 1.1-1.9 GHz, so the
# useful file centers are those whose 187.5 MHz span overlaps 1100-2000.
BANDS = {
    "L": (1100, 2000),
    "S": (2000, 4000),
    "C": (4000, 8000),
    "X": (8000, 12000),
}
DEFAULT_BAND = (1100, 2000)
SPAN_MHZ = 187.5  # each rawspec.0000 file covers ~187.5 MHz


def api_query(target):
    """Query the BL API and return every GBT-grammar file with parsed
    metadata. The API prefix-matches target names (HIP2 also returns
    HIP26, HIP225, ...), so callers MUST filter by _parsed['target'];
    the raw list is kept for ABACAD companion discovery."""
    url = f"{BL_API}/query-files?target={urllib.parse.quote(target)}"
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.loads(r.read().decode())
    files = data.get("data", data) if isinstance(data, dict) else data
    out = []
    for f in files:
        fname = (f.get("url") or "").split("/")[-1]
        m = GBT_PAT.search(fname)
        if m:
            f["_parsed"] = {
                "mjd": m.group(1), "seq": int(m.group(2)),
                "target": m.group(3), "scan": m.group(4),
                "prod": m.group(5), "tier": m.group(6),
                "spliced": fname.startswith("spliced_"),
                "cf": f.get("center_freq") or 0,
                "size": f.get("size") or f.get("filesize") or 0,
            }
            out.append(f)
    return out


def is_fine(p):
    """Fine-res = tier 0000, either product family: per-node rawspec
    (2017+, 187.5 MHz each) or gpuspec incl. spliced whole-band files
    (2016 era, ~15.5 GB covering the full receiver band)."""
    return p["tier"] == "0000"


def sessions_for(files, fine_only=True):
    """Group fine-res files into sessions by MJD."""
    sess = defaultdict(list)
    for f in files:
        p = f["_parsed"]
        if fine_only and not is_fine(p):
            continue
        sess[p["mjd"]].append(f)
    return dict(sorted(sess.items(), key=lambda kv: int(kv[0])))


def companion_seqs(target_seqs):
    """Candidate companion sequence windows: +/- 10 SEQ around each A scan."""
    lo = min(target_seqs) - 10
    hi = max(target_seqs) + 10
    return lo, hi


def find_companions(all_files, mjd, target, target_seqs):
    """Files from other targets in the same session, SEQ-adjacent to the
    target's scans. Returns {target_name: [files]} for seq-neighbors only."""
    lo, hi = companion_seqs(target_seqs)
    comps = defaultdict(list)
    for f in all_files:
        p = f["_parsed"]
        if p["mjd"] != mjd or p["target"].upper() == target.upper():
            continue
        if not is_fine(p):
            continue
        if lo <= p["seq"] <= hi:
            comps[p["target"]].append(f)
    return dict(comps)


def in_band(f, band):
    span = 750.0 if f["_parsed"]["spliced"] else SPAN_MHZ
    cf = f["_parsed"]["cf"]
    lo, hi = band
    return cf - span / 2 <= hi and cf + span / 2 >= lo


def fmt_gb(b):
    return f"{b / 1e9:.1f} GB"


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--target", "-t", required=True, help="Exact BL target token "
                    "(as it appears in filenames, e.g. HIP69, HIP26)")
    ap.add_argument("--session", "-s", help="Restrict to one session MJD")
    ap.add_argument("--all-sessions", action="store_true",
                    help="Use every session, not just the newest")
    ap.add_argument("--band", "-b", default="L",
                    help="Frequency band: L (default), S, C, X, or min,max in MHz")
    ap.add_argument("--companions", action="store_true",
                    help="Include ABACAD companion target scans (RFI reference)")
    ap.add_argument("--download", "-d", metavar="DIR",
                    help="Download selected files to this directory")
    ap.add_argument("--list", metavar="FILE",
                    help="Write selected URLs to a text file instead of downloading")
    args = ap.parse_args()

    if args.band.upper() in BANDS:
        band = BANDS[args.band.upper()]
    else:
        lo, hi = args.band.split(",")
        band = (float(lo), float(hi))

    print(f"Querying BL API for target={args.target} ...")
    raw = api_query(args.target)
    files = [f for f in raw
             if f["_parsed"]["target"].upper() == args.target.upper()]
    fine = [f for f in files if is_fine(f["_parsed"])]
    print(f"  {len(raw)} GBT files in response, {len(files)} exact-match "
          f"(prefix hits from other targets ignored)")
    print(f"  {len(fine)} fine-res (tier 0000) files for {args.target}\n")

    sess = sessions_for(files)
    if not sess:
        print("No fine-res GBT files for this target. Check the exact token "
              "(use src/bl_search.py --find <partial> to locate it).")
        return 1

    print(f"Sessions ({len(sess)}):")
    for mjd, fs in sess.items():
        tot = sum(f["_parsed"]["size"] for f in fs)
        band_n = sum(1 for f in fs if in_band(f, band))
        band_b = sum(f["_parsed"]["size"] for f in fs if in_band(f, band))
        print(f"  MJD {mjd}: {len(fs)} fine files {fmt_gb(tot)} | "
              f"in-band: {band_n} files {fmt_gb(band_b)}")

    if args.session:
        picks = [args.session]
    elif args.all_sessions:
        picks = list(sess.keys())
    else:
        picks = [max(sess.keys(), key=int)]
        print(f"\nNewest session MJD {picks[0]} selected "
              f"(use --session or --all-sessions to change)")

    selected = []
    print()
    for mjd in picks:
        fine_m = sess[mjd]
        t_seq = sorted({f["_parsed"]["seq"] for f in fine_m})
        band_files = [f for f in fine_m if in_band(f, band)]
        tb = sum(f["_parsed"]["size"] for f in band_files)
        print(f"=== MJD {mjd} ===")
        print(f"  target scans: {len(t_seq)} (seq {t_seq[0]}..{t_seq[-1]})")
        print(f"  in-band fine files: {len(band_files)} ({fmt_gb(tb)})")
        selected += band_files

        if args.companions:
            comps = find_companions(raw, mjd, args.target, t_seq)
            for cname, cfs in sorted(comps.items()):
                cband = [f for f in cfs if in_band(f, band)]
                cb = sum(f["_parsed"]["size"] for f in cband)
                mark = "-> downloading" if cband else "(none in band)"
                print(f"  companion {cname}: {len(cfs)} fine, "
                      f"{len(cband)} in-band {fmt_gb(cb)} {mark}")
                selected += cband
        print()

    tot_sel = sum(f["_parsed"]["size"] for f in selected)
    print(f"TOTAL selected: {len(selected)} files, {fmt_gb(tot_sel)}")

    if args.list:
        with open(args.list, "w") as fh:
            for f in selected:
                fh.write(f["url"] + "\n")
        print(f"URL list written to {args.list}")
        return 0

    if args.download:
        import os
        os.makedirs(args.download, exist_ok=True)
        done = skip = fail = 0
        for f in sorted(selected, key=lambda x: x["_parsed"]["cf"]):
            dest = os.path.join(args.download, f["url"].split("/")[-1])
            if os.path.exists(dest):
                print(f"  SKIP (exists) {os.path.basename(dest)}")
                skip += 1
                continue
            print(f"  GET {os.path.basename(dest)} "
                  f"({fmt_gb(f['_parsed']['size'])}) ...", end=" ", flush=True)
            t0 = time.time()
            try:
                urllib.request.urlretrieve(f["url"], dest)
                dt = time.time() - t0
                mbps = (f["_parsed"]["size"] / 1e6) / max(dt, 0.1)
                print(f"done ({mbps:.0f} MB/s)")
                done += 1
            except Exception as e:
                print(f"FAILED: {e}")
                fail += 1
        print(f"\nDownloaded {done}, skipped {skip}, failed {fail}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
