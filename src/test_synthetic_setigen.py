#!/usr/bin/env python3
"""
test_synthetic_setigen.py - Build a completely synthetic setigen Frame from scratch
(not loaded from any BL file), inject a known signal, save, and run turbo_seti.

This isolates whether turbo_seti's detection failure is:
  (a) A data format issue with BL HDF5 files, or
  (b) A fundamental algorithm issue in turbo_seti's dechirp/FFT pipeline.

If turbo_seti detects the signal here but not in BL files -> format issue.
If turbo_seti still can't detect -> algorithm bug in turbo_seti itself.
"""
import os
import sys
import numpy as np

# Output dirs
os.makedirs("data/synthetic", exist_ok=True)
os.makedirs("results/synthetic", exist_ok=True)

print("=" * 70)
print("TEST: Pure Synthetic setigen Frame -> turbo_seti detection")
print("=" * 70)

# Step 1: Build a synthetic Frame from scratch with noise
print("\n--- Step 1: Create synthetic Frame ---")
import setigen
from setigen import Frame

frame = Frame(
    fch1=2604e6,          # Hz (same as Parkes mid-res)
    df=2.7939677,         # Hz per channel
    fchans=4096,          # Number of frequency channels
    tchans=16,            # Number of time integrations
    dt=18.253611,         # Seconds (same as Parkes)
    seed=42,
)

# setigen Frame starts with zeros. Add Gaussian noise manually.
rng = np.random.default_rng(42)
noise_level = 100.0  # Arbitrary, similar to real data counts
frame.data = rng.normal(0, noise_level, frame.data.shape).astype(np.float32)
frame._update_noise_frame_stats()

nchans = frame.fchans
n_int = frame.tchans

print(f"  fch1: {frame.fch1/1e6:.3f} MHz")
print(f"  df: {frame.df:.6f} Hz")
print(f"  fchans: {nchans}")
print(f"  dt: {frame.dt:.3f} s")
print(f"  tchans: {n_int}")
print(f"  fmid: {frame.fmid/1e6:.6f} MHz")
print(f"  bandwidth: {nchans * frame.df:.0f} Hz ({nchans * frame.df/1e6:.3f} MHz)")
print(f"  noise_std: {frame.noise_std:.4f}")
print(f"  data shape: {frame.data.shape}")
print(f"  data range: [{np.min(frame.data):.3f}, {np.max(frame.data):.3f}]")

# Step 2: Inject a signal at the center frequency with zero drift
print("\n--- Step 2: Inject signal ---")
signal_freq = frame.fmid  # Center of band, in Hz
drift_rate = 0.0          # Zero drift (easy case first)
target_snr = 25.0         # Strong signal
signal_level = target_snr * frame.noise_std

print(f"  Signal freq: {signal_freq/1e6:.6f} MHz")
print(f"  Drift rate: {drift_rate} Hz/s")
print(f"  Target SNR: {target_snr}")
print(f"  Signal level: {signal_level:.2f}")

frame.add_constant_signal(
    f_start=signal_freq,
    drift_rate=drift_rate,
    level=signal_level,
    width=frame.df * 3,  # ~3 channels wide
    f_profile_type='gaussian',
)

# Verify signal is in data
data = np.array(frame.data)
print(f"  Data range after injection: [{np.min(data):.3f}, {np.max(data):.3f}]")

# Find the peak channel
integrated = np.sum(data, axis=0)  # Sum over time
peak_ch = np.argmax(integrated)
print(f"  Peak channel: {peak_ch} (expected ~{nchans // 2})")
print(f"  Peak integrated value: {integrated[peak_ch]:.1f}")

# Compute actual SNR from data
noise_per_ch = np.std(integrated)
signal_peak = integrated[peak_ch] - np.median(integrated)
actual_snr = signal_peak / noise_per_ch if noise_per_ch > 0 else float('nan')
print(f"  Measured SNR from integrated spectrum: {actual_snr:.1f}")

