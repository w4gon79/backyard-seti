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
                       snr=25):
    """Run turbo_seti FindDoppler on a data file."""
    from turbo_seti.find_doppler.find_doppler import FindDoppler

    # Resolve absolute paths before any chdir
    filepath = os.path.abspath(filepath)
    out_dir = os.path.abspath(out_dir)
    basename = os.path.basename(filepath)

    if not os.path.exists(out_dir):
        os.makedirs(out_dir)

    # turbo_seti uses filename.split('/')[-1] internally to build output paths.
    # On Windows, backslash paths break this. Workaround: chdir to the data dir
    # so turbo_seti sees just the basename.
    data_dir = os.path.dirname(filepath)
    original_cwd = os.getcwd()
    os.chdir(data_dir)

    try:
        # turbo_seti: min_drift = smallest detectable drift rate (near zero)
        #             max_drift = largest detectable drift rate
        # It searches BOTH positive and negative automatically.
        # Most signals drift at 0.01-0.5 Hz/s, so min_drift must be tiny.
        abs_max = abs(max_drift)
        abs_min = 1e-05  # near-zero, catches slow drifters (RFI and real signals)

        print(f"Loading: {filepath}")
        print(f"Drift range: -{abs_max} to +{abs_max} Hz/s (min detectable: {abs_min} Hz/s)")
        print(f"SNR threshold: {snr}")

        # Pass out_dir as absolute path with forward slashes (turbo_seti uses .rstrip('/'))
        out_dir_fwd = out_dir.replace('\\', '/')

        doppler = FindDoppler(
            basename,
            min_drift=abs_min,
            max_drift=abs_max,
            snr=snr,
            out_dir=out_dir_fwd,
        )

        print(f"Running search...")
        doppler.search()
    finally:
        os.chdir(original_cwd)

    # Find output files
    stem = os.path.splitext(basename)[0]
    # turbo_seti writes to out_dir/stem.dat and out_dir/stem.log
    dat_path = os.path.join(out_dir, stem + '.dat')

    if os.path.exists(dat_path):
        size = os.path.getsize(dat_path)
        with open(dat_path) as f:
            lines = f.readlines()
        hits = [l for l in lines if not l.startswith('#') and l.strip()]
        print(f"\nResults: {len(hits)} hits found")
        print(f"Output: {dat_path} ({size} bytes)")

        if hits:
            print(f"\nTop hits:")
            for line in hits[:10]:
                print(f"  {line.strip()}")
    else:
        print("No output file generated (zero hits or error)")
        # Check if it ended up in the data dir instead
        alt_dat = os.path.join(data_dir, stem + '.dat')
        if os.path.exists(alt_dat):
            print(f"(Found output in data dir: {alt_dat})")

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
