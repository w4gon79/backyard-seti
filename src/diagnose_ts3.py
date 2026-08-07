#!/usr/bin/env python3
"""Check what turbo_seti's H5Reader actually reads from the injected file."""
import numpy as np
from blimpy import Waterfall
from blimpy.io.hdf_reader import H5Reader

inj_path = "data/injected/Parkes_57790_62144_PROXCEN_S_mid_INJ_d0.0_s100.0.h5"
orig_path = "data/Parkes_57790_62144_PROXCEN_S_mid.h5"

# The injection is at channel 152056
# Frequency at that channel: fch1 + foff*152056
fch1 = 2574.03515625
foff = -0.00286102294921875
inj_freq = fch1 + foff * 152056  # ~3009 MHz
print(f"Injection freq: {inj_freq:.6f} MHz")
print(f"Reading a narrow band around {inj_freq-1:.3f}-{inj_freq+1:.3f} MHz")

# Read injected file via H5Reader (same path turbo_seti uses)
reader_inj = H5Reader(inj_path)
reader_orig = H5Reader(orig_path)

print(f"\n=== H5Reader injection file ===")
print(f"  header: {reader_inj.header}")
print(f"  n_ints_in_file: {reader_inj.n_ints_in_file}")

# Read a small chunk around the injection frequency
f_start = inj_freq - 0.5
f_stop = inj_freq + 0.5
print(f"\n  Reading {f_start:.3f}-{f_stop:.3f} MHz...")

data_inj = reader_inj.read_data(f_start=f_start, f_stop=f_stop)
data_orig = reader_orig.read_data(f_start=f_start, f_stop=f_stop)

print(f"  inj data shape: {data_inj.data.shape}")
print(f"  orig data shape: {data_orig.data.shape}")

# Compare
d_inj = np.array(data_inj.data, dtype=np.float64)
d_orig = np.array(data_orig.data, dtype=np.float64)
diff = d_inj - d_orig
nz = np.count_nonzero(diff)
mx = np.max(np.abs(diff)) if diff.size > 0 else 0
print(f"  nonzero diff: {nz}")
print(f"  max diff: {mx:.0f}")

if nz > 0:
    peak_idx = np.unravel_index(np.argmax(np.abs(diff)), diff.shape)
    print(f"  peak at: {peak_idx}")
    print(f"  peak diff: {diff[peak_idx]:.0f}")
    print(f"  H5Reader CAN see the injected signal!")
else:
    print(f"  H5Reader CANNOT see the injected signal!")

# Also try blimpy Waterfall with f_start/f_stop
print(f"\n=== blimpy Waterfall with bandpass filter ===")
wf_inj = Waterfall(inj_path, load_data=True, f_start=f_start, f_stop=f_stop)
wf_orig = Waterfall(orig_path, load_data=True, f_start=f_start, f_stop=f_stop)
print(f"  inj data shape: {wf_inj.data.shape}")
d_inj = np.array(wf_inj.data, dtype=np.float64)
d_orig = np.array(wf_orig.data, dtype=np.float64)
diff = d_inj - d_orig
nz = np.count_nonzero(diff)
print(f"  nonzero diff: {nz}")
if nz > 0:
    print(f"  blimpy CAN see it too")
