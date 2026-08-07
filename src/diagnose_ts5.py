#!/usr/bin/env python3
"""
Run turbo_seti FindDoppler step by step on the injected file
to find exactly where the detection fails.
"""
import numpy as np
import sys, os
sys.path.insert(0, 'src')

from turbo_seti.find_doppler.find_doppler import FindDoppler
from turbo_seti.find_doppler.data_handler import DATAH5

inj_path = "data/injected/Parkes_57790_62144_PROXCEN_S_mid_INJ_d0.0_s100.0.h5"

print("=== Step 1: Create FindDoppler ===")
doppler = FindDoppler(
    inj_path,
    min_drift=1e-05,
    max_drift=5,
    snr=5,
    out_dir="G:/seti/results/injected",
)

print(f"\n=== Step 2: Check data handle ===")
dh = doppler.data_handle
print(f"  file: {dh.filename}")
print(f"  n_coarse_chan: {dh.n_coarse_chan}")
print(f"  tsteps: {dh.tsteps}")
print(f"  fftlen: {dh.fftlen}")
print(f"  f_start: {dh.f_start}")
print(f"  f_stop: {dh.f_stop}")

print(f"\n=== Step 3: Load data for coarse channel 148 ===")
# FindDoppler.search() iterates over coarse channels
# Let's manually load the data for the channel containing our injection
wf = dh.fil_file

# Read coarse channel 148 (contains our injection at local chan 504)
fch1 = 2574.03515625
foff = 0.00286102294921875
chans_per_cc = 304128 // 297  # 1024
cc = 148
f_start = fch1 + foff * (cc * chans_per_cc)
f_stop = fch1 + foff * ((cc+1) * chans_per_cc)
print(f"  Coarse chan {cc}: {f_start:.6f} - {f_stop:.6f} MHz")

wf.read_data(f_start=f_start, f_stop=f_stop)
data = np.array(wf.data, dtype=np.float64)
print(f"  data shape: {data.shape}")
print(f"  data[0,0,504]: {data[0,0,504]:.0f} (injection channel)")
print(f"  data[0,0,512]: {data[0,0,512]:.0f} (center)")
print(f"  median of tint 0: {np.median(data[0,0,:]):.0f}")
print(f"  Is channel 504 the brightest? {np.argmax(data[0,0,:]) == 504}")
print(f"  max channel: {np.argmax(data[0,0,:])}, max val: {np.max(data[0,0,:]):.0f}")

# Now check: after turbo_seti's internal processing, what happens?
# turbo_seti's search() does:
# 1. Read coarse channel data
# 2. Take FFT of each time slice
# 3. Dechirp at various drift rates
# 4. Threshold

# Let's simulate the FFT step
print(f"\n=== Step 4: Simulate turbo_seti FFT step ===")
# turbo_seti works with the spectra (mean over time? or per-time-sample?)
# From turbo_seti source, it flattens data to (n_tints, n_chans)

spec_data = data[:, 0, :]  # shape: (279, 1024)
print(f"  spec shape: {spec_data.shape}")

# Check if the injection creates a detectable peak
mean_spec = np.mean(spec_data, axis=0)
median = np.median(spec_data)
mad = np.median(np.abs(spec_data - median))
sigma = 1.4826 * mad
peak_val = mean_spec[504]
print(f"  Mean spectrum peak at 504: {peak_val:.0f}")
print(f"  Median: {median:.0f}, sigma: {sigma:.0f}")
print(f"  SNR at 504: {(peak_val - median)/sigma:.1f}")

# Check: is the data in float32 causing precision issues?
print(f"\n=== Step 5: Check float32 overflow ===")
f32_data = data.astype(np.float32)
print(f"  Original dtype: {data.dtype}")
print(f"  float32 max: {np.finfo(np.float32).max:.0f}")
print(f"  data max: {np.max(data):.0f}")
print(f"  Within float32 range: {np.max(data) < np.finfo(np.float32).max}")

# The issue might be that turbo_seti normalizes by dividing by the sum
# If values are huge, the normalized signal might be tiny
total_power = np.sum(spec_data[0])
frac = spec_data[0, 504] / total_power
median_frac = np.median(spec_data[0]) / total_power
print(f"\n  Total power (tint 0): {total_power:.0f}")
print(f"  Fraction at 504: {frac:.2e}")
print(f"  Median fraction: {median_frac:.2e}")
print(f"  Ratio: {frac/median_frac:.1f}x median")
