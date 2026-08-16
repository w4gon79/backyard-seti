#!/usr/bin/env python3
"""
fine_res_pipeline.py - Production pipeline for turbo_seti on fine-res BL data.

Strategy:
  1. Extract narrow sub-bands from 12 GB fine-res files (avoid loading full file)
  2. Write clean HDF5 with corrected header (blimpy sub-band headers are broken)
  3. Run turbo_seti FindDoppler on each sub-band
  4. Collect and deduplicate hits across all sub-bands
  5. Optionally filter against known RFI zones

Fine-res specs:
  df = 2.79 Hz/channel
  tsamp = 18.254 s
  nchans = 207,618,048 (580 MHz bandwidth)
  Drift resolution = 0.0076 Hz/s (excellent for narrowband SETI)

Usage:
    python fine_res_pipeline.py --data-dir data/fine --out results/fine_pipeline
    python fine_res_pipeline.py --file data/fine/Parkes_57791_72989_PROXCEN_S_fine.h5
    python fine_res_pipeline.py --data-dir data/fine --sub-band-width 50000 --overlap 1000
"""
import argparse
import os
import sys
import time
import json
import h5py
import numpy as np
from pathlib import Path

try:
    _SETI_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _SETI_ROOT not in sys.path:
        sys.path.insert(0, _SETI_ROOT)
    import rfi_zones
except Exception:
    rfi_zones = None


def load_checkpoint(out_dir):
    """Load checkpoint.json from output dir if it exists."""
    cp_path = os.path.join(out_dir, 'checkpoint.json')
    if os.path.isfile(cp_path):
        with open(cp_path) as f:
            return json.load(f)
    return None


def save_checkpoint(out_dir, file_index, file_total, file_name,
                    sub_band_index=None, sub_band_total=None,
                    completed_files=None):
    """Write checkpoint.json so resume can pick up from here."""
    cp = {
        'file_index': file_index,
        'file_total': file_total,
        'file_name': file_name,
        'sub_band_index': sub_band_index,
        'sub_band_total': sub_band_total,
        'completed_files': completed_files or [],
        'timestamp': time.strftime('%Y-%m-%dT%H:%M:%S'),
    }
    cp_path = os.path.join(out_dir, 'checkpoint.json')
    with open(cp_path, 'w') as f:
        json.dump(cp, f, indent=2)
    return cp


def extract_sub_band(filepath, f_start_mhz, f_stop_mhz, out_path):
    """Extract a sub-band from a fine-res file, write clean HDF5.

    Direct h5py block reads via incoherent_stack.load_spectrum_window_2d
    (verified bit-exact vs blimpy). Avoids blimpy's per-call file open and
    header parsing, and its silent clamping of out-of-range windows to the
    full 580 MHz band.
    """
    try:
        from incoherent_stack import load_spectrum_window_2d
    except ImportError:
        # Running as a subprocess from src/: put repo root on the path
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from incoherent_stack import load_spectrum_window_2d
    import hdf5plugin  # noqa: F401 - registers bitshuffle filter for h5py

    freqs, data = load_spectrum_window_2d(filepath, f_start_mhz, f_stop_mhz)
    if freqs is None:
        raise ValueError(
            f'Window {f_start_mhz:.9f}-{f_stop_mhz:.9f} MHz outside '
            f'file coverage: {filepath}')

    # Loader returns (n_tints, n_chans); sub-band files keep the
    # filterbank (n_tints, 1, n_chans) convention turbo_seti expects.
    data = data[:, np.newaxis, :]

    # Copy source header attrs; fch1/nchans overridden below. sub_fch1
    # comes from the loader's actual grid, so header and data always agree.
    with h5py.File(filepath, 'r') as f_in:
        header = dict(f_in['data'].attrs)
        foff = float(header['foff'])
        tsamp = float(header['tsamp'])

    sub_fch1 = float(freqs[0])
    sub_nchans = data.shape[-1]

    # turbo_seti's data handler splits the band into 64 coarse channels
    # when the file carries no coarse-channel metadata (its 'SWAG' path,
    # taken by every extracted sub-band). If sub_nchans is not divisible
    # by 64, its slice math and header disagree by a channel and
    # load_data() raises 'spectra.shape != (tsteps_valid, fftlen)'
    # (first seen on GBT gpuspec data: 258156 chans -> 4033.69/cc).
    # Trim from the top edge to an exact multiple of 64; the lost band is
    # <64 channels (~0.18 Hz), far below the sub-band overlap.
    TSETI_CC = 64
    trim = sub_nchans % TSETI_CC
    if trim:
        sub_nchans -= trim
        data = data[..., :sub_nchans]

    # Write clean HDF5
    with h5py.File(out_path, 'w') as f:
        n_tints = data.shape[0]
        ds = f.create_dataset('data', data=data,
                              chunks=(min(16, n_tints), 1, sub_nchans))

        for key in header:
            val = header[key]
            if hasattr(val, 'value'):
                val = val.value
            if isinstance(val, np.ndarray) and val.size == 1:
                val = val.item()
            if key == 'fch1':
                val = sub_fch1
            elif key == 'nchans':
                val = sub_nchans
            try:
                ds.attrs[key] = val
            except (TypeError, ValueError):
                ds.attrs[key] = str(val)

        f.attrs['CLASS'] = np.bytes_('FILTERBANK')
        f.attrs['VERSION'] = np.bytes_('1.0')

        mask_shape = list(data.shape)
        mask_shape[-1] = int(mask_shape[-1] * 1.293)
        mask = np.zeros(mask_shape, dtype=np.uint8)
        f.create_dataset('mask', data=mask,
                         chunks=(min(16, n_tints), 1, min(12288, mask_shape[-1])))

    return sub_nchans, sub_fch1, foff


