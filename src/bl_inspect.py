"""
bl_inspect.py - Inspect a Breakthrough Listen data file.

Usage:
    python bl_inspect.py <path/to/file.fil or .h5>
    python bl_inspect.py <file> --waterfall --save
    python bl_inspect.py <file> --header
"""

import argparse
import sys
import os

def inspect_file(filepath, show_waterfall=False, save_plot=False, header_only=False):
    """Load a BL file with blimpy and display info."""
    from blimpy import Waterfall
    import numpy as np

    print(f"Loading: {filepath}")
    print(f"File size: {os.path.getsize(filepath) / 1e9:.2f} GB")

    wf = Waterfall(filepath)

    # Header / metadata
    print("\n=== Header ===")
    print(f"  Source:     {wf.header.get('source_name', 'Unknown')}")
    print(f"  Telescope:  {wf.header.get('telescope_id', 'Unknown')}")
    print(f"  RA:         {wf.header.get('src_raj', 'N/A')}")
    print(f"  Dec:        {wf.header.get('src_dej', 'N/A')}")
    print(f"  MJD:        {wf.header.get('tstart', 'N/A')}")
    print(f"  Freq start: {wf.header.get('fch1', 'N/A')} MHz")
    print(f"  Freq step:  {wf.header.get('foff', 'N/A')} Hz")
    print(f"  N channels: {wf.header.get('nchans', 'N/A')}")
    print(f"  N ints:     {wf.n_ints_in_file}")
    print(f"  Data shape: {wf.data.shape}")
    print(f"  Freq range: {wf.container.f_min:.4f} - {wf.container.f_max:.4f} MHz")

    if header_only:
        return

    if show_waterfall or save_plot:
        import matplotlib
        if not show_waterfall:
            matplotlib.use('Agg')
        import matplotlib.pyplot as plt

        fig = plt.figure(figsize=(14, 8))
        wf.plot_waterfall()
        plt.title(f"{wf.header.get('source_name', 'Unknown')} - BL Data", fontsize=12)
        plt.tight_layout()

        if save_plot:
            outpath = os.path.splitext(filepath)[0] + '_waterfall.png'
            plt.savefig(outpath, dpi=150)
            print(f"\nSaved: {outpath}")

        if show_waterfall:
            plt.show()

    return wf


def main():
    parser = argparse.ArgumentParser(description='Inspect a Breakthrough Listen data file.')
    parser.add_argument('filepath', help='Path to .fil or .h5 file')
    parser.add_argument('--waterfall', '-w', action='store_true', help='Show waterfall plot')
    parser.add_argument('--save', '-s', action='store_true', help='Save plot to file')
    parser.add_argument('--header', action='store_true', help='Header info only')
    args = parser.parse_args()

    if not os.path.exists(args.filepath):
        print(f"Error: file not found: {args.filepath}")
        sys.exit(1)

    inspect_file(args.filepath, show_waterfall=args.waterfall,
                 save_plot=args.save, header_only=args.header)


if __name__ == '__main__':
    main()
