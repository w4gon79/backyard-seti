#!/usr/bin/env python3
"""
Reproduce turbo_seti's exact noise computation on our injected data
to find why the signal isn't detected.
"""
import numpy as np
from turbo_seti.find_doppler.data_handler import DATAH5
from turbo_seti.find_doppler.find_doppler import comp_stats

inj_path = "data/injected/setigen_v2_s25.h5"
orig_path = "data/Parkes_57790_62144_PROXCEN_S_mid.h5"

# Coarse channel 148 (where injection is)
fch1 = 2574.03515625
foff = 0.00286102294921875
chans_per_cc = 304128 // 297
cc = 148
f_start = fch1 + foff * (cc * chans_per_cc)
f_stop = fch1 + foff * ((cc+1) * chans_per_cc)

print(f"=== Loading coarse channel {cc} ===")

for label, path in [("ORIGINAL", orig_path), ("INJECTED", inj_path)]:
    print(f"\n--- {label} ---")
    h5 = DATAH5(path, f_start=f_start, f_stop=f_stop,
                cchan_id=cc, n_coarse_chan=297,
                gpu_backend=False, precision=1)
    spectra, drift_indices = h5.load_data()
    h5.close()
    
    print(f"  spectra shape: {spectra.shape}")
    
    # This is what turbo_seti actually does (line 413 of find_doppler.py):
    integrated = spectra.sum(axis=0)
    print(f"  integrated spectrum shape: {integrated.shape}")
    print(f"  integrated max: {np.max(integrated):.0f}")
    print(f"  integrated median (raw): {np.median(integrated):.0f}")
    
    # comp_stats: percentile clip to [5%, 95%], then median + std
    the_median, the_stddev = comp_stats(integrated, xp=np)
    print(f"  comp_stats median: {the_median:.0f}")
    print(f"  comp_stats stddev: {the_stddev:.0f}")
    
    # Now normalize: (integrated - median) / stddev
    normalized = (integrated - the_median) / the_stddev
    print(f"  normalized max: {np.max(normalized):.1f}")
    print(f"  normalized max index: {np.argmax(normalized)}")
    
    # Check: is any element above SNR threshold 5?
    above_thresh = np.sum(normalized > 5.0)
    print(f"  elements above SNR 5: {above_thresh}")
    above_thresh_25 = np.sum(normalized > 25.0)
    print(f"  elements above SNR 25: {above_thresh_25}")
    
    if label == "INJECTED":
        # Where is our injection?
        # The injection was at fmid = 3009092319.4885254 Hz
        # In this coarse channel, that's local index:
        inj_freq = 3009092319.4885254
        f_start_hz = f_start * 1e6
        local_idx = int(round((inj_freq - f_start_hz) / 2861.02294921875))
        print(f"\n  Injection local index: {local_idx}")
        print(f"  Normalized value at injection: {normalized[local_idx]:.1f}")
        print(f"  Raw integrated at injection: {integrated[local_idx]:.0f}")
        print(f"  (raw - median) / stddev = ({integrated[local_idx]:.0f} - {the_median:.0f}) / {the_stddev:.0f}")
