#!/usr/bin/env python3
"""
verify_injection.py - Check that an injected signal is actually visible
in the data, and diagnose why turbo_seti might miss it.
"""
import sys
import numpy as np
from blimpy import Waterfall

orig_path = sys.argv[1] if len(sys.argv) > 1 else "data/Parkes_57790_62144_PROXCEN_S_mid.h5"
inj_path = sys.argv[2] if len(sys.argv) > 2 else "data/injected/Parkes_57790_62144_PROXCEN_S_mid_INJ_d0.5_s15.0.h5"

print("Loading original...")
wf_orig = Waterfall(orig_path, load_data=True)
print("Loading injected...")
wf_inj = Waterfall(inj_path, load_data=True)

d_orig = np.array(wf_orig.data, dtype=np.float64)
d_inj = np.array(wf_inj.data, dtype=np.float64)

# Compute the difference
diff = d_inj - d_orig
print(f"\nData shapes: orig={d_orig.shape}, inj={d_inj.shape}, diff={diff.shape}")
print(f"Max |diff|: {np.max(np.abs(diff)):.0f}")
print(f"Non-zero diff pixels: {np.count_nonzero(diff)}")
print(f"Total pixels: {diff.size}")

# Find where the signal was injected
# Look at the first time sample
t0_diff = np.abs(diff[0, 0, :])
top_chans = np.argsort(t0_diff)[-20:][::-1]

header = wf_inj.header
fch1 = float(header['fch1'])
foff = float(header['foff'])
nchans = int(header['nchans'])

print(f"\n=== Signal location (first time sample) ===")
for i, ch in enumerate(top_chans[:10]):
    freq_mhz = fch1 + foff * ch
    print(f"  Channel {ch}: {freq_mhz:.6f} MHz, diff={diff[0,0,ch]:.0f}")

# Track the signal across time samples
print(f"\n=== Signal track across time ===")
# For each time sample, find the peak channel in the diff
for t in range(min(20, d_orig.shape[0])):
    t_diff = np.abs(diff[t, 0, :])
    peak_ch = np.argmax(t_diff)
    peak_val = t_diff[peak_ch]
    freq_mhz = fch1 + foff * peak_ch
    if peak_val > 0:
        print(f"  t={t:3d}: chan={peak_ch}, freq={freq_mhz:.6f} MHz, amp={peak_val:.0f}")

# Check a few more time samples spread across the observation
n_tints = d_orig.shape[0]
print(f"\n=== Signal track (every 50th sample) ===")
for t in range(0, n_tints, max(1, n_tints // 10)):
    t_diff = np.abs(diff[t, 0, :])
    peak_ch = np.argmax(t_diff)
    peak_val = t_diff[peak_ch]
    freq_mhz = fch1 + foff * peak_ch
    t_sec = t * float(header['tsamp'])
    expected_freq = 1420.405 + (0.5 * t_sec) / 1e6
    print(f"  t={t:3d} ({t_sec:.1f}s): chan={peak_ch}, freq={freq_mhz:.6f} MHz, "
          f"expected={expected_freq:.6f}, amp={peak_val:.0f}")

# Check signal SNR in integrated spectrum
print(f"\n=== Integrated spectrum check ===")
spec_orig = np.mean(d_orig[:, 0, :], axis=0)
spec_inj = np.mean(d_inj[:, 0, :], axis=0)
spec_diff = spec_inj - spec_orig

noise_sigma = np.std(spec_diff[np.abs(spec_diff) < np.max(spec_diff) * 0.1])
peak_chan = np.argmax(spec_diff)
peak_freq = fch1 + foff * peak_chan
peak_power = spec_diff[peak_chan]

print(f"Peak channel: {peak_chan} ({peak_freq:.6f} MHz)")
print(f"Peak power in integrated diff: {peak_power:.0f}")
print(f"Noise sigma in diff: {noise_sigma:.0f}")
print(f"Effective SNR in integrated spectrum: {peak_power / noise_sigma:.1f}" if noise_sigma > 0 else "SNR: inf")
