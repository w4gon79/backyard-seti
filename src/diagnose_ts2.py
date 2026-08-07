#!/usr/bin/env python3
"""Deep dive: what does turbo_seti's DATAH5 actually load?"""
import numpy as np
from turbo_seti.find_doppler.data_handler import DATAH5

inj_path = "data/injected/Parkes_57790_62144_PROXCEN_S_mid_INJ_d0.0_s100.0.h5"

h5 = DATAH5(inj_path, kernels=None, gpu_id=0)
wf = h5.fil_file

print(f"Waterfall object: {type(wf).__name__}")
print(f"  .data shape: {wf.data.shape}")
print(f"  .data dtype: {wf.data.dtype}")
print(f"  .data[:10]: {wf.data[:10]}")
print(f"  .n_ints_in_file: {wf.n_ints_in_file}")
print(f"  .file_size_bytes: {wf.file_size_bytes}")
print(f"  .container type: {type(wf.container).__name__}")

# The Waterfall created by DATAH5 might not load data in __init__
# It might use load_data() or read_data() to actually populate data
print(f"\n  Has load_data: {hasattr(wf, 'load_data')}")
print(f"  Has read_data: {hasattr(wf, 'read_data')}")

# Try the get_spectra method
print(f"\n=== get_spectra ===")
result = h5.get_spectra()
print(f"  result type: {type(result).__name__}")
if isinstance(result, tuple):
    print(f"  tuple length: {len(result)}")
    for i, r in enumerate(result):
        print(f"  [{i}] type={type(r).__name__}, shape={getattr(r, 'shape', 'N/A')}")
        if hasattr(r, '__len__') and not hasattr(r, 'shape'):
            print(f"      value: {r}")
