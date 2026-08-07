#!/usr/bin/env python3
"""
inject_signal.py - Inject synthetic narrowband signals into BL HDF5 files
for pipeline validation.

Uses blimpy for reading (handles BL HDF5 quirks). Copies the original file
then patches the data in-place with h5py, preserving all original metadata.

Usage:
    # Single injection
    python inject_signal.py data/Parkes_57790_62144_PROXCEN_S_mid.h5 \
        --freq 1420.405 --drift 0.5 --snr 15 --out data/injected/

    # Batch: all drift/SNR combos
    python inject_signal.py data/Parkes_57790_62144_PROXCEN_S_mid.h5 \
        --batch --drifts 0.01,0.1,0.5,1.0,2.0 --snrs 5,10,15,20,25,30 \
        --out data/injected/
"""

import argparse
import os
import sys
import time
import itertools
import shutil
import numpy as np
import h5py


def load_header_blimpy(filepath):
    """Load just the header from a BL file via blimpy."""
    from blimpy import Waterfall
    wf = Waterfall(filepath, load_data=False)
    return wf.header


def load_data_blimpy(filepath):
    """Load data array from a BL file via blimpy."""
    from blimpy import Waterfall
    wf = Waterfall(filepath, load_data=True)
    return np.array(wf.data, dtype=np.float32)


def estimate_noise(data):
    """Robust noise estimate using MAD."""
    median = np.median(data)
    mad = np.median(np.abs(data - median))
    sigma = 1.4826 * mad
    return sigma, median


def inject_signal_into_data(data, header, freq_mhz, drift_hz_s, snr):
    """
    Inject a drifting narrowband signal into the data array.
    
    Returns dict with injection parameters.
    """
    fch1 = float(header['fch1'])
    foff = float(header['foff'])
    nchans = int(header['nchans'])
    tsamp = float(header['tsamp'])
    
    sigma, median = estimate_noise(data)
    amplitude = snr * sigma
    
    abs_foff = abs(foff)
    n_tints = data.shape[0]
    
    for t_idx in range(n_tints):
        t_sec = t_idx * tsamp
        current_freq_mhz = freq_mhz + (drift_hz_s * t_sec) / 1e6
        
        chan_idx = int(round((fch1 - current_freq_mhz) / abs_foff))
        
        if chan_idx < 0 or chan_idx >= nchans:
            continue
        
        if data.ndim == 3:
            data[t_idx, 0, chan_idx] += amplitude
            for offset in range(1, 3):
                w = np.exp(-(offset**2) / 2.0)
                if chan_idx - offset >= 0:
                    data[t_idx, 0, chan_idx - offset] += amplitude * w
                if chan_idx + offset < nchans:
                    data[t_idx, 0, chan_idx + offset] += amplitude * w
        elif data.ndim == 2:
            data[t_idx, chan_idx] += amplitude
            for offset in range(1, 3):
                w = np.exp(-(offset**2) / 2.0)
                if chan_idx - offset >= 0:
                    data[t_idx, chan_idx - offset] += amplitude * w
                if chan_idx + offset < nchans:
                    data[t_idx, chan_idx + offset] += amplitude * w
    
    return {
        'freq_mhz': freq_mhz,
        'drift_hz_s': drift_hz_s,
        'snr': snr,
        'amplitude': amplitude,
        'sigma': sigma,
        'nchans': nchans,
        'tsamp': tsamp,
        'n_tints': n_tints,
    }


def patch_h5_data(source_path, dest_path, new_data):
    """
    Copy source HDF5 to dest, then replace the data dataset with new_data.
    Preserves all original structure, attributes, and metadata.
    """
    # Copy the entire file
    shutil.copy2(source_path, dest_path)
    
    # Open the copy and replace data
    # BL stores ALL header fields as attributes ON the 'data' dataset.
    # Must save and restore them when recreating it.
    with h5py.File(dest_path, 'r+') as f:
        old_ds = f['data']
        shape = old_ds.shape
        dtype = old_ds.dtype
        
        # Save all dataset attributes before deleting
        saved_attrs = {k: old_ds.attrs[k] for k in old_ds.attrs}
        
        # Delete and recreate
        del f['data']
        new_ds = f.create_dataset('data', data=new_data, shape=shape,
                                   dtype=dtype, chunks=True)
        
        # Restore all attributes
        for k, v in saved_attrs.items():
            new_ds.attrs[k] = v
    
    return dest_path


