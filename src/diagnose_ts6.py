#!/usr/bin/env python3
"""Check turbo_seti's load_data() on the injected coarse channel."""
import numpy as np
from turbo_seti.find_doppler.data_handler import DATAH5

inj_path = "data/injected/Parkes_57790_62144_PROXCEN_S_mid_INJ_d0.0_s100.0.h5"

# Coarse channel 148: channels 151552-152576
fch1 = 2574.03515625
foff = 0.00286102294921875
chans_per_cc = 304128 // 297  # 1024
cc = 148
f_start = fch1 + foff * (cc * chans_per_cc)
f_stop = fch1 + foff * ((cc+1) * chans_per_cc)
print(f"Coarse chan {cc}: {f_start:.6f} - {f_stop:.6f} MHz")

# Create DATAH5 exactly like turbo_seti does
h5 = DATAH5(inj_path, f_start=f_start, f_stop=f_stop,
            cchan_id=cc, n_coarse_chan=297,
            gpu_backend=False, precision=1)

print(f"  fftlen: {h5.fftlen}")
print(f"  tsteps: {h5.tsteps}")
print(f"  tsteps_valid: {h5.tsteps_valid}")
print(f"  tdwidth: {h5.tdwidth}")
print(f"  shoulder_size: {h5.shoulder_size}")

# Load data
print(f"\n=== Loading data ===")
spectra, drift_indices = h5.load_data()
h5.close()

print(f"  spectra type: {type(spectra).__name__}")
print(f"  spectra shape: {spectra.shape}")
print(f"  spectra dtype: {spectra.dtype}")
print(f"  spectra[0, 0:5]: {spectra[0, 0:5]}")
print(f"  spectra[0, 504]: {spectra[0, 504]}")  # injection point
print(f"  drift_indices shape: {drift_indices.shape}")

# Check if injection is visible
median = np.median(spectra)
mad = np.median(np.abs(spectra - median))
sigma = 1.4826 * mad
peak = np.max(spectra[0])
peak_idx = np.argmax(spectra[0])
print(f"\n  median: {median:.0f}")
print(f"  sigma: {sigma:.0f}")
print(f"  peak in spectra[0]: {peak:.0f} at index {peak_idx}")
print(f"  SNR: {(peak-median)/sigma:.1f}")

# Check for overflow or inf/nan
print(f"\n  any inf: {np.any(np.isinf(spectra))}")
print(f"  any nan: {np.any(np.isnan(spectra))}")
print(f"  min: {np.min(spectra):.0f}")
print(f"  max: {np.max(spectra):.0f}")