def run_turbo_seti(filepath, out_dir, min_drift=1e-5, max_drift=5.0, snr=5.0):
    """Run turbo_seti on a single file."""
    from turbo_seti.find_doppler.find_doppler import FindDoppler

    filepath = os.path.abspath(filepath)
    out_dir = os.path.abspath(out_dir).replace('\\', '/')
    basename = os.path.basename(filepath)
    data_dir = os.path.dirname(filepath)

    os.makedirs(out_dir, exist_ok=True)

    original_cwd = os.getcwd()
    os.chdir(data_dir)
    try:
        doppler = FindDoppler(
            basename,
            min_drift=min_drift,
            max_drift=max_drift,
            snr=snr,
            out_dir=out_dir,
        )
        doppler.search()
    finally:
        os.chdir(original_cwd)

    stem = os.path.splitext(basename)[0]
    dat_path = os.path.join(out_dir, stem + '.dat')

    hits = []
    if os.path.exists(dat_path):
        with open(dat_path) as f:
            for line in f:
                if not line.startswith('#') and line.strip():
                    parts = line.split()
                    if len(parts) < 6:
                        continue
                    try:
                        # turbo_seti .dat format:
                        # Top_Hit#  DriftRate  SNR  UncorrectedFreq  CorrectedFreq  Index  freq_start  freq_end ...
                        hit = {
                            'drift_rate': float(parts[1]),
                            'snr': float(parts[2]),
                            'freq': float(parts[3]),
                            'channel': int(float(parts[5])),
                        }
                        hits.append(hit)
                    except (ValueError, IndexError):
                        continue
    return hits


def compute_sub_bands(fch1, foff, nchans, sub_band_chans=8192, overlap_chans=512):
    """Compute sub-band frequency ranges covering the full band.

    Returns list of (f_start_mhz, f_stop_mhz, start_chan, stop_chan).
    """
    effective_step = sub_band_chans - overlap_chans
    sub_bands = []
    start = 0
    while start < nchans:
        stop = min(start + sub_band_chans, nchans)
        f_start = fch1 + foff * start
        f_stop = fch1 + foff * stop
        sub_bands.append((f_start, f_stop, start, stop))
        if stop >= nchans:
            break
        start += effective_step
    return sub_bands


