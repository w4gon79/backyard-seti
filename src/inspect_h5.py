#!/usr/bin/env python3
"""Inspect BL HDF5 file structure via blimpy + raw h5py."""
import sys
import numpy as np
import h5py

filepath = sys.argv[1] if len(sys.argv) > 1 else "data/Parkes_57790_62144_PROXCEN_S_mid.h5"

print("=== Raw h5py inspection ===")
with h5py.File(filepath, 'r') as f:
    print(f"Root keys: {list(f.keys())}")
    
    def show_tree(name, obj):
        if isinstance(obj, h5py.Dataset):
            print(f"  DATASET: {name}  shape={obj.shape}  dtype={obj.dtype}  chunks={obj.chunks}")
    
    f.visititems(show_tree)
    
    print("\n=== Root attributes ===")
    for k in sorted(f.attrs.keys()):
        v = f.attrs[k]
        print(f"  {k} = {v}")
    
    # Try reading data via chunk iteration
    print("\n=== Data read test (first 5 channels of first tint) ===")
    ds = f['data']
    try:
        chunk = ds[0:1, 0:1, 0:5]
        print(f"  data[0:1,0:1,0:5] = {chunk}")
    except Exception as e:
        print(f"  Slice failed: {e}")
        try:
            chunk = np.array(ds[0, 0, 0:5])
            print(f"  data[0,0,0:5] = {chunk}")
        except Exception as e2:
            print(f"  Index failed: {e2}")

print("\n=== blimpy read test ===")
try:
    from blimpy import Waterfall
    wf = Waterfall(filepath, load_data=True)
    print(f"  data shape: {wf.data.shape}")
    print(f"  data dtype: {wf.data.dtype}")
    print(f"  data[0,0,0:5] = {wf.data[0,0,0:5]}")
    print(f"  header keys: {list(wf.header.keys())[:10]}")
    print(f"  n_ints_in_file: {wf.n_ints_in_file}")
    print(f"  file_size_bytes: {wf.file_size_bytes/1e9:.2f} GB")
except Exception as e:
    import traceback
    traceback.print_exc()
