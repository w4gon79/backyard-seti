#!/usr/bin/env python3
"""
test_setigen.py - Inject signal using setigen, save, verify with turbo_seti.
"""
import numpy as np
import os
import setigen
from setigen import Frame

input_path = "data/Parkes_57790_62144_PROXCEN_S_mid.h5"
print(f"Loading {input_path} into setigen Frame...")

frame = Frame(waterfall=input_path)
print(f"Frame loaded.")
print(f"  fch1: {frame.fch1}")
print(f"  df: {frame.df}")
print(f"  dt: {frame.dt}")
print(f"  fchans: {frame.fchans}")
print(f"  tchans: {frame.tchans}")
print(f"  data shape: {frame.data.shape}")
print(f"  fmin: {frame.fmin}")
print(f"  fmax: {frame.fmax}")
print(f"  fmid: {frame.fmid}")
print(f"  noise mean: {frame.noise_mean}")
print(f"  noise std: {frame.noise_std}")

# Inject a signal at band center, zero drift, SNR 25
center_freq = frame.fmid
print(f"\nInjecting signal at {center_freq:.3f} Hz, drift=0, SNR=25...")

frame.add_signal(
    path=setigen.constant_path(f_start=center_freq, drift_rate=0.0),
    t_profile=setigen.constant_t_profile(1.0),
    f_profile=setigen.gaussian_f_profile(width=frame.df * 3),
)

print(f"Signal added.")

# Check if signal is now visible
data = np.array(frame.data)
print(f"  data shape: {data.shape}")
print(f"  max value: {np.max(data):.0f}")

# Save to new HDF5
out_path = "data/injected/setigen_test_s25.h5"
os.makedirs("data/injected", exist_ok=True)
frame.save_h5(out_path)
print(f"Saved to {out_path}")

# Verify: load the saved file back
print(f"\n=== Verification: load saved file ===")
frame2 = Frame(waterfall=out_path)
print(f"  data shape: {frame2.data.shape}")
print(f"  noise mean: {frame2.noise_mean}")
print(f"  noise std: {frame2.noise_std}")

# Compare original vs injected
orig_frame = Frame(waterfall=input_path)
diff = np.array(frame2.data, dtype=np.float64) - np.array(orig_frame.data, dtype=np.float64)
nz = np.count_nonzero(diff)
mx = np.max(np.abs(diff))
print(f"  diff nonzero: {nz}, max: {mx}")
if nz > 0:
    peak = np.unravel_index(np.argmax(np.abs(diff)), diff.shape)
    print(f"  peak at: {peak}")
    print(f"  Signal confirmed!")
else:
    print(f"  No diff - signal lost in save!")

# Run turbo_seti on it
print(f"\n=== Running turbo_seti on setigen-injected file ===")
import sys
sys.path.insert(0, 'src')
from bl_doppler_search import run_doppler_search

dat_path = run_doppler_search(out_path, out_dir='results/injected',
                               min_drift=-5, max_drift=5, snr=5)

with open(dat_path) as f:
    lines = f.readlines()
hits = [l for l in lines if not l.startswith('#') and l.strip()]
print(f"\nturbo_seti found {len(hits)} hits")
for h in hits[:10]:
    print(f"  {h.strip()}")
