#!/usr/bin/env python3
"""Download multiple PROXCEN mid-res cadence sessions."""
import urllib.request
import os
import sys
import time

# Sessions to download (spread across different observation epochs)
# Already have: 57910
# Downloading: 57783-equivalent (57790 is closest early epoch), 57846, 57904, 57942, 58026
SESSIONS = {
    "57790": [
        "http://blpd4.ssl.berkeley.edu/dl2/Parkes_57790_62144_PROXCEN_S_mid.h5",
        "http://blpd4.ssl.berkeley.edu/dl2/Parkes_57790_62489_PROXCEN_R_mid.h5",
        "http://blpd4.ssl.berkeley.edu/dl2/Parkes_57790_62830_PROXCEN_S_mid.h5",
        "http://blpd4.ssl.berkeley.edu/dl2/Parkes_57790_63169_PROXCEN_R_mid.h5",
        "http://blpd4.ssl.berkeley.edu/dl2/Parkes_57790_63510_PROXCEN_S_mid.h5",
        "http://blpd4.ssl.berkeley.edu/dl2/Parkes_57790_63847_PROXCEN_R_mid.h5",
    ],
    "57846": [
        "http://blpd3.ssl.berkeley.edu/dl2/Parkes_57846_49534_PROXCEN_S_mid.h5",
        "http://blpd3.ssl.berkeley.edu/dl2/Parkes_57846_49879_PROXCEN_R_mid.h5",
        "http://blpd3.ssl.berkeley.edu/dl2/Parkes_57846_50220_PROXCEN_S_mid.h5",
        "http://blpd3.ssl.berkeley.edu/dl2/Parkes_57846_50560_PROXCEN_R_mid.h5",
        "http://blpd3.ssl.berkeley.edu/dl2/Parkes_57846_50900_PROXCEN_S_mid.h5",
        "http://blpd3.ssl.berkeley.edu/dl2/Parkes_57846_51239_PROXCEN_R_mid.h5",
    ],
    "57904": [
        "http://blpd3.ssl.berkeley.edu/dl2/Parkes_57904_43071_PROXCEN_S_mid.h5",
        "http://blpd3.ssl.berkeley.edu/dl2/Parkes_57904_43412_PROXCEN_R_mid.h5",
        "http://blpd3.ssl.berkeley.edu/dl2/Parkes_57904_43752_PROXCEN_S_mid.h5",
        "http://blpd3.ssl.berkeley.edu/dl2/Parkes_57904_44092_PROXCEN_R_mid.h5",
        "http://blpd3.ssl.berkeley.edu/dl2/Parkes_57904_44432_PROXCEN_S_mid.h5",
        "http://blpd3.ssl.berkeley.edu/dl2/Parkes_57904_44772_PROXCEN_R_mid.h5",
    ],
    "57942": [
        "http://blpd3.ssl.berkeley.edu/dl2/Parkes_57942_33681_PROXCEN_S_mid.h5",
        "http://blpd3.ssl.berkeley.edu/dl2/Parkes_57942_34021_PROXCEN_R_mid.h5",
        "http://blpd3.ssl.berkeley.edu/dl2/Parkes_57942_34362_PROXCEN_S_mid.h5",
        "http://blpd3.ssl.berkeley.edu/dl2/Parkes_57942_34702_PROXCEN_R_mid.h5",
        "http://blpd3.ssl.berkeley.edu/dl2/Parkes_57942_35042_PROXCEN_S_mid.h5",
        "http://blpd3.ssl.berkeley.edu/dl2/Parkes_57942_35382_PROXCEN_R_mid.h5",
    ],
    "58026": [
        "http://blpd3.ssl.berkeley.edu/dl2/Parkes_58026_20429_PROXCEN_S_mid.h5",
        "http://blpd3.ssl.berkeley.edu/dl2/Parkes_58026_20771_PROXCEN_R_mid.h5",
        "http://blpd3.ssl.berkeley.edu/dl2/Parkes_58026_21110_PROXCEN_S_mid.h5",
        "http://blpd3.ssl.berkeley.edu/dl2/Parkes_58026_21450_PROXCEN_R_mid.h5",
        "http://blpd3.ssl.berkeley.edu/dl2/Parkes_58026_21790_PROXCEN_S_mid.h5",
        "http://blpd3.ssl.berkeley.edu/dl2/Parkes_58026_22130_PROXCEN_R_mid.h5",
    ],
}

OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

def download(url, dest):
    fname = url.split("/")[-1]
    filepath = os.path.join(dest, fname)
    if os.path.exists(filepath):
        print(f"  SKIP (exists): {fname}")
        return
    print(f"  Downloading: {fname}")
    try:
        urllib.request.urlretrieve(url, filepath)
        sz = os.path.getsize(filepath) / 1e6
        print(f"  Done: {sz:.0f} MB")
    except Exception as e:
        print(f"  ERROR: {e}")

def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    total_files = sum(len(urls) for urls in SESSIONS.values())
    print(f"Downloading {total_files} files ({len(SESSIONS)} sessions) to {OUT_DIR}\n")

    for i, (mjd, urls) in enumerate(sorted(SESSIONS.items()), 1):
        print(f"\n=== Session MJD {mjd} ({i}/{len(SESSIONS)}) ===")
        for url in urls:
            download(url, OUT_DIR)

    print("\n=== All downloads complete ===")
    # List what we have
    h5_files = [f for f in os.listdir(OUT_DIR) if f.endswith(".h5")]
    print(f"Total .h5 files in data/: {len(h5_files)}")

if __name__ == "__main__":
    main()
