#!/usr/bin/env python3
"""Final check: what does turbo_seti actually read at the injection channel?"""
import numpy as np
from turbo_seti.find_doppler.data_handler import DATAH5

inj_path = "data/injected/Parkes_57790_62144_PROXCEN_S_mid_INJ_d0.0_s100.0.h5"
orig_path = "data/Parkes_57790_62144_PROXCEN_S_mid.h5"

print("=== Creating DATAH5 objects ===")
h5_inj = DATAH5(inj_path, kernels=None, gpu_id=0)
h5_orig = DATAH5(orig_path, kernels=None, gpu_id=0)

# turbo_seti reads data through the internal Waterfall's container
# Let's use the same read_data call it uses
wf_inj = h5_inj.fil_file
wf_orig = h5_orig.fil_file

print(f"\n=== Internal Waterfall info ===")
print(f"inj file: {wf_inj.filename}")
print(f"inj container type: {type(wf_inj.container).__name__}")
print(f"inj container file_path: {wf_inj.container.filename}")

# Read data through the container (same path turbo_seti uses internally)
# turbo_seti calls read_data during search() via load_data
# Let's try reading the coarse channel that has the injection
# Coarse channel 148 spans channels 151552-152576
# In frequency, that's:
fch1 = 2574.03515625
foff = 0.00286102294921875
ch_start = 151552
ch_end = 152576
f_start = fch1 + foff * ch_start
f_stop = fch1 + foff * ch_end
print(f"\n=== Reading coarse chan 148: {f_start:.3f}-{f_stop:.3f} MHz ===")

wf_inj.read_data(f_start=f_start, f_stop=f_stop)
wf_orig.read_data(f_start=f_start, f_stop=f_stop)

print(f"inj data shape after read: {wf_inj.data.shape}")
print(f"orig data shape after read: {wf_orig.data.shape}")

if wf_inj.data.size > 0 and wf_orig.data.size > 0:
    d_inj = np.array(wf_inj.data, dtype=np.float64)
    d_orig = np.array(wf_orig.data, dtype=np.float64)
    
    # Check the middle channel (where injection should be)
    mid = d_inj.shape[-1] // 2
    print(f"\nMiddle channel ({mid}):")
    print(f"  inj:  {d_inj[0, 0, mid]:.0f}")
    print(f"  orig: {d_orig[0, 0, mid]:.0f}")
    print(f"  diff: {d_inj[0, 0, mid] - d_orig[0, 0, mid]:.0f}")
    
    # Find max diff
    diff = d_inj - d_orig
    nz = np.count_nonzero(diff)
    mx = np.max(np.abs(diff))
    print(f"\n  nonzero diff: {nz}")
    print(f"  max |diff|: {mx:.0f}")
    
    if nz > 0:
        peak = np.unravel_index(np.argmax(np.abs(diff)), diff.shape)
        print(f"  peak location: {peak}")
        print(f"  peak diff: {diff[peak]:.0f}")
else:
    print("  No data loaded!")