def inject_and_save(input_path, output_path, freq_mhz, drift_hz_s, snr):
    """Full pipeline: load, inject, save using file-copy approach."""
    header = load_header_blimpy(input_path)
    data = load_data_blimpy(input_path)
    
    info = inject_signal_into_data(data, header, freq_mhz, drift_hz_s, snr)
    
    patch_h5_data(input_path, output_path, data)
    
    return info


def batch_inject(input_path, out_dir, drifts, snrs, base_freq=2139.0):
    """Inject multiple drift/SNR combos into copies of one file."""
    os.makedirs(out_dir, exist_ok=True)
    basename = os.path.splitext(os.path.basename(input_path))[0]
    
    # Load header and data once
    header = load_header_blimpy(input_path)
    data_orig = load_data_blimpy(input_path)
    
    fch1 = float(header['fch1'])
    foff = float(header['foff'])
    nchans = int(header['nchans'])
    flow = fch1 + foff * (nchans - 1)
    fhigh = fch1
    
    results = []
    for drift, snr in itertools.product(drifts, snrs):
        idx = len(results)
        freq_offset_mhz = ((idx % 100) - 50) * 0.003
        inj_freq = base_freq + freq_offset_mhz
        
        if inj_freq < flow or inj_freq > fhigh:
            inj_freq = (flow + fhigh) / 2
        
        suffix = f"d{drift}_s{snr}"
        out_name = f"{basename}_INJ_{suffix}.h5"
        out_path = os.path.join(out_dir, out_name)
        
        data_mod = data_orig.copy()
        info = inject_signal_into_data(data_mod, header, inj_freq, drift, snr)
        info['output_path'] = out_path
        
        patch_h5_data(input_path, out_path, data_mod)
        
        fname = os.path.basename(out_path)
        print(f"  {fname:<60} drift={drift:<6} snr={snr:<5} freq={inj_freq:.6f} MHz")
        results.append((out_path, info))
    
    return results


def main():
    parser = argparse.ArgumentParser(
        description="Inject synthetic signals into BL HDF5 files for pipeline validation."
    )
    parser.add_argument('input', help='Input .h5 file path')
    parser.add_argument('--freq', type=float, default=2139.0,
                        help='Signal frequency in MHz (default: 2139, band center for Parkes L-band)')
    parser.add_argument('--drift', type=float, default=0.5,
                        help='Drift rate in Hz/s (single mode)')
    parser.add_argument('--snr', type=float, default=15,
                        help='Target SNR (single mode)')
    parser.add_argument('--out', '-o', default='data/injected/',
                        help='Output directory')
    
    parser.add_argument('--batch', action='store_true',
                        help='Batch mode: inject all drift/SNR combinations')
    parser.add_argument('--drifts', type=str,
                        help='Comma-separated drift rates (e.g., 0.01,0.1,0.5,1.0,2.0)')
    parser.add_argument('--snrs', type=str,
                        help='Comma-separated SNR values (e.g., 5,10,15,20,25,30)')
    
    args = parser.parse_args()
    
    if not os.path.exists(args.input):
        print(f"Error: input file not found: {args.input}")
        sys.exit(1)
    
    if args.batch:
        if not args.drifts or not args.snrs:
            parser.error("--batch requires --drifts and --snrs")
        drifts = [float(x) for x in args.drifts.split(',')]
        snrs = [float(x) for x in args.snrs.split(',')]
        
        n_files = len(drifts) * len(snrs)
        print(f"Batch injection: {len(drifts)} drifts x {len(snrs)} SNRs = {n_files} files")
        print(f"Input: {args.input}")
        print(f"Output dir: {args.out}\n")
        
        t0 = time.time()
        results = batch_inject(args.input, args.out, drifts, snrs, base_freq=args.freq)
        elapsed = time.time() - t0
        
        print(f"\nInjected {len(results)} signals in {elapsed:.0f}s")
    else:
        os.makedirs(args.out, exist_ok=True)
        basename = os.path.splitext(os.path.basename(args.input))[0]
        out_name = f"{basename}_INJ_d{args.drift}_s{args.snr}.h5"
        out_path = os.path.join(args.out, out_name)
        
        print(f"Injecting signal into {args.input}")
        print(f"  Freq: {args.freq} MHz")
        print(f"  Drift: {args.drift} Hz/s")
        print(f"  SNR: {args.snr}")
        
        info = inject_and_save(args.input, out_path, args.freq, args.drift, args.snr)
        
        print(f"\nOutput: {out_path}")
        print(f"  Amplitude: {info['amplitude']:.0f}")
        print(f"  Noise sigma: {info['sigma']:.0f}")
        print(f"  Channels: {info['nchans']}")
        print(f"  Time samples: {info['n_tints']}")


if __name__ == "__main__":
    main()
