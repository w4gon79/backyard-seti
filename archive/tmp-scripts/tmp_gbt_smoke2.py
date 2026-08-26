"""Fix the smoke window: foff is NEGATIVE (flipped band), fch1 is the TOP
(1926.27 MHz). Coverage: fch1 + foff*(nchans-1) = 1023.87 MHz. So the band
is 1023.9-1926.3 MHz (upside down). Pick a window inside, e.g. 1500 MHz."""
import os
import sys
import time

sys.path.insert(0, r'G:\seti\src')

SRC = r'G:\seti\data\fine\spliced_blc0001020304050607_guppi_57532_03953_GJ447_0011.gpuspec.0000.h5'
OUT = r'G:\seti\results\_gbt_smoke'
os.makedirs(OUT, exist_ok=True)

from fine_res_pipeline import extract_sub_band  # noqa: E402

F0, F1 = 1500.0, 1500.732
t0 = time.time()
sub_path = os.path.join(OUT, 'smoke_sub.h5')
n, fch1, foff = extract_sub_band(SRC, F0, F1, sub_path)
print(f'extract: {n} chans, fch1={fch1:.6f}, foff={foff:.3e}, '
      f'{time.time()-t0:.1f}s, size={os.path.getsize(sub_path)/1e6:.1f} MB')

import numpy as np
import h5py
with h5py.File(sub_path, 'r') as f:
    d = f['data'][:]
print(f'sub-band shape: {d.shape}, finite={np.isfinite(d).all()}, '
      f'mean={float(np.mean(d)):.3e}, max={float(np.max(d)):.3e}')

t0 = time.time()
from turbo_seti.find_doppler.find_doppler import FindDoppler  # noqa: E402
fd = FindDoppler(sub_path, max_drift=1.0, snr=5.0, out_dir='.')
fd.search()
print(f'turboSETI: {time.time()-t0:.1f}s')

dat = sub_path.replace('.h5', '.dat')
print('--- hits (.dat) ---' if os.path.isfile(dat) else 'NO .dat produced')
if os.path.isfile(dat):
    print(open(dat).read())
log = sub_path.replace('.h5', '.log')
if os.path.isfile(log):
    tail = open(log).read().strip().splitlines()
    print('--- log tail ---')
    print('\n'.join(tail[-6:]))
