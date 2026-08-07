#!/usr/bin/env python3
"""Inject a fake signal with a drift rate that turbo_seti can actually resolve."""
import numpy as np
from blimpy import Waterfall
import h5py
import shutil

src = 'G:/seti/data/Parkes_57910_34684_PROXCEN_S_mid.h5'
dst = 'G:/seti/data/test_signal_v2.h5'

print(f"Copying to {dst}")
shutil.copy2(src, dst)

wf = Waterfall(src, max_load=2)
data = wf.data.astype(np.float64)

n_chans = data.shape[-1]
n_times = data.shape[0]
tsamp = wf.header['tsamp']      # ~1.07s
foff_hz = wf.header['foff']     # ~2861 Hz per channel

# turbo_seti computed drift resolution = 9.58 Hz/s
# We need to use a drift rate that is an EXACT MULTIPLE of this
# So the signal coherently stacks during dedrift
drift_resolution = 9.584659205397037
drift_rate_hz = drift_resolution * 1  # exactly 1 resolution bin = ~9.58 Hz/s

# Pick a channel in a quiet part of the band (avoid DC bins at multiples of 1024)
inject_ch = 150000  # arbitrary, not near a DC bin
freq_mhz = wf.header['fch1'] + wf.header['foff'] * inject_ch
print(f"Injecting at channel {inject_ch} ({freq_mhz:.4f} MHz)")
print(f"Drift rate: {drift_rate_hz:.4f} Hz/s (1x resolution bin)")

# Noise level at this channel
noise_std = np.std(data[:, 0, inject_ch])
noise_mean = np.mean(data[:, 0, inject_ch])
print(f"Noise: mean={noise_mean:.2f}, std={noise_std:.2f}")

# Inject strong signal: SNR ~100
signal_strength = noise_std * 100
print(f"Signal strength: {signal_strength:.2f} (SNR ~100)")

# Compute drift in channels per time sample
drift_per_sample_hz = drift_rate_hz * tsamp
channels_per_sample = drift_per_sample_hz / abs(foff_hz)
print(f"Channels per time sample: {channels_per_sample:.8f}")

# Inject: for each time step, add signal to the drifting channel position
# Use sub-channel interpolation for precise drift
for t in range(n_times):
    drift_offset = t * channels_per_sample
    ch_center = inject_ch + drift_offset
    
    # Signal spans ~3 channels (narrowband but not delta function)
    for offset in range(-1, 2):
        ch = int(round(ch_center)) + offset
        if 0 <= ch < n_chans:
            # Weight: center gets full strength, neighbors get half
            weight = 1.0 if offset == 0 else 0.3
            data[t, 0, ch] += signal_strength * weight

print(f"Injected across {n_times} time samples")

# Write back
print("Writing modified data...")
with h5py.File(dst, 'r+') as f:
    dset = f['data']
    dset[...] = data.astype(dset.dtype)
    print("Written successfully")

print(f"\nDone. Run:")
print(f"  .\\seti-python.bat src\\bl_doppler_search.py data\\test_signal_v2.h5 --out results --snr 10")
