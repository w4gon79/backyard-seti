#!/usr/bin/env python3
"""Check actual per-channel SNR of injected signal as turbo_seti would see it."""
import numpy as np
from blimpy import Waterfall

inj_path = "data/injected/Parkes_57790_62144_PROXCEN_S_mid_INJ_d0.0_s100.0.h5"
orig_path = "data/Parkes_57790_62144_PROXCEN_S_mid.h5"

wf_inj = Waterfall(inj_path, load_data=True)
wf_orig = Waterfall(orig_path, load_data=True)

d_inj = np.array(wf_inj.data, dtype=np.float64)
d_orig = np.array(wf_orig.data, dtype=np.float64)

# The injection is at channel 152056
target_ch = 152056

print(f"=== Channel {target_ch} (injection point) ===")
print(f"  orig values (first 5 tints): {d_orig[:5, 0, target_ch]}")
print(f"  inj values  (first 5 tints): {d_inj[:5, 0, target_ch]}")
print(f"  diff        (first 5 tints): {(d_inj-d_orig)[:5, 0, target_ch]}")

# turbo_seti normalizes by dividing by the median or mean of each spectrum
# Then looks for peaks above SNR threshold
# Let's simulate what it sees

# Per-spectrum normalization (subtract mean, divide by std)
# This is per time sample, across all channels
print(f"\n=== Per-tint normalized SNR at injection channel ===")
for t in range(5):
    spec = d_inj[t, 0, :]
    spec_orig = d_orig[t, 0, :]
    
    # turbo_seti uses the raw spectrum, then takes FFT
    # The "SNR" is relative to the noise in that coarse channel
    # Find which coarse channel contains our injection
    # 297 coarse channels, each ~1024 fine channels
    coarse_ch = target_ch // (d_inj.shape[2] // 297)
    fine_start = coarse_ch * (d_inj.shape[2] // 297)
    fine_end = fine_start + (d_inj.shape[2] // 297)
    
    cc_data = spec[fine_start:fine_end]
    cc_orig = spec_orig[fine_start:fine_end]
    
    median = np.median(cc_data)
    mad = np.median(np.abs(cc_data - median))
    sigma = 1.4826 * mad
    
    local_idx = target_ch - fine_start
    signal_power = cc_data[local_idx] - median
    snr_local = signal_power / sigma if sigma > 0 else float('inf')
    
    print(f"  tint {t}: coarse_ch={coarse_ch}, local_idx={local_idx}")
    print(f"    coarse channel range: {fine_start}-{fine_end} ({d_inj.shape[2]//297} chans)")
    print(f"    median={median:.0f}, sigma={sigma:.0f}")
    print(f"    value={cc_data[local_idx]:.0f}, signal_above_median={signal_power:.0f}")
    print(f"    local SNR = {snr_local:.1f}")

# Check the coarse channel structure
ncc = 297
chans_per_cc = d_inj.shape[2] // ncc
print(f"\n=== Coarse channel structure ===")
print(f"  n_coarse_chan: {ncc}")
print(f"  channels per coarse chan: {chans_per_cc}")
print(f"  injection in coarse chan: {target_ch // chans_per_cc}")
print(f"  local position: {target_ch % chans_per_cc}")
