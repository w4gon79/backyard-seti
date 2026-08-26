"""Smoke test the pipeline on the GBT file: extract one 732 kHz sub-band
and run turboSETI on it. Exactly what fine_res_pipeline does per chunk,
just one window, low drift cap for speed."""
import os
import sys
import time

sys.path.insert(0, r'G:\seti\src')

SRC = r'G:\seti\data\fine\spliced_blc0001020304050607_guppi_57532_03953_GJ447_0011.gpuspec.0000.h5'
OUT = r'G:\seti\results\_gbt_smoke'
os.makedirs(OUT, exist_ok=True)

from fine_res_pipeline import extract_sub_band  # noqa: E402

# quiet part of the band, ~2100 MHz
F0, F1 = 2100.0, 2100.732
t0 = time.time()
sub_path = os.path.join(OUT, 'smoke_sub.h5')
n, fch1, foff = extract_sub_band(SRC, F0, F1, sub_path)
print(f'extract: {n} chans, fch1={fch1:.6f}, foff={foff:.3e}, '
      f'{time.time()-t0:.1f}s, size={os.path.getsize(sub_path)/1e6:.1f} MB')

import numpy as np
import h5py
with h5py.File(sub_path, 'r') as f:
    d = f['data'][:]
print(f'sub-band data shape: {d.shape}, dtype={d.dtype}, '
      f'finite={np.isfinite(d).all()}, mean={float(np.mean(d)):.3e}')

t0 = time.time()
from turbo_seti.find_doppler.find_doppler import FindDoppler  # noqa: E402
fd = FindDoppler(sub_path, max_drift=1.0, snr_floor=5.0)
fd.search()
print(f'turboSETI: {time.time()-t0:.1f}s')

dat = sub_path.replace('.h5', '.dat')
log = sub_path.replace('.h5', '.log')
print('dat exists:', os.path.isfile(dat), 'log exists:', os.path.isfile(log))
if os.path.isfile(dat):
    print('--- hits ---')
    with open(dat) as f:
        print(f.read())
if os.path.isfile(log):
    tail = open(log).read().strip().splitlines()
    print('--- log tail ---')
    print('\n'.join(tail[-6:]))
