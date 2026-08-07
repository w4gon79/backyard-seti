#!/usr/bin/env python3
"""
Diagnose why turbo_seti can't see injected signals.
Compare how blimpy loads original vs injected files, and check
what turbo_seti's data handler actually sees.
"""
import sys
import numpy as np

orig_path = "data/Parkes_57790_62144_PROXCEN_S_mid.h5"
inj_path = "data/injected/test_zerodrift_s100.h5"

print("=== Loading both files via blimpy ===")
from blimpy import Waterfall

wf_orig = Waterfall(orig_path, load_data=True)
wf_inj = Waterfall(inj_path, load_data=True)

d_orig = np.array(wf_orig.data, dtype=np.float64)
d_inj = np.array(wf_inj.data, dtype=np.float64)

print(f"Original: shape={d_orig.shape}, dtype={d_orig.dtype}")
print(f"  min={np.min(d_orig):.0f}, max={np.max(d_orig):.0f}, mean={np.mean(d_orig):.0f}")
print(f"Injected: shape={d_inj.shape}, dtype={d_inj.dtype}")
print(f"  min={np.min(d_inj):.0f}, max={np.max(d_inj):.0f}, mean={np.mean(d_inj):.0f}")

# Check the difference at the injection point
diff = d_inj - d_orig
print(f"\nDiff: max={np.max(diff):.0f}, nonzero={np.count_nonzero(diff)}")

# Find the injection channel
t0_diff = np.abs(diff[0, 0, :])
peak_ch = np.argmax(t0_diff)
print(f"Peak diff channel: {peak_ch}")
print(f"  orig val: {d_orig[0,0,peak_ch]:.0f}")
print(f"  inj  val: {d_inj[0,0,peak_ch]:.0f}")
print(f"  diff:     {diff[0,0,peak_ch]:.0f}")

# Now check what turbo_seti's data handler sees
print("\n=== turbo_seti data handler comparison ===")
from turbo_seti.find_doppler.data_handler import DATAHandle

print("\nOriginal file via turbo_seti DATAHandle:")
try:
    dh_orig = DATAHandle(orig_path, kernels=None, gpu_id=0)
    print(f"  n_coarse_chan: {dh_orig.n_coarse_chan}")
    print(f"  data_shape: {dh_orig.data_shape}")
    print(f"  source_name: {dh_orig.source_name}")
    print(f"  f_start: {dh_orig.f_start}")
    print(f"  f_stop: {dh_orig.f_stop}")
    # Try to get a spectrum
    if hasattr(dh_orig, 'spec'):
        print(f"  spec shape: {dh_orig.spec.shape if dh_orig.spec is not None else 'None'}")
except Exception as e:
    import traceback
    traceback.print_exc()

print("\nInjected file via turbo_seti DATAHandle:")
try:
    dh_inj = DATAHandle(inj_path, kernels=None, gpu_id=0)
    print(f"  n_coarse_chan: {dh_inj.n_coarse_chan}")
    print(f"  data_shape: {dh_inj.data_shape}")
    print(f"  source_name: {dh_inj.source_name}")
    print(f"  f_start: {dh_inj.f_start}")
    print(f"  f_stop: {dh_inj.f_stop}")
except Exception as e:
    import traceback
    traceback.print_exc()

# Check if turbo_seti's internal loading differs
print("\n=== Direct DATAH5 comparison ===")
from turbo_seti.find_doppler.data_handler import DATAH5

print("\nOriginal via DATAH5:")
try:
    h5_orig = DATAH5(orig_path, kernels=None, gpu_id=0)
    print(f"  n_coarse_chan: {h5_orig.n_coarse_chan}")
    print(f"  data_shape: {h5_orig.data_shape}")
    # Get the first spectrum
    spec_orig = h5_orig.get_spectra()
    print(f"  spectra type: {type(spec_orig)}")
    if isinstance(spec_orig, tuple):
        s = spec_orig[0]
    else:
        s = spec_orig
    if hasattr(s, 'shape'):
        print(f"  spectra shape: {s.shape}")
        # Check the injection channel area
        mid = s.shape[-1] // 2 if s.ndim > 0 else 0
        print(f"  spectra[mid-2:mid+3]: {s[..., max(0,mid-2):mid+3]}")
except Exception as e:
    import traceback
    traceback.print_exc()

print("\nInjected via DATAH5:")
try:
    h5_inj = DATAH5(inj_path, kernels=None, gpu_id=0)
    print(f"  n_coarse_chan: {h5_inj.n_coarse_chan}")
    print(f"  data_shape: {h5_inj.data_shape}")
    spec_inj = h5_inj.get_spectra()
    if isinstance(spec_inj, tuple):
        s = spec_inj[0]
    else:
        s = spec_inj
    if hasattr(s, 'shape'):
        print(f"  spectra shape: {s.shape}")
        mid = s.shape[-1] // 2 if s.ndim > 0 else 0
        print(f"  spectra[mid-2:mid+3]: {s[..., max(0,mid-2):mid+3]}")
except Exception as e:
    import traceback
    traceback.print_exc()
