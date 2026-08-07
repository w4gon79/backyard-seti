#!/usr/bin/env python3
"""
inject_signal_v2.py - Inject signals by creating new HDF5 files from scratch
using blimpy's own writer, avoiding the patch problem.

Strategy: Load data with blimpy, modify in memory, then write to a new file
using a format blimpy can definitely read back.
"""

import sys, os, shutil
import numpy as np
import h5py

def inject_and_write(input_path, output_path, freq_mhz, drift_hz_s, snr):
    """Load via blimpy, inject signal, write via h5py from scratch."""
    from blimpy import Waterfall
    
    # Load original
    wf = Waterfall(input_path, load_data=True)
    data = np.array(wf.data, dtype=np.float32)
    header = wf.header
    
    fch1 = float(header['fch1'])
    foff = float(header['foff'])
    nchans = int(header['nchans'])
    tsamp = float(header['tsamp'])
    
    # Noise estimate
    sigma = 1.4826 * np.median(np.abs(data - np.median(data)))
    amplitude = snr * sigma
    
    # Find injection channel
    chan_idx = int(round((fch1 - freq_mhz) / abs(foff)))
    print(f"  Target freq: {freq_mhz:.6f} MHz -> channel {chan_idx}")
    print(f"  Actual freq: {fch1 + foff*chan_idx:.6f} MHz")
    
    if chan_idx < 0 or chan_idx >= nchans:
        print(f"  ERROR: channel {chan_idx} out of range [0, {nchans})")
        print(f"  Band: {fch1 + foff*(nchans-1):.3f} - {fch1:.3f} MHz")
        return None
    
    # Inject signal
    n_tints = data.shape[0]
    for t_idx in range(n_tints):
        t_sec = t_idx * tsamp
        current_freq_mhz = freq_mhz + (drift_hz_s * t_sec) / 1e6
        ch = int(round((fch1 - current_freq_mhz) / abs(foff)))
        if ch < 0 or ch >= nchans:
            continue
        data[t_idx, 0, ch] += amplitude
        for offset in range(1, 3):
            w = np.exp(-(offset**2) / 2.0)
            if ch - offset >= 0:
                data[t_idx, 0, ch - offset] += amplitude * w
            if ch + offset < nchans:
                data[t_idx, 0, ch + offset] += amplitude * w
    
    print(f"  Injected. Checking data[{0},0,{chan_idx}]: before={np.array(wf.data[0,0,chan_idx]):.0f} after={data[0,0,chan_idx]:.0f}")
    
    # Write to HDF5 from scratch, copying the BL format exactly
    with h5py.File(output_path, 'w') as f:
        # Create data dataset with all header attrs ON it (BL format)
        ds = f.create_dataset('data', data=data, chunks=(5, 1, min(4752, nchans)))
        
        # Copy ALL header fields as dataset attributes
        for key in header:
            val = header[key]
            # Handle astropy quantities
            if hasattr(val, 'value'):
                val = val.value
            if isinstance(val, np.ndarray) and val.size == 1:
                val = val.item()
            try:
                ds.attrs[key] = val
            except (TypeError, ValueError):
                ds.attrs[key] = str(val)
        
        # Root attrs
        f.attrs['CLASS'] = np.bytes_('FILTERBANK')
        f.attrs['VERSION'] = np.bytes_('1.0')
        
        # Mask (empty, matching BL format)
        mask_shape = list(data.shape)
        mask_shape[-1] = int(mask_shape[-1] * 1.293)
        mask = np.zeros(mask_shape, dtype=np.uint8)
        f.create_dataset('mask', data=mask, chunks=(5, 1, min(12288, mask_shape[-1])))
    
    print(f"  Written to {output_path}")
    
    # VERIFY: read it back and check
    wf2 = Waterfall(output_path, load_data=True)
    d2 = np.array(wf2.data, dtype=np.float32)
    print(f"  Verify: data shape={d2.shape}")
    print(f"  Verify: data[0,0,{chan_idx}] = {d2[0,0,chan_idx]:.0f} (should be ~{data[0,0,chan_idx]:.0f})")
    
    wf_orig = Waterfall(input_path, load_data=True)
    d_orig = np.array(wf_orig.data, dtype=np.float32)
    diff = d2 - d_orig
    nonzero = np.count_nonzero(diff)
    max_diff = np.max(np.abs(diff))
    print(f"  Verify: diff nonzero={nonzero}, max={max_diff:.0f}")
    
    if nonzero == 0:
        print("  *** WARNING: Signal NOT present in written file! ***")
        return None
    
    return {
        'freq_mhz': freq_mhz,
        'drift_hz_s': drift_hz_s,
        'snr': snr,
        'channel': chan_idx,
        'amplitude': amplitude,
    }


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description="Inject signal into BL HDF5 (v2, from-scratch writer)")
    parser.add_argument('input', help='Input .h5 file')
    parser.add_argument('--freq', type=float, default=2139.0, help='Frequency MHz')
    parser.add_argument('--drift', type=float, default=0.0, help='Drift Hz/s')
    parser.add_argument('--snr', type=float, default=25.0, help='SNR')
    parser.add_argument('--out', '-o', default='data/injected/', help='Output dir')
    args = parser.parse_args()
    
    os.makedirs(args.out, exist_ok=True)
    basename = os.path.splitext(os.path.basename(args.input))[0]
    out_name = f"{basename}_INJ_d{args.drift}_s{args.snr}.h5"
    out_path = os.path.join(args.out, out_name)
    
    print(f"Injecting into {args.input}")
    info = inject_and_write(args.input, out_path, args.freq, args.drift, args.snr)
    if info:
        print(f"\nSuccess!")
    else:
        print(f"\nFAILED!")
