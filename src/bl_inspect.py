"""
bl_inspect.py - Inspect a Breakthrough Listen data file.

Usage:
    python bl_inspect.py <path/to/file.fil or .h5>
    python bl_inspect.py <file> --waterfall --save
    python bl_inspect.py <file> --header
    python bl_inspect.py <file> --waterfall --save --fstart 1400 --fstop 1500
"""

import argparse
import sys
import os


def inspect_file(filepath, show_waterfall=False, save_plot=False, header_only=False,
                 fstart=None, fstop=None):
    """Load a BL file with blimpy and display info."""
    from blimpy import Waterfall
    import numpy as np

    print(f"Loading: {filepath}")
    print(f"File size: {os.path.getsize(filepath) / 1e9:.2f} GB")

    file_gb = os.path.getsize(filepath) / 1e9
    # For large files, use max_load to prevent OOM
    if file_gb > 2:
        wf = Waterfall(filepath, max_load=2)
        print(f"  (Large file: {file_gb:.1f} GB, using disk-backed loading)")
    else:
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
    f_ch1 = wf.header.get('fch1', 0)
    f_off = wf.header.get('foff', 0)
    n_chans = wf.header.get('nchans', 0)
    f_high = f_ch1
    f_low = f_ch1 + f_off * n_chans  # foff is negative for descending freq
    f_min = min(f_high, f_low)
    f_max = max(f_high, f_low)
    print(f"  Freq range: {f_min:.4f} - {f_max:.4f} MHz")

    if header_only:
        return wf

    if show_waterfall or save_plot:
        import matplotlib
        if not show_waterfall:
            matplotlib.use('Agg')
        import matplotlib.pyplot as plt

        # Determine frequency window to plot
        if fstart is not None and fstop is not None:
            plot_lo, plot_hi = min(fstart, fstop), max(fstart, fstop)
        elif file_gb > 2:
            # Large file: default to 100 MHz window from the bottom of the band
            plot_lo = f_min
            plot_hi = f_min + 100.0
        else:
            # Small file: plot everything
            plot_lo = f_min
            plot_hi = f_max

        print(f"  (Plotting frequency window: {plot_lo:.1f} - {plot_hi:.1f} MHz)")

        if file_gb > 2:
            # Large file: bypass plot_waterfall(), read HDF5 directly
            fig, ax = plot_large_waterfall(filepath, wf, plot_lo, plot_hi)
        else:
            # Small file: use blimpy's built-in plot
            fig = plt.figure(figsize=(14, 8))
            wf.plot_waterfall()
            ax = plt.gca()

        ax.set_title(f"{wf.header.get('source_name', 'Unknown')} - BL Data "
                     f"({plot_lo:.0f}-{plot_hi:.0f} MHz)", fontsize=12)
        plt.tight_layout()

        if save_plot:
            outpath = os.path.splitext(filepath)[0] + '_waterfall.png'
            plt.savefig(outpath, dpi=150)
            print(f"\nSaved: {outpath}")

        if show_waterfall:
            plt.show()
        else:
            plt.close()

    return wf