def process_file(filepath, out_dir, sub_band_chans=8192, overlap_chans=512,
                 max_drift=5.0, snr=5.0, verbose=True,
                 start_sub_band=0, file_index=0, file_total=1):
    """Process a single fine-res file through the pipeline."""

    filename = os.path.basename(filepath)
    stem = os.path.splitext(filename)[0]

    if verbose:
        print(f"\n{'=' * 70}")
        print(f"Processing: {filename}")
        print(f"  Size: {os.path.getsize(filepath) / 1e9:.2f} GB")
        print(f"{'=' * 70}")

    # Read header (direct h5py; hdf5plugin registers bitshuffle)
    import hdf5plugin  # noqa: F401
    with h5py.File(filepath, 'r') as f:
        _attrs = f['data'].attrs
        fch1 = float(_attrs['fch1'])
        foff = float(_attrs['foff'])
        nchans = int(_attrs['nchans'])
        tsamp = float(_attrs['tsamp'])
        n_ints = f['data'].shape[0]

    fmin = fch1
    fmax = fch1 + foff * (nchans - 1)
    t_total = n_ints * tsamp
    drift_res = abs(foff * 1e6) / t_total

    if verbose:
        print(f"  Band: {fmin:.3f} - {fmax:.3f} MHz ({fmax-fmin:.1f} MHz)")
        print(f"  Channels: {nchans:,}")
        print(f"  Time: {n_ints} ints x {tsamp:.3f}s = {t_total:.1f}s")
        print(f"  Drift resolution: {drift_res:.6f} Hz/s")
        print(f"  df: {abs(foff)*1e6:.4f} Hz/channel")

    # Compute sub-bands
    sub_bands = compute_sub_bands(fch1, foff, nchans, sub_band_chans, overlap_chans)

    if verbose:
        print(f"  Sub-bands: {len(sub_bands)} x {sub_band_chans} chans "
              f"(overlap {overlap_chans})")

    # Process each sub-band
    subdir = os.path.join(out_dir, stem)
    subband_dir = os.path.join(subdir, 'subbands')
    os.makedirs(subband_dir, exist_ok=True)

    all_hits = []
    t0 = time.time()

    # Load any partial hits from a previous run of this file
    partial_hits_path = os.path.join(subdir, f'{stem}_partial_hits.json')
    if start_sub_band > 0 and os.path.isfile(partial_hits_path):
        with open(partial_hits_path) as f:
            partial = json.load(f)
            all_hits = partial.get('hits', [])
            if verbose:
                print(f"  Resuming with {len(all_hits)} hits from sub-bands 0-{start_sub_band-1}")

    for i, (f_start, f_stop, ch_start, ch_stop) in enumerate(sub_bands):
        if i < start_sub_band:
            continue
        # Skip sub-bands fully covered by per-epoch RFI zones
        if rfi_zones is not None:
            try:
                _ep = os.path.basename(filepath).split('_')[1]
                if rfi_zones.coverage_fraction(f_start, f_stop, _ep) >= 0.99:
                    if verbose:
                        print(f"\n  [{i+1}/{len(sub_bands)}] {f_start:.4f}-{f_stop:.4f} "
                              f"MHz: RFI zone, skipped")
                    continue
            except Exception:
                pass
        sub_name = f"{stem}_sub{i:05d}.h5"
        sub_path = os.path.join(subband_dir, sub_name)
        subband_res_dir = os.path.join(subdir, 'turbo_seti')

        if verbose:
            bw = (f_stop - f_start) * 1000  # kHz
            elapsed = time.time() - t0
            if i > 0:
                rate = elapsed / i
                eta = rate * (len(sub_bands) - i)
                eta_str = f" ETA {eta/60:.0f}min"
            else:
                eta_str = ""
            print(f"  [{i+1}/{len(sub_bands)}] {f_start:.4f}-{f_stop:.4f} MHz "
                  f"({bw:.1f} kHz){eta_str}", end='', flush=True)

        try:
            nch, sub_fch1, sub_foff = extract_sub_band(
                filepath, f_start, f_stop, sub_path)

            # Save spectra snapshot for Mission Control dashboard
            try:
                with h5py.File(sub_path, 'r') as hf:
                    snap_data = hf['data'][:]
                # Downsample frequency axis to 1024 bins
                n_chans = snap_data.shape[-1]
                target_cols = 1024
                if n_chans > target_cols:
                    factor = n_chans // target_cols
                    snap_data = snap_data[:, :, :factor*target_cols].reshape(
                        snap_data.shape[0], 1, target_cols, factor).mean(axis=-1)
                spectra_db = 10 * np.log10(np.abs(snap_data) + 1e-10)
                # Downsample time axis to max 60 rows
                if spectra_db.shape[0] > 60:
                    step = spectra_db.shape[0] // 60
                    spectra_db = spectra_db[::step][:60]
                np.savez_compressed(
                    os.path.join(out_dir, 'last_spectra.npz'),
                    spectra=spectra_db.squeeze(),
                    f_start=f_start, f_stop=f_stop,
                    subband_index=i,
                )
            except Exception:
                pass

            hits = run_turbo_seti(sub_path, subband_res_dir,
                                  max_drift=max_drift, snr=snr)

            # Keep all hits, annotate with sub-band info
            for h in hits:
                h['sub_band'] = i
                h['file'] = stem
                all_hits.append(h)

            if verbose:
                print(f" -> {len(hits)} hits")

        except Exception as e:
            if verbose:
                print(f" ERROR: {e}")

        # Checkpoint: save progress after each sub-band
        save_checkpoint(out_dir, file_index, file_total, filename,
                        sub_band_index=i + 1, sub_band_total=len(sub_bands),
                        completed_files=None)
        # Save partial hits for this file so resume can reload them
        with open(partial_hits_path, 'w') as f:
            json.dump({'hits': all_hits, 'last_sub_band': i}, f)

        # Clean up sub-band file AND turbo_seti intermediate files
        # to keep results directory tidy
        import gc
        gc.collect()
        # Remove sub-band HDF5
        if os.path.exists(sub_path):
            try:
                os.remove(sub_path)
            except (PermissionError, OSError):
                pass
        # Remove turbo_seti .dat and .log files for this sub-band
        sub_stem = os.path.splitext(os.path.basename(sub_path))[0]
        for ext in ['.dat', '.log']:
            ts_file = os.path.join(subdir, 'turbo_seti', sub_stem + ext)
            if os.path.exists(ts_file):
                try:
                    os.remove(ts_file)
                except (PermissionError, OSError):
                    pass
    # Clean up empty subbands directory after loop (not inside it)
    sb_dir = os.path.join(subdir, 'subbands')
    if os.path.isdir(sb_dir) and not os.listdir(sb_dir):
        try:
            os.rmdir(sb_dir)
        except (PermissionError, OSError):
            pass

    elapsed = time.time() - t0

    if verbose:
        print(f"\n  Total hits: {len(all_hits)}")
        print(f"  Time: {elapsed:.1f}s ({elapsed/len(sub_bands):.2f}s/sub-band)")

    # Sort by SNR descending
    all_hits.sort(key=lambda h: h['snr'], reverse=True)

    # Save results
    results_path = os.path.join(subdir, f'{stem}_hits.json')
    with open(results_path, 'w') as f:
        json.dump({
            'file': filename,
            'total_hits': len(all_hits),
            'sub_bands_processed': len(sub_bands),
            'drift_resolution_hz_s': drift_res,
            'processing_time_s': elapsed,
            'hits': all_hits,
        }, f, indent=2)

    if verbose and all_hits:
        print(f"\n  Top 10 hits:")
        for h in all_hits[:10]:
            print(f"    SNR {h['snr']:.1f} | {h['freq']:.6f} MHz | "
                  f"drift {h['drift_rate']:.4f} Hz/s | sub {h['sub_band']}")

    print(f"  Results: {results_path}")

    return all_hits


