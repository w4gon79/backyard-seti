#!/usr/bin/env python3
"""
test_midres_clone.py - Build a synthetic Frame with BL mid-res parameters
to reproduce the detection failure.

Key differences from the working synthetic test:
  - foff POSITIVE (+0.002861 MHz) like real BL mid-res
  - nchans: 304128 (or scaled down but same ratio)
  - tsamp: 1.074s (17x finer)
  - Proper coarse channel structure

We'll test several variations to isolate which parameter causes the failure.
"""
import os
import sys
import numpy as np

os.makedirs("data/synthetic", exist_ok=True)
os.makedirs("results/synthetic", exist_ok=True)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bl_doppler_search import run_doppler_search

import setigen
from setigen import Frame


def make_and_test(name, fch1, df, fchans, tchans, dt, signal_freq_hz,
                  drift_rate=0.0, target_snr=25.0, snr_threshold=5.0,
                  noise_level=100.0, seed=42):
    """Build synthetic frame, inject signal, save, run turbo_seti."""
    print(f"\n{'=' * 70}")
    print(f"TEST: {name}")
    print(f"  fch1={fch1/1e6:.3f} MHz, df={df:.7f} Hz, fchans={fchans}, "
          f"tchans={tchans}, dt={dt:.3f}s")
    print(f"  foff={'positive' if df > 0 else 'negative'}")
    print(f"  Signal: {signal_freq_hz/1e6:.6f} MHz, drift={drift_rate} Hz/s, SNR={target_snr}")
    print(f"{'=' * 70}")

    # setigen Frame uses df in Hz. For ascending freq (BL mid-res), foff is positive
    # which means fch1 is the LOWEST freq and channels go UP.
    # setigen Frame: ascending=False by default (fch1 is highest, channels descend).
    # BL mid-res has foff > 0, meaning ascending=True.
    ascending = (df > 0)

    frame = Frame(
        fch1=fch1,
        df=abs(df),  # setigen wants positive df, uses ascending flag
        fchans=fchans,
        tchans=tchans,
        dt=dt,
        ascending=ascending,
        seed=seed,
    )

    # Inject noise
    rng = np.random.default_rng(seed)
    frame.data = rng.normal(0, noise_level, frame.data.shape).astype(np.float32)
    frame._update_noise_frame_stats()

    print(f"  noise_std: {frame.noise_std:.4f}")
    print(f"  fmid: {frame.fmid/1e6:.6f} MHz")
    print(f"  bandwidth: {fchans * abs(df):.0f} Hz ({fchans * abs(df)/1e6:.3f} MHz)")

    # Inject signal
    signal_level = target_snr * frame.noise_std
    print(f"  signal level: {signal_level:.2f}")

    frame.add_constant_signal(
        f_start=signal_freq_hz,
        drift_rate=drift_rate,
        level=signal_level,
        width=abs(df) * 3,
        f_profile_type='gaussian',
    )

    # Verify signal in data
    data = np.array(frame.data)
    integrated = np.sum(data, axis=0)
    peak_ch = np.argmax(integrated)
    noise_per_ch = np.std(integrated)
    signal_peak = integrated[peak_ch] - np.median(integrated)
    measured_snr = signal_peak / noise_per_ch if noise_per_ch > 0 else float('nan')
    print(f"  Peak channel: {peak_ch}, measured SNR: {measured_snr:.1f}")
    print(f"  Data range: [{np.min(data):.3f}, {np.max(data):.3f}]")

    # Save
    out_path = f"data/synthetic/{name}.h5"
    frame.save_h5(out_path)
    print(f"  Saved: {out_path} ({os.path.getsize(out_path) / 1024:.1f} KB)")

    # Reload and verify
    from blimpy import Waterfall
    wf = Waterfall(out_path, load_data=True)
    reloaded = np.array(wf.data, dtype=np.float32)
    if reloaded.ndim == 3:
        reloaded = reloaded[:, 0, :]
    re_int = np.sum(reloaded, axis=0)
    re_peak = np.argmax(re_int)
    print(f"  Reloaded: peak ch {re_peak}, val {re_int[re_peak]:.1f}")

    # Run turbo_seti
    print(f"\n  Running turbo_seti (SNR threshold={snr_threshold})...")
    dat_path = run_doppler_search(
        out_path,
        out_dir='results/synthetic',
        min_drift=-5,
        max_drift=5,
        snr=snr_threshold,
    )

    # Parse hits
    if os.path.exists(dat_path):
        with open(dat_path) as f:
            lines = f.readlines()
        hits = [l for l in lines if not l.startswith('#') and l.strip()]
        print(f"\n  RESULT: {len(hits)} hits found")
        for h in hits[:5]:
            print(f"    {h.strip()}")
        return len(hits)
    else:
        print(f"\n  RESULT: No .dat file generated (zero hits)")
        return 0


# ─── Test 1: Exact BL mid-res clone (scaled down) ─────────────────────
# BL mid-res: fch1=2574.035 MHz, foff=+0.002861 MHz, nchans=304128, tsamp=1.074s
# Scale down nchans to keep memory reasonable, but keep the key parameters
# Use 1 coarse channel worth: ~4752 channels at foff=0.002861 MHz
results = {}

# Test A: Positive foff (ascending), BL-like coarse channel
results['A_pos_foff'] = make_and_test(
    name="midres_clone_pos_foff",
    fch1=2574.03515625e6,      # Hz, same as BL
    df=+0.00286102294921875 * 1e6,  # Positive foff in Hz (ascending)
    fchans=4752,               # One coarse channel worth
    tchans=16,                 # Match our working test
    dt=1.073741824,            # BL mid-res tsamp
    signal_freq_hz=2574.03515625e6 + 0.00286102294921875e6 * 2376,  # Center of band
    drift_rate=0.0,
)

# Test B: Negative foff (descending), same params otherwise
results['B_neg_foff'] = make_and_test(
    name="midres_clone_neg_foff",
    fch1=2574.03515625e6 + 0.00286102294921875e6 * 4751,  # Top of band
    df=-0.00286102294921875 * 1e6,  # Negative foff (descending, like fine-res)
    fchans=4752,
    tchans=16,
    dt=1.073741824,
    signal_freq_hz=2574.03515625e6 + 0.00286102294921875e6 * 2376,
    drift_rate=0.0,
)

# Test C: Large channel count like BL (304k channels, positive foff)
# This is memory-heavy but we need to test coarse channel splitting
results['C_large_nchans'] = make_and_test(
    name="midres_clone_large",
    fch1=2574.03515625e6,
    df=+0.00286102294921875 * 1e6,
    fchans=304128,             # Full BL mid-res channel count
    tchans=4,                  # Fewer time samples to keep memory manageable
    dt=1.073741824,
    signal_freq_hz=2574.03515625e6 + 0.00286102294921875e6 * 152064,  # Center
    drift_rate=0.0,
)

# Summary
print(f"\n{'=' * 70}")
print("SUMMARY")
print(f"{'=' * 70}")
for name, count in results.items():
    status = "✓ DETECTED" if count > 0 else "✗ ZERO HITS"
    print(f"  {name}: {count} hits  {status}")
