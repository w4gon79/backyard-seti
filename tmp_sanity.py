#!/usr/bin/env python3
"""Sanity check: inspect raw data values to verify the pipeline isn't broken."""
from blimpy import Waterfall
import numpy as np

wf = Waterfall('G:/seti/data/Parkes_57910_34684_PROXCEN_S_mid.h5', max_load=0.5)
print(f"Source: {wf.header.get('source_name')}")
print(f"Channels: {wf.header.get('nchans')}")
print(f"Data shape: {wf.data.shape}")
print(f"Data type: {wf.data.dtype}")

data = wf.data
print(f"\nData statistics:")
print(f"  Min:    {np.nanmin(data):.6f}")
print(f"  Max:    {np.nanmax(data):.6f}")
print(f"  Mean:   {np.nanmean(data):.6f}")
print(f"  Median: {np.nanmedian(data):.6f}")
print(f"  Std:    {np.nanstd(data):.6f}")

if np.nanstd(data) < 1e-10:
    print("\nWARNING: Data is all zeros or constant!")
elif np.nanmax(data) - np.nanmin(data) < 1e-6:
    print("\nWARNING: Almost no dynamic range!")
else:
    print("\nData has normal variation.")

# Check for any bright channels (potential signals)
if len(data.shape) == 3:
    col_max = np.nanmax(data[:, 0, :], axis=0)
    median_val = np.nanmedian(col_max)
    bright_threshold = median_val + 10 * np.nanstd(col_max)
    bright_chans = np.where(col_max > bright_threshold)[0]
    print(f"\n  Median channel max: {median_val:.6f}")
    print(f"  10-sigma threshold: {bright_threshold:.6f}")
    print(f"  Bright channels (>10sigma): {len(bright_chans)}")
    if len(bright_chans) > 0:
        print(f"  First 10 bright channels: {bright_chans[:10]}")
        f_ch1 = wf.header.get('fch1', 0)
        f_off = wf.header.get('foff', 0)
        for ch in bright_chans[:5]:
            freq = f_ch1 + f_off * ch
            print(f"    Channel {ch}: {freq:.4f} MHz, val={col_max[ch]:.6f}")
