#!/usr/bin/env python3
"""Check foff sign in original vs injected files."""
from blimpy import Waterfall
import h5py

for label, path in [
    ("ORIG", "data/Parkes_57790_62144_PROXCEN_S_mid.h5"),
    ("INJ", "data/injected/Parkes_57790_62144_PROXCEN_S_mid_INJ_d0.0_s100.0.h5"),
]:
    print(f"\n=== {label}: {path} ===")
    
    # Raw h5py
    with h5py.File(path, 'r') as f:
        ds = f['data']
        fch1_raw = float(ds.attrs['fch1'])
        foff_raw = float(ds.attrs['foff'])
        print(f"  Raw h5py: fch1={fch1_raw}, foff={foff_raw}")
    
    # blimpy
    wf = Waterfall(path, load_data=False)
    fch1 = float(wf.header['fch1'])
    foff = float(wf.header['foff'])
    nchans = int(wf.header['nchans'])
    print(f"  blimpy:   fch1={fch1}, foff={foff}")
    print(f"  band: {min(fch1, fch1+foff*(nchans-1)):.3f} - {max(fch1, fch1+foff*(nchans-1)):.3f} MHz")
