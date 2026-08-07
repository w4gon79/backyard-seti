#!/usr/bin/env python3
"""Inject test signals at band center with various drift rates."""
import sys, os
sys.path.insert(0, 'src')
from inject_signal import load_header_blimpy, load_data_blimpy, inject_signal_into_data, patch_h5_data

orig = 'data/Parkes_57790_62144_PROXCEN_S_mid.h5'
outdir = 'data/injected'
os.makedirs(outdir, exist_ok=True)

header = load_header_blimpy(orig)
data_orig = load_data_blimpy(orig)

fch1 = float(header['fch1'])
foff = float(header['foff'])
nchans = int(header['nchans'])
center_chan = nchans // 2
center_freq = fch1 + foff * center_chan
print(f'Band center: {center_freq:.3f} MHz (channel {center_chan})')
print(f'Band: {fch1 + foff*(nchans-1):.3f} - {fch1:.3f} MHz')

# Test 1: Zero drift, SNR 25
d = data_orig.copy()
info = inject_signal_into_data(d, header, center_freq, 0.0, 25)
patch_h5_data(orig, f'{outdir}/test_zerodrift_s25.h5', d)
print(f'Zero drift SNR 25: freq={center_freq:.3f}, amp={info["amplitude"]:.0f}')

# Test 2: 9.58 Hz/s drift (exact turbo_seti bin), SNR 25
d = data_orig.copy()
info = inject_signal_into_data(d, header, center_freq, 9.58, 25)
patch_h5_data(orig, f'{outdir}/test_bindrift_s25.h5', d)
print(f'Bin drift SNR 25: freq={center_freq:.3f}, amp={info["amplitude"]:.0f}')

# Test 3: Zero drift, very high SNR 100
d = data_orig.copy()
info = inject_signal_into_data(d, header, center_freq, 0.0, 100)
patch_h5_data(orig, f'{outdir}/test_zerodrift_s100.h5', d)
print(f'Zero drift SNR 100: freq={center_freq:.3f}, amp={info["amplitude"]:.0f}')

# Test 4: Zero drift, SNR 10
d = data_orig.copy()
info = inject_signal_into_data(d, header, center_freq, 0.0, 10)
patch_h5_data(orig, f'{outdir}/test_zerodrift_s10.h5', d)
print(f'Zero drift SNR 10: freq={center_freq:.3f}, amp={info["amplitude"]:.0f}')

print('\nDone. Run turbo_seti on each to test detection.')
