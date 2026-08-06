"""
bl_doppler_search.py - Run turbo_seti Doppler drift search on a BL file.

Usage:
    python bl_doppler_search.py <file.fil or .h5> --out results/
    python bl_doppler_search.py <file> --min-drift -5 --max-drift 5
"""

import argparse
import os
import sys


def run_doppler_search(filepath, out_dir='results', min_drift=-5, max_drift=5,
                       snr=25, min_channel=None, max_channel=None):
    """Run turbo_seti FindDoppler on a data file."""
    from turbo_seti.find_doppler.find_doppler import FindDoppler

    if not os.path.exists(out_dir):
        os.makedirs(out_dir)

    print(f"Loading: {filepath}")
    print(f"Drift range: {min_drift} to {max_drift} Hz/s")
    print(f"SNR threshold: {snr}")

    doppler = FindDoppler(
        filepath,
        min_drift=min_drift,
        max_drift=max_drift,
        snr_threshold=snr,
        out_dir=out_dir,
    )

    basename = os.path.splitext(os.path.basename(filepath))[0]
    dat_path = os.path.join(out_dir, basename + '.dat')

    print(f"Running search...")
    doppler.search()

    # Check results
    if os.path.exists(dat_path):
        size = os.path.getsize(dat_path)
        with open(dat_path) as f:
            lines = f.readlines()
        # Count hits (skip header lines)
        hits = [l for l in lines if not l.startswith('#') and l.strip()]
        print(f"\nResults: {len(hits)} hits found")
        print(f"Output: {dat_path} ({size} bytes)")

        if hits:
            print(f"\nTop hits:")
            for line in hits[:10]:
                print(f"  {line.strip()}")
    else:
        print("No output file generated (zero hits or error)")

    return dat_path


def main():
    parser = argparse.ArgumentParser(description='turbo_seti Doppler drift search.')
    parser.add_argument('filepath', help='Path to .fil or .h5 file')
    parser.add_argument('--out', '-o', default='results', help='Output directory')
    parser.add_argument('--min-drift', type=float, default=-5, help='Min drift rate (Hz/s)')
    parser.add_argument('--max-drift', type=float, default=5, help='Max drift rate (Hz/s)')
    parser.add_argument('--snr', type=float, default=25, help='SNR threshold')
    args = parser.parse_args()

    if not os.path.exists(args.filepath):
        print(f"Error: file not found: {args.filepath}")
        sys.exit(1)

    run_doppler_search(args.filepath, out_dir=args.out,
                       min_drift=args.min_drift, max_drift=args.max_drift,
                       snr=args.snr)


if __name__ == '__main__':
    main()
