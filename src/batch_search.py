#!/usr/bin/env python3
# NOTE: Must run with Python 3.11 (has turbo_seti installed)
# e.g. python src\batch_search.py
"""
batch_search.py - Run turbo_seti on all PROXCEN cadence sessions.
Outputs results to results/ and writes a summary to results/batch_summary.txt
"""

import os
import sys
import glob
import time
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bl_doppler_search import run_doppler_search

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
RESULTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "results")

def group_sessions():
    sessions = defaultdict(list)
    for f in sorted(glob.glob(os.path.join(DATA_DIR, "Parkes_*_PROXCEN_*_mid.h5"))):
        fname = os.path.basename(f)
        parts = fname.split("_")
        if len(parts) >= 6:
            mjd = parts[1]
            sessions[mjd].append(f)
    return sessions

def parse_dat(dat_path):
    hits = []
    if not os.path.exists(dat_path):
        return hits
    with open(dat_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            cols = line.split("\t")
            if len(cols) >= 3:
                hits.append({
                    "drift": float(cols[0]),
                    "snr": float(cols[1]),
                    "freq": float(cols[2]),
                })
    return hits

def find_on_only_hits(session_results):
    on_freqs = set()
    off_freqs = set()
    for filepath, hits in session_results.items():
        fname = os.path.basename(filepath)
        is_on = "_S_" in fname
        for h in hits:
            f_rounded = round(h["freq"], -3)
            if is_on:
                on_freqs.add(f_rounded)
            else:
                off_freqs.add(f_rounded)
    return on_freqs - off_freqs

def main():
    os.makedirs(RESULTS_DIR, exist_ok=True)
    sessions = group_sessions()
    
    log_path = os.path.join(RESULTS_DIR, "batch_summary.txt")
    log = open(log_path, "w")
    def pprint(*args):
        msg = " ".join(str(a) for a in args)
        print(msg)
        log.write(msg + "\n")
        log.flush()
    
    pprint(f"Found {len(sessions)} PROXCEN cadence sessions")
    pprint(f"Data dir: {DATA_DIR}")
    pprint(f"Results dir: {RESULTS_DIR}")
    
    all_summary = []
    
    for mjd in sorted(sessions.keys()):
        files = sessions[mjd]
        pprint(f"\n{'='*60}")
        pprint(f"=== Session MJD {mjd} ({len(files)} files) ===")
        pprint(f"{'='*60}")
        
        session_hits = {}
        t0 = time.time()
        
        for i, filepath in enumerate(files, 1):
            fname = os.path.basename(filepath)
            tag = "ON " if "_S_" in fname else "OFF"
            pprint(f"\n[{i}/{len(files)}] [{tag}] {fname}")
            
            # Skip if already processed
            stem = os.path.splitext(fname)[0]
            dat_path = os.path.join(RESULTS_DIR, stem + ".dat")
            if os.path.exists(dat_path):
                hits = parse_dat(dat_path)
                session_hits[filepath] = hits
                pprint(f"  SKIP (already done): {len(hits)} hits")
                continue
            
            try:
                run_doppler_search(
                    filepath, out_dir=RESULTS_DIR,
                    min_drift=-5, max_drift=5, snr=10
                )
                hits = parse_dat(dat_path)
                session_hits[filepath] = hits
                pprint(f"  -> {len(hits)} hits at SNR>=10")
            except Exception as e:
                pprint(f"  -> ERROR: {e}")
                session_hits[filepath] = []
        
        elapsed = time.time() - t0
        on_only = find_on_only_hits(session_hits)
        total_on = sum(len(h) for f, h in session_hits.items() if "_S_" in os.path.basename(f))
        total_off = sum(len(h) for f, h in session_hits.items() if "_R_" in os.path.basename(f))
        
        summary = {
            "mjd": mjd,
            "files": len(files),
            "total_hits_on": total_on,
            "total_hits_off": total_off,
            "on_only_freqs": len(on_only),
            "on_only_list": sorted(on_only),
            "elapsed_s": elapsed,
        }
        all_summary.append(summary)
        
        pprint(f"\n--- Session MJD {mjd} Summary ---")
        pprint(f"  Total ON hits:  {total_on}")
        pprint(f"  Total OFF hits: {total_off}")
        pprint(f"  ON-only freqs:  {len(on_only)}")
        if on_only:
            pprint(f"  ON-only frequencies (Hz): {sorted(on_only)[:20]}")
        pprint(f"  Elapsed: {elapsed:.0f}s")
    
    pprint(f"\n\n{'='*60}")
    pprint(f"=== FINAL SUMMARY (SNR >= 10) ===")
    pprint(f"{'='*60}")
    pprint(f"{'MJD':<10} {'ON hits':<10} {'OFF hits':<10} {'ON-only':<10} {'Time':<8}")
    for s in all_summary:
        pprint(f"{s['mjd']:<10} {s['total_hits_on']:<10} {s['total_hits_off']:<10} {s['on_only_freqs']:<10} {s['elapsed_s']:.0f}s")
    
    all_on_only = set()
    for s in all_summary:
        all_on_only.update(s["on_only_list"])
    
    if all_on_only:
        pprint(f"\n*** {len(all_on_only)} unique ON-only candidate frequencies across all sessions ***")
        for f in sorted(all_on_only)[:50]:
            pprint(f"  {f:.0f} Hz")
    else:
        pprint(f"\n*** Zero ON-only candidates across all sessions ***")
    
    pprint("\nDone.")
    log.close()

if __name__ == "__main__":
    main()
