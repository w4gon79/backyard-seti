#!/usr/bin/env python3
"""Debug: compare original vs injected HDF5 structure."""
import h5py
import sys

orig = sys.argv[1] if len(sys.argv) > 1 else "data/Parkes_57790_62144_PROXCEN_S_mid.h5"
inj = sys.argv[2] if len(sys.argv) > 2 else "data/injected/Parkes_57790_62144_PROXCEN_S_mid_INJ_d0.5_s15.0.h5"

for label, path in [("ORIGINAL", orig), ("INJECTED", inj)]:
    print(f"\n=== {label}: {path} ===")
    try:
        with h5py.File(path, 'r') as f:
            print(f"  Keys: {list(f.keys())}")
            for k in f.attrs:
                v = f.attrs[k]
                print(f"  attr {k} = {v} (type={type(v).__name__})")
            if 'data' in f:
                ds = f['data']
                print(f"  data: shape={ds.shape} dtype={ds.dtype} chunks={ds.chunks}")
    except Exception as e:
        print(f"  ERROR: {e}")
    
    try:
        from blimpy import Waterfall
        wf = Waterfall(path, load_data=False)
        print(f"  blimpy header keys: {sorted(wf.header.keys())}")
        print(f"  blimpy nifs: {wf.header.get('nifs', 'MISSING')}")
    except Exception as e:
        print(f"  blimpy ERROR: {e}")
