#!/usr/bin/env python3
"""Debug the injection write path step by step."""
import numpy as np
import h5py
import shutil
import os

orig = "data/Parkes_57790_62144_PROXCEN_S_mid.h5"
test_copy = "data/injected/test_copy.h5"
test_modify = "data/injected/test_modify.h5"

os.makedirs("data/injected", exist_ok=True)

# Step 1: Load data via blimpy
print("=== Step 1: Load via blimpy ===")
from blimpy import Waterfall
wf = Waterfall(orig, load_data=True)
data = np.array(wf.data, dtype=np.float32)
print(f"  Loaded data: shape={data.shape}, dtype={data.dtype}")
print(f"  First 5 values: {data[0,0,:5]}")
print(f"  Mean: {np.mean(data):.0f}, Std: {np.std(data):.0f}")

# Step 2: Modify a known channel
print("\n=== Step 2: Modify data ===")
test_val = 999999999.0
data[0, 0, 100000] = test_val
data[0, 0, 100001] = test_val
print(f"  Set data[0,0,100000] = {data[0,0,100000]}")
print(f"  Set data[0,0,100001] = {data[0,0,100001]}")

# Step 3: Copy file and patch
print("\n=== Step 3: Copy + patch ===")
shutil.copy2(orig, test_copy)

with h5py.File(test_copy, 'r+') as f:
    old_ds = f['data']
    print(f"  Original data shape: {old_ds.shape}, dtype: {old_ds.dtype}")
    
    # Save attrs
    saved_attrs = {k: old_ds.attrs[k] for k in old_ds.attrs}
    print(f"  Saved {len(saved_attrs)} attrs")
    
    # Delete old dataset
    del f['data']
    
    # Create new one
    new_ds = f.create_dataset('data', data=data, shape=data.shape,
                               dtype=data.dtype, chunks=True)
    
    # Restore attrs
    for k, v in saved_attrs.items():
        new_ds.attrs[k] = v
    
    print(f"  Written. New dataset shape: {new_ds.shape}")

# Step 4: Read back and verify
print("\n=== Step 4: Read back via h5py ===")
with h5py.File(test_copy, 'r') as f:
    ds = f['data']
    print(f"  data shape: {ds.shape}, dtype: {ds.dtype}")
    try:
        val = ds[0, 0, 100000]
        print(f"  data[0,0,100000] = {val} (expected {test_val})")
    except Exception as e:
        print(f"  Read failed: {e}")
        # Try reading larger slice
        try:
            chunk = ds[0:1, 0:1, 99999:100002]
            print(f"  Slice read: {chunk}")
        except Exception as e2:
            print(f"  Slice also failed: {e2}")

# Step 5: Read back via blimpy
print("\n=== Step 5: Read back via blimpy ===")
try:
    wf2 = Waterfall(test_copy, load_data=True)
    d2 = np.array(wf2.data, dtype=np.float32)
    print(f"  blimpy data shape: {d2.shape}")
    print(f"  data[0,0,100000] = {d2[0,0,100000]} (expected {test_val})")
    print(f"  data[0,0,100001] = {d2[0,0,100001]} (expected {test_val})")
    print(f"  data[0,0,0:5] = {d2[0,0,:5]}")
except Exception as e:
    import traceback
    traceback.print_exc()

# Cleanup
os.remove(test_copy)
print("\nDone.")
