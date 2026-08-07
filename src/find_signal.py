#!/usr/bin/env python3
"""Find the injected signal in the spectrum and compare freq mapping."""
import numpy as np
from blimpy import Waterfall

orig_path = "data/Parkes_57790_62144_PROXCEN_S_mid.h5"
inj_path = "data/injected/test_zerodrift_s100.h5"

wf_orig = Waterfall(orig_path, load_data=True)
wf_inj = Waterfall(inj_path, load_data=True)

d_orig = np.array(wf_orig.data, dtype=np.float64)
d_inj = np.array(wf_inj.data, dtype=np.float64)

# Integrated spectra (mean over time)
spec_orig = np.mean(d_orig[:, 0, :], axis=0)
spec_inj = np.mean(d_inj[:, 0, :], axis=0)
diff = spec_inj - spec_orig

# Find the peak in the diff
peak_ch = np.argmax(np.abs(diff))
peak_val = diff[peak_ch]

# Frequency mapping
header = wf_inj.header
fch1 = float(header['fch1'])
foff = float(header['foff'])
nchans = int(header['nchans'])

# blimpy freq axis: freq[i] = fch1 + foff * i
# With negative foff, freq decreases as channel index increases
freq_peak = fch1 + foff * peak_ch

print(f"=== Injection signal location ===")
print(f"Peak diff channel: {peak_ch}")
print(f"Peak diff value: {peak_val:.0f}")
print(f"Frequency at peak: {freq_peak:.6f} MHz")
print(f"Original value at peak: {spec_orig[peak_ch]:.0f}")
print(f"Injected value at peak: {spec_inj[peak_ch]:.0f}")

# Show context around the peak
print(f"\n=== Channels around peak ===")
for ch in range(max(0, peak_ch-5), min(nchans, peak_ch+6)):
    f = fch1 + foff * ch
    print(f"  ch {ch}: {f:.6f} MHz  orig={spec_orig[ch]:.0f}  inj={spec_inj[ch]:.0f}  diff={diff[ch]:.0f}")

# Check where blimpy thinks the band edges are
freqs = wf_inj.freqs
print(f"\n=== Band edges (from blimpy freqs array) ===")
print(f"  Lowest freq: {np.min(freqs):.6f} MHz")
print(f"  Highest freq: {np.max(freqs):.6f} MHz")
print(f"  Center freq: {(np.min(freqs) + np.max(freqs))/2:.6f} MHz")
print(f"  n_freqs: {len(freqs)}")
print(f"  freqs[0]: {freqs[0]:.6f}")
print(f"  freqs[-1]: {freqs[-1]:.6f}")
print(f"  freqs[peak_ch]: {freqs[peak_ch]:.6f}")

# Also show the first 5 and last 5 channels
print(f"\n=== First 5 channels ===")
for ch in range(5):
    f = fch1 + foff * ch
    fb = freqs[ch]
    print(f"  ch {ch}: header_freq={f:.6f}  blimpy_freq={fb:.6f}")

print(f"\n=== Last 5 channels ===")
for ch in range(nchans-5, nchans):
    f = fch1 + foff * ch
    fb = freqs[ch]
    print(f"  ch {ch}: header_freq={f:.6f}  blimpy_freq={fb:.6f}")

# Check: what does the SNR 100 signal look like relative to noise?
noise = np.std(spec_orig)
signal_power = spec_inj[peak_ch] - spec_orig[peak_ch]
print(f"\n=== SNR check ===")
print(f"Noise std (original spectrum): {noise:.0f}")
print(f"Signal power (diff at peak): {signal_power:.0f}")
print(f"Ratio: {signal_power/noise:.1f}")
