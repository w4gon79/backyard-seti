#!/usr/bin/env python3
"""Inject a fake narrowband signal into a copy of real BL data to test turbo_seti."""
import numpy as np
from blimpy import Waterfall
import h5py
import shutil
import os

src = 'G:/seti/data/Parkes_57910_34684_PROXCEN_S_mid.h5'
dst = 'G:/seti/data/test_signal_injected.h5'

# Copy the file
print(f"Copying {src} -> {dst}")
shutil.copy2(src, dst)

# Read the data
wf = Waterfall(src, max_load=2)
data = wf.data.astype(np.float64)  # (279, 1, 304128)

# Pick a quiet channel to inject signal
n_chans = data.shape[-1]
n_times = data.shape[0]

# Choose a channel in the middle of the band
inject_ch = n_chans // 2
freq_mhz = wf.header['fch1'] + wf.header['foff'] * inject_ch
print(f"Injecting fake signal at channel {inject_ch} ({freq_mhz:.4f} MHz)")

# Get the noise level at this channel
noise_std = np.std(data[:, 0, inject_ch])
noise_mean = np.mean(data[:, 0, inject_ch])
print(f"  Noise: mean={noise_mean:.2f}, std={noise_std:.2f}")

# Inject a narrowband signal with a small drift rate
# Signal should be well above noise: SNR ~ 50
signal_strength = noise_std * 50
print(f"  Injecting signal: {signal_strength:.2f} amplitude (SNR ~50)")

# Drift rate: 0.5 Hz/s across the observation
# Frequency step per time sample: drift_rate * tsamp
tsamp = wf.header['tsamp']  # ~1.07s
drift_rate_hz = 0.5  # Hz/s
drift_per_sample = drift_rate_hz * tsamp
foff_hz = wf.header['foff']  # Hz per channel (~2861 Hz)
channels_per_sample = drift_per_sample / abs(foff_hz)
print(f"  Drift: {drift_rate_hz} Hz/s = {channels_per_sample:.6f} channels/sample")

# Inject the drifting signal
for t in range(n_times):
    drift_offset = int(t * channels_per_sample)
    ch = inject_ch + drift_offset
    if 0 <= ch < n_chans:
        data[t, 0, ch] += signal_strength

print(f"  Injected across {n_times} time samples")

# Write back to the copy
print(f"Writing modified data to {dst}...")
with h5py.File(dst, 'r+') as f:
    # The dataset key is lowercase 'data'
    if 'data' in f:
        dset = f['data']
        print(f"  Original dataset shape: {dset.shape}, dtype: {dset.dtype}")
        # Write the modified data back
        dset[...] = data.astype(dset.dtype)
        print(f"  Written successfully")
    else:
        print(f"  ERROR: 'data' key not found. Keys: {list(f.keys())}")

print("\nDone. Now run:")
print(f'  .\\seti-python.bat src\\bl_doppler_search.py data\\test_signal_injected.h5 --out results --snr 10')
