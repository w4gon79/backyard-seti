#!/usr/bin/env python3
"""
test_fine_res.py - Inject signal into fine-res BL data and run turbo_seti.

Fine-res: df=2.79 Hz/ch, tsamp=18.25s, 207M channels, 12 GB.
Drift resolution = df / (n_ts * tsamp) = 2.79 / (20*18.25) = 0.0076 Hz/s.
This SHOULD work, unlike mid-res where drift resolution was 166 Hz/s.

Strategy: Load a narrow sub-band using blimpy's f_start/f_stop selection,
rebuild a clean Waterfall with correct header, inject, save, search.
"""
import os
import sys
import numpy as np
import h5py

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

DATA_DIR = "data/fine"
INJ_DIR = "data/injected"
RES_DIR = "results/fine"

os.makedirs(INJ_DIR, exist_ok=True)
os.makedirs(RES_DIR, exist_ok=True)


def test_injected_fine_res():
    """Inject a signal into a fine-res file sub-band, then run turbo_seti."""
    from blimpy import Waterfall

    filepath = os.path.join(DATA_DIR, "Parkes_57791_72989_PROXCEN_S_fine.h5")
    out_path = os.path.join(INJ_DIR, "fine_injected_subband.h5")

    print("=" * 70)
    print("TEST: Fine-res with injected signal (sub-band)")
    print("=" * 70)
    print(f"  File: {filepath}")
    print(f"  Size: {os.path.getsize(filepath) / 1e9:.2f} GB")

    # Read header
    wf = Waterfall(filepath, load_data=False)
    header = wf.header
    fch1 = float(header['fch1'])
    foff = float(header['foff'])
    nchans = int(header['nchans'])
    tsamp = float(header['tsamp'])
    n_ints = wf.n_ints_in_file

    # foff is +2.79e-6 MHz (positive = ascending). fch1 is the LOWEST freq.
    fmin = fch1
    fmax = fch1 + foff * (nchans - 1)
    fmid = (fmin + fmax) / 2.0

    print(f"  fch1: {fch1:.6f} MHz (lowest freq)")
    print(f"  foff: {foff:.2e} MHz (positive = ascending)")
    print(f"  nchans: {nchans}")
    print(f"  tsamp: {tsamp:.3f} s")
    print(f"  n_ints: {n_ints}")
    print(f"  Band: {fmin:.3f} - {fmax:.3f} MHz ({fmax-fmin:.3f} MHz)")
    print(f"  fmid: {fmid:.3f} MHz")

    # Pick a narrow sub-band around center
    sub_nchans = 8192
    center_chan = nchans // 2
    start_chan = center_chan - sub_nchans // 2
    stop_chan = center_chan + sub_nchans // 2

    f_start = fch1 + foff * start_chan
    f_stop = fch1 + foff * stop_chan

    print(f"\n  Sub-band: {f_start:.6f} - {f_stop:.6f} MHz ({sub_nchans} chans)")
    print(f"  Loading sub-band...")

    # Load sub-band data
    wf_sub = Waterfall(filepath, load_data=True, f_start=f_start, f_stop=f_stop)
    data = np.array(wf_sub.data, dtype=np.float32)
    print(f"  Loaded shape: {data.shape}")
    print(f"  Data range: [{np.min(data):.4f}, {np.max(data):.4f}]")

    # blimpy doesn't update header nchans/fch1 for sub-band loads.
    # Compute correct sub-band header values.
    sub_fch1 = f_start  # New lowest freq
    sub_foff = foff     # Same per-channel spacing
    sub_nchans_actual = data.shape[-1]  # Actual loaded channels
    print(f"  Corrected sub header: fch1={sub_fch1:.6f}, foff={sub_foff:.2e}, nchans={sub_nchans_actual}")

    # Compute noise
    sigma = 1.4826 * np.median(np.abs(data - np.median(data)))
    if sigma == 0 or np.isnan(sigma):
        print(f"  WARNING: sigma is {sigma}! Trying std.")
        sigma = np.std(data)
    amplitude = 25.0 * sigma
    print(f"  Noise sigma: {sigma:.6f}")
    print(f"  Signal amplitude (25*sigma): {amplitude:.6f}")

    # Signal at center of sub-band
    sub_signal_chan = sub_nchans_actual // 2
    signal_freq_mhz = sub_fch1 + sub_foff * sub_signal_chan
    print(f"  Signal channel: {sub_signal_chan} of {sub_nchans_actual}")
    print(f"  Signal freq: {signal_freq_mhz:.6f} MHz")

    # Inject signal (zero drift)
    n_tints = data.shape[0]
    for t_idx in range(n_tints):
        for offset in range(-2, 3):
            ch = sub_signal_chan + offset
            if 0 <= ch < sub_nchans_actual:
                w = np.exp(-(offset**2) / 2.0)
                val = amplitude * w
                if data.ndim == 3:
                    data[t_idx, 0, ch] += val
                else:
                    data[t_idx, ch] += val

    peak_val = data[0, 0, sub_signal_chan] if data.ndim == 3 else data[0, sub_signal_chan]
    print(f"  Peak after injection: {peak_val:.6f}")

    # Write a clean HDF5 file with correct header values
    # Use h5py directly to avoid blimpy's header preservation issues
    print(f"\n  Writing clean HDF5 with corrected header...")
    with h5py.File(out_path, 'w') as f:
        # Create data dataset
        ds = f.create_dataset('data', data=data,
                              chunks=(min(16, n_tints), 1, sub_nchans_actual))

        # Copy header from original, but fix fch1 and nchans
        for key in header:
            val = header[key]
            # Handle astropy quantities
            if hasattr(val, 'value'):
                val = val.value
            if isinstance(val, np.ndarray) and val.size == 1:
                val = val.item()
            # Override the critical values
            if key == 'fch1':
                val = sub_fch1
            elif key == 'nchans':
                val = sub_nchans_actual
            try:
                ds.attrs[key] = val
            except (TypeError, ValueError):
                ds.attrs[key] = str(val)

        # Root attrs
        f.attrs['CLASS'] = np.bytes_('FILTERBANK')
        f.attrs['VERSION'] = np.bytes_('1.0')

        # Empty mask (matching BL format)
        mask_shape = list(data.shape)
        mask_shape[-1] = int(mask_shape[-1] * 1.293)
        mask = np.zeros(mask_shape, dtype=np.uint8)
        f.create_dataset('mask', data=mask,
                         chunks=(min(16, n_tints), 1, min(12288, mask_shape[-1])))

    print(f"  Written: {out_path} ({os.path.getsize(out_path) / 1e6:.1f} MB)")

    # Verify roundtrip
    wf_check = Waterfall(out_path, load_data=True)
    d_check = np.array(wf_check.data, dtype=np.float32)
    check_peak = d_check[0, 0, sub_signal_chan] if d_check.ndim == 3 else d_check[0, sub_signal_chan]
    check_fch1 = float(wf_check.header['fch1'])
    check_nchans = int(wf_check.header['nchans'])
    print(f"  Verify: shape={d_check.shape}, fch1={check_fch1:.6f}, nchans={check_nchans}")
    print(f"  Verify: peak[{sub_signal_chan}]={check_peak:.6f}")

    # Run turbo_seti
    print(f"\n  Running turbo_seti (SNR threshold=5)...")
    from bl_doppler_search import run_doppler_search
    dat_path = run_doppler_search(
        out_path,
        out_dir=RES_DIR,
        min_drift=-5,
        max_drift=5,
        snr=5,
    )

    if os.path.exists(dat_path):
        with open(dat_path) as f:
            lines = f.readlines()
        hits = [l for l in lines if not l.startswith('#') and l.strip()]
        print(f"\n  RESULT: {len(hits)} hits on injected fine-res sub-band")
        for h in hits[:10]:
            print(f"    {h.strip()}")
        return len(hits)
    else:
        print(f"\n  RESULT: No .dat file generated")
        return 0


if __name__ == '__main__':
    hits = test_injected_fine_res()
    print(f"\n{'=' * 70}")
    print(f"FINE-RES TEST COMPLETE: {hits} hits")
    print(f"{'=' * 70}")
