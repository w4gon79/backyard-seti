#!/usr/bin/env python3
"""Compare what turbo_seti's DATAH5 sees vs what blimpy sees for injected file."""
import numpy as np
from blimpy import Waterfall
from turbo_seti.find_doppler.data_handler import DATAH5

inj_path = "data/injected/Parkes_57790_62144_PROXCEN_S_mid_INJ_d0.0_s100.0.h5"
orig_path = "data/Parkes_57790_62144_PROXCEN_S_mid.h5"

# Blimpy view (confirmed working)
print("=== blimpy view ===")
wf_inj = Waterfall(inj_path, load_data=True)
wf_orig = Waterfall(orig_path, load_data=True)
print(f"  inj data[0,0,152056] = {wf_inj.data[0,0,152056]:.0f}")
print(f"  orig data[0,0,152056] = {wf_orig.data[0,0,152056]:.0f}")

# turbo_seti DATAH5 view
print("\n=== turbo_seti DATAH5 view ===")
h5_inj = DATAH5(inj_path, kernels=None, gpu_id=0)
h5_orig = DATAH5(orig_path, kernels=None, gpu_id=0)

print(f"  inj fil_file type: {type(h5_inj.fil_file).__name__}")
print(f"  inj fil_file.data type: {type(h5_inj.fil_file.data).__name__}")

# Access the internal Waterfall object's data
wf_ts_inj = h5_inj.fil_file
wf_ts_orig = h5_orig.fil_file

print(f"  inj data shape: {wf_ts_inj.data.shape}")
print(f"  inj data dtype: {wf_ts_inj.data.dtype}")

# Key question: does turbo_seti's internal Waterfall see the injection?
if hasattr(wf_ts_inj, 'data') and wf_ts_inj.data is not None:
    d_inj = np.array(wf_ts_inj.data)
    d_orig = np.array(wf_ts_orig.data)
    diff = d_inj - d_orig
    nz = np.count_nonzero(diff)
    print(f"  inj data[0,0,152056] = {d_inj[0,0,152056]:.0f}")
    print(f"  orig data[0,0,152056] = {d_orig[0,0,152056]:.0f}")
    print(f"  diff[0,0,152056] = {diff[0,0,152056]:.0f}")
    print(f"  nonzero diff: {nz}")
    if nz == 0:
        print("  *** turbo_seti CANNOT see the injected signal! ***")
    else:
        print("  *** turbo_seti CAN see the injected signal ***")
else:
    print("  data is None or not loaded")

# Check if turbo_seti loads data lazily
print(f"\n  inj header: {dict(h5_inj.header)}")
print(f"  n_coarse_chan: {h5_inj.n_coarse_chan}")
print(f"  tsteps: {h5_inj.tsteps}")
print(f"  fftlen: {h5_inj.fftlen}")
