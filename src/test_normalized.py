#!/usr/bin/env python3
"""
test_normalized.py - Test if data scaling fixes turbo_seti detection.
The raw BL data values are 10^7-10^12. turbo_seti's FFT/dechirp pipeline
likely overflows in float32. Test by scaling down before search.
"""
import numpy as np
import os
import h5py
import shutil
from blimpy import Waterfall

input_path = "data/Parkes_57790_62144_PROXCEN_S_mid.h5"
inj_path = "data/injected/setigen_v2_s25.h5"
out_dir = "data/injected"
os.makedirs(out_dir, exist_ok=True)

# Take the setigen-injected file (confirmed has signal at SNR 124 in integrated spectrum)
# and create a scaled-down version
out_path = os.path.join(out_dir, "setigen_v2_scaled.h5")

print("=== Creating scaled copy of injected file ===")
shutil.copy2(inj_path, out_path)

# Read the data via blimpy
wf = Waterfall(inj_path, load_data=True)
data = np.array(wf.data, dtype=np.float64)
header = wf.header

print(f"  Original data: min={np.min(data):.0f}, max={np.max(data):.0f}, mean={np.mean(data):.0f}")

# Scale down by dividing by 1e6
scale = 1e6
data_scaled = (data / scale).astype(np.float32)
print(f"  Scaled data:   min={np.min(data_scaled):.0f}, max={np.max(data_scaled):.0f}, mean={np.mean(data_scaled):.0f}")

# Write back using setigen's save path (which produces valid HDF5)
# Use a Frame initialized from the waterfall
import setigen
frame = setigen.Frame(waterfall=inj_path)
frame.data = data_scaled[:, 0, :]  # Frame data is (tchans, fchans)
frame.save_h5(out_path)
print(f"  Saved scaled file to {out_path}")

# Verify
wf2 = Waterfall(out_path, load_data=True)
print(f"  Reload: min={np.min(wf2.data):.0f}, max={np.max(wf2.data):.0f}")

# Run turbo_seti
print(f"\n=== Running turbo_seti on scaled data ===")
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

if len(hits) == 0:
    print("\n*** Still zero hits. The problem is NOT float overflow. ***")
    print("The signal is confirmed at SNR 124 in the integrated spectrum.")
    print("turbo_seti's dechirp/FFT pipeline is where it gets lost.")
else:
    print(f"\n*** {len(hits)} HITS! Scaling was the fix! ***")