def plot_large_waterfall(filepath, wf, f_start_mhz, f_stop_mhz):
    """For large HDF5 files, read a frequency slice directly and plot it."""
    import h5py
    import numpy as np
    import matplotlib.pyplot as plt

    f_ch1 = wf.header.get('fch1', 0)      # MHz, highest freq
    f_off = wf.header.get('foff', 0)       # Hz, negative
    n_chans = wf.header.get('nchans', 0)
    tsamp = wf.header.get('tsamp', 1.0)    # seconds

    # Convert f_off to MHz
    f_off_mhz = f_off / 1e6  # negative value

    # Channel indices for our frequency window
    # freq(i) = f_ch1 + f_off_mhz * i
    # i = (freq - f_ch1) / f_off_mhz
    ch_start = int((f_start_mhz - f_ch1) / f_off_mhz)
    ch_stop = int((f_stop_mhz - f_ch1) / f_off_mhz)

    # Clamp to valid range
    ch_start = max(0, ch_start)
    ch_stop = min(n_chans, ch_stop)
    if ch_start >= ch_stop:
        ch_start, ch_stop = 0, min(n_chans, 100000)

    n_plot_chans = ch_stop - ch_start
    print(f"  (Reading channels {ch_start}-{ch_stop}, {n_plot_chans} channels)")

    # Read the data slice from HDF5
    with h5py.File(filepath, 'r') as h5:
        # BL HDF5 structure: DATA shape is (nints, nifs, nchans)
        # or sometimes (nifs, nints, nchans)
        # BL HDF5 files use lowercase 'data' key
        if 'data' in h5:
            data = h5['data']
        elif 'DATA' in h5:
            data = h5['DATA']
        else:
            # Find the dataset with largest size
            datasets = [(k, h5[k]) for k in h5.keys() if hasattr(h5[k], 'shape')]
            if not datasets:
                raise Exception(f"No datasets found in HDF5 file. Keys: {list(h5.keys())}")
            data = max(datasets, key=lambda x: x[1].size)[1]
        shape = data.shape
        print(f"  (HDF5 data shape: {shape})")

        # Read the frequency slice across all time integrations
        # Try common BL layouts
        if len(shape) == 3:
            # Most BL files: (nints, 1, nchans)
            if shape[1] == 1:
                plot_data = data[:, 0, ch_start:ch_stop]
            elif shape[0] == 1:
                plot_data = data[0, :, ch_start:ch_stop]
            else:
                plot_data = data[:, 0, ch_start:ch_stop]
        else:
            plot_data = data[:ch_stop] if len(shape) == 1 else data[..., ch_start:ch_stop]

    plot_data = np.array(plot_data, dtype=np.float64)

    # Decimate frequency axis if too many channels for plotting
    max_plot_cols = 2000
    if plot_data.shape[1] > max_plot_cols:
        step = plot_data.shape[1] // max_plot_cols
        plot_data = plot_data[:, ::step]
        print(f"  (Decimated to {plot_data.shape[1]} frequency bins for display)")

    # Build frequency axis for the plot
    n_display = plot_data.shape[1]
    freqs = np.linspace(f_start_mhz, f_stop_mhz, n_display)

    # Time axis
    n_times = plot_data.shape[0]
    times = np.arange(n_times) * tsamp

    fig, ax = plt.subplots(figsize=(14, 8))
    # Use log scale for better dynamic range
    plot_db = 10 * np.log10(np.maximum(plot_data, 1e-30))
    im = ax.pcolormesh(freqs, times, plot_db, shading='auto', cmap='viridis')
    cbar = plt.colorbar(im, ax=ax, label='Power (dB)')
    ax.set_xlabel('Frequency (MHz)')
    ax.set_ylabel('Time (s)')
    ax.set_xlim(f_start_mhz, f_stop_mhz)

    return fig, ax


def main():
    parser = argparse.ArgumentParser(description='Inspect a Breakthrough Listen data file.')
    parser.add_argument('filepath', help='Path to .fil or .h5 file')
    parser.add_argument('--waterfall', '-w', action='store_true', help='Show waterfall plot')
    parser.add_argument('--save', '-s', action='store_true', help='Save plot to file')
    parser.add_argument('--header', action='store_true', help='Header info only')
    parser.add_argument('--fstart', type=float, default=None, help='Plot start frequency (MHz)')
    parser.add_argument('--fstop', type=float, default=None, help='Plot stop frequency (MHz)')
    args = parser.parse_args()

    if not os.path.exists(args.filepath):
        print(f"Error: file not found: {args.filepath}")
        sys.exit(1)

    inspect_file(args.filepath, show_waterfall=args.waterfall,
                 save_plot=args.save, header_only=args.header,
                 fstart=args.fstart, fstop=args.fstop)


if __name__ == '__main__':
    main()