def main():
    parser = argparse.ArgumentParser(
        description='Fine-res turbo_seti pipeline')
    parser.add_argument('--file', action='append', help='Fine-res .h5 file (can be specified multiple times)')
    parser.add_argument('--data-dir', help='Directory of fine-res files')
    parser.add_argument('--out', '-o', default='results/fine_pipeline',
                        help='Output directory')
    parser.add_argument('--sub-band-width', type=int, default=8192,
                        help='Sub-band width in channels (default 8192)')
    parser.add_argument('--overlap', type=int, default=512,
                        help='Sub-band overlap in channels (default 512)')
    parser.add_argument('--max-drift', type=float, default=5.0,
                        help='Max drift rate Hz/s (default 5.0)')
    parser.add_argument('--snr', type=float, default=5.0,
                        help='SNR threshold (default 5.0)')
    parser.add_argument('--limit', type=int, default=None,
                        help='Limit number of sub-bands (for testing)')
    parser.add_argument('--resume', action='store_true',
                        help='Resume from checkpoint in output directory')
    args = parser.parse_args()

    os.makedirs(args.out, exist_ok=True)

    files = []
    if args.file:
        files = args.file
    elif args.data_dir:
        files = sorted([
            os.path.join(args.data_dir, f)
            for f in os.listdir(args.data_dir)
            if f.endswith('.h5')
        ])
    else:
        parser.error('Provide --file or --data-dir')

    # ── Resume logic ──
    resume_cp = None
    if args.resume:
        resume_cp = load_checkpoint(args.out)
        if resume_cp:
            print(f"RESUME: Found checkpoint from {resume_cp.get('timestamp', '?')}")
            print(f"  Last file: {resume_cp.get('file_index', 0)}/{resume_cp.get('file_total', 0)} "
                  f"({resume_cp.get('file_name', '?')})")
            print(f"  Sub-band: {resume_cp.get('sub_band_index', 0)}/{resume_cp.get('sub_band_total', 0)}")
        else:
            print("RESUME: No checkpoint found, starting from scratch")

    # Determine which files to skip and where to resume sub-bands
    completed_files = []
    start_file_index = 0
    start_sub_band = 0

    if resume_cp:
        # Check which files already have their hits.json written
        for i, filepath in enumerate(files):
            fname = os.path.basename(filepath)
            stem = os.path.splitext(fname)[0]
            hits_path = os.path.join(args.out, stem, f'{stem}_hits.json')
            if os.path.isfile(hits_path):
                completed_files.append(fname)
                print(f"  SKIP (complete): {fname}")
            else:
                start_file_index = i
                start_sub_band = resume_cp.get('sub_band_index', 0) if resume_cp.get('file_name', '') == fname else 0
                break

    print(f"\nFine-res SETI Pipeline")
    print(f"  Files: {len(files)}")
    if completed_files:
        print(f"  Resuming from file {start_file_index + 1}/{len(files)}, sub-band {start_sub_band}")
    print(f"  Sub-band: {args.sub_band_width} chans, overlap {args.overlap}")
    print(f"  Drift search: -{args.max_drift} to +{args.max_drift} Hz/s, SNR >= {args.snr}")

    all_results = {}

    # Record results from already-completed files
    for fname in completed_files:
        stem = os.path.splitext(fname)[0]
        hits_path = os.path.join(args.out, stem, f'{stem}_hits.json')
        with open(hits_path) as f:
            data = json.load(f)
            all_results[fname] = data['total_hits']

    # Process remaining files
    for i, filepath in enumerate(files):
        if i < start_file_index:
            continue
        fname = os.path.basename(filepath)
        sb_start = start_sub_band if i == start_file_index else 0

        hits = process_file(
            filepath, args.out,
            sub_band_chans=args.sub_band_width,
            overlap_chans=args.overlap,
            max_drift=args.max_drift,
            snr=args.snr,
            start_sub_band=sb_start,
            file_index=i,
            file_total=len(files),
        )
        all_results[fname] = len(hits)

        # File complete: update checkpoint with completed file list
        completed_files.append(fname)
        save_checkpoint(args.out, i + 1, len(files), fname,
                        sub_band_index=0, sub_band_total=0,
                        completed_files=completed_files)
        # Clean up partial hits file for this file
        stem = os.path.splitext(fname)[0]
        partial_path = os.path.join(args.out, stem, f'{stem}_partial_hits.json')
        if os.path.isfile(partial_path):
            os.remove(partial_path)

    # Clean up checkpoint on successful completion
    cp_path = os.path.join(args.out, 'checkpoint.json')
    if os.path.isfile(cp_path):
        os.remove(cp_path)
        print("  Checkpoint cleared (pipeline complete)")

    print(f"\n{'=' * 70}")
    print(f"PIPELINE COMPLETE")
    print(f"{'=' * 70}")
    for fname, count in all_results.items():
        print(f"  {fname}: {count} hits")
    print(f"  Total: {sum(all_results.values())} hits across {len(files)} files")


if __name__ == '__main__':
    main()