# Step 3: Save to HDF5
print("\n--- Step 3: Save to HDF5 ---")
out_path = "data/synthetic/synthetic_zero_drift.h5"
frame.save_h5(out_path)
print(f"  Saved: {out_path} ({os.path.getsize(out_path) / 1024:.1f} KB)")

# Step 4: Reload and verify signal survived
print("\n--- Step 4: Reload and verify ---")
from blimpy import Waterfall
wf = Waterfall(out_path, load_data=True)
reloaded = np.array(wf.data, dtype=np.float32)
if reloaded.ndim == 3:
    reloaded = reloaded[:, 0, :]  # Drop IF dim
print(f"  Reloaded shape: {reloaded.shape}")
print(f"  Reloaded range: [{np.min(reloaded):.3f}, {np.max(reloaded):.3f}]")

# Check signal is still there
re_integrated = np.sum(reloaded, axis=0)
re_peak = np.argmax(re_integrated)
print(f"  Peak channel: {re_peak}")
print(f"  Peak integrated value: {re_integrated[re_peak]:.1f}")

# Step 5: Run turbo_seti
print("\n--- Step 5: Run turbo_seti ---")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bl_doppler_search import run_doppler_search

dat_path = run_doppler_search(
    out_path,
    out_dir='results/synthetic',
    min_drift=-5,
    max_drift=5,
    snr=5,  # Low threshold to catch anything
)

# Parse results
print("\n--- Step 6: Results ---")
if os.path.exists(dat_path):
    with open(dat_path) as f:
        lines = f.readlines()
    hits = [l for l in lines if not l.startswith('#') and l.strip()]
    print(f"  turbo_seti found {len(hits)} hits")
    for h in hits[:10]:
        print(f"    {h.strip()}")

    if len(hits) == 0:
        print("\n  *** STILL ZERO HITS ON SYNTHETIC DATA ***")
        print("  This confirms the problem is in turbo_seti's algorithm,")
        print("  not in BL file format.")
    else:
        print("\n  *** SYNTHETIC SIGNAL DETECTED! ***")
        print("  turbo_seti works on synthetic data but fails on BL files.")
        print("  Problem is data-format related.")
else:
    print("  No .dat output file generated.")

# Step 7: Also try with a non-zero drift rate
print("\n" + "=" * 70)
print("BONUS TEST: Non-zero drift (0.5 Hz/s)")
print("=" * 70)

frame2 = Frame(
    fch1=2604e6,
    df=2.7939677,
    fchans=4096,
    tchans=16,
    dt=18.253611,
    seed=43,
)
rng2 = np.random.default_rng(43)
frame2.data = rng2.normal(0, noise_level, frame2.data.shape).astype(np.float32)
frame2._update_noise_frame_stats()

signal_freq2 = frame2.fmid
drift_rate2 = 0.5  # Hz/s, typical for a real signal
signal_level2 = 25.0 * frame2.noise_std

print(f"  Signal freq: {signal_freq2/1e6:.6f} MHz")
print(f"  Drift rate: {drift_rate2} Hz/s")
print(f"  Signal level: {signal_level2:.2f}")

frame2.add_constant_signal(
    f_start=signal_freq2,
    drift_rate=drift_rate2,
    level=signal_level2,
    width=frame2.df * 3,
    f_profile_type='gaussian',
)

out_path2 = "data/synthetic/synthetic_drift_0p5.h5"
frame2.save_h5(out_path2)
print(f"  Saved: {out_path2}")

dat_path2 = run_doppler_search(
    out_path2,
    out_dir='results/synthetic',
    min_drift=-5,
    max_drift=5,
    snr=5,
)

if os.path.exists(dat_path2):
    with open(dat_path2) as f:
        lines = f.readlines()
    hits2 = [l for l in lines if not l.startswith('#') and l.strip()]
    print(f"  turbo_seti found {len(hits2)} hits (drift=0.5 Hz/s)")
    for h in hits2[:10]:
        print(f"    {h.strip()}")
else:
    print("  No .dat output file generated.")

print("\n" + "=" * 70)
print("TEST COMPLETE")
print("=" * 70)
