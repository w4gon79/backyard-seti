#!/usr/bin/env python3
"""
incoherent_stack.py - Phase 2C: Incoherent power spectrum stacking.

Loads raw spectra from ON/OFF cadence files across multiple epochs,
subtracts OFF from ON in the observed frame (kills steady RFI),
applies barycentric correction, interpolates onto a common frequency grid,
and averages across epochs. Noise averages down as sqrt(N), persistent
signals average up linearly.

Usage (CLI):
    python incoherent_stack.py --target PROXCEN --freq-center 3000 --width 10
    python incoherent_stack.py --target PROXCEN --freq-center 3000 --width 10 --plot

Usage (importable):
    from incoherent_stack import run_stack_job, get_available_epochs
    epochs = get_available_epochs()
    result = run_stack_job(params, progress_callback=my_cb)

Author: Carl & Joel
Created: 2026-08-10
"""

import os
import sys
import argparse
import numpy as np
import json
import math
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'src'))
from barycentric_correct import (
    compute_barycentric_velocity,
    extract_mjd_from_filename,
    resolve_target_coords,
)

try:
    import rfi_zones
except ImportError:  # zones optional; stacking works unmasked without them
    rfi_zones = None

# ---------------------------------------------------------------------------
# Module-level data -- extensible from external code (e.g. dashboard/app.py)
# ---------------------------------------------------------------------------

# Data file locations (checked in order).
# 3B layout: G:\seti\data\fine = SSD staging (downloads + active scans,
# drains after each epoch); D:\seti_data\{TARGET}\fine = per-target
# archive; D:\seti_data\fine = legacy flat fallback.
DATA_STAGING = r'G:\seti\data\fine'
ARCHIVE_ROOT = r'D:\seti_data'
FINE_DIRS = [DATA_STAGING, os.path.join(ARCHIVE_ROOT, 'fine')]  # legacy compat


def _fine_dirs_for(target='PROXCEN'):
    """Ordered search dirs for one target: SSD staging, per-target
    archive, per-target on G:, legacy flat."""
    dirs = [DATA_STAGING]
    t = str(target).strip().upper() if target else ''
    if t:
        dirs.append(os.path.join(ARCHIVE_ROOT, t, 'fine'))
        dirs.append(os.path.join(r'G:\seti\data', t, 'fine'))
    dirs.append(os.path.join(ARCHIVE_ROOT, 'fine'))
    return dirs


def _all_fine_dirs():
    """Every fine dir that exists: staging, legacy flat, and all
    per-target archive dirs (for filename-only lookups like find_h5)."""
    dirs = [DATA_STAGING, os.path.join(ARCHIVE_ROOT, 'fine')]
    if os.path.isdir(ARCHIVE_ROOT):
        for d in sorted(os.listdir(ARCHIVE_ROOT)):
            sub = os.path.join(ARCHIVE_ROOT, d, 'fine')
            if os.path.isdir(sub) and sub not in dirs:
                dirs.append(sub)
    return dirs

# ---------------------------------------------------------------------------
# Epoch auto-discovery from data directories
# ---------------------------------------------------------------------------

import glob as _glob
import re as _re

# Fallback hardcoded epochs (used if auto-discovery misses something)
_HARDCODED_EPOCHS = {
    '57791': {
        'mjd_int': 57791,
        'seqs': [('72989', '73331'), ('73670', '74011'), ('74349', '74689')],
    },
    '57846': {
        'mjd_int': 57846,
        'seqs': [('49534', '49879'), ('50220', '50560'), ('50900', '51239')],
    },
    '57930': {
        'mjd_int': 57930,
        'seqs': [('41709', '42051'), ('42390', '42730'), ('43070', '43410')],
    },
    '58020': {
        'mjd_int': 58020,
        'seqs': [('21048', '21390'), ('21729', '22070'), ('22410', '22750')],
    },
}


def _discover_epochs(target='PROXCEN'):
    """Scan FINE_DIRS for target's fine-res files and build epoch dict.

    Filename pattern: Parkes_MJD_SEQ_TARGET_[SR]_fine.h5
    ON/OFF pairs are identified by _S (ON) and _R (OFF) suffixes.
    Pairs are formed by matching adjacent sequence numbers (S then R).
    """
    epochs = {}
    pat = _re.compile(r'Parkes_(\d+)_(\d+)_' + target + r'_[SR]_fine\.h5$')
    # GBT grammar: *_guppi_<MJD>_<SEQ>_<TARGET>_<SCAN>.PROD.TIER.h5
    # No S/R cadence markers. ABACAD: within an epoch the session target
    # is observed 3x (A-scans = ON) and each companion once (B/C/D = OFF).
    # ON/OFF pairs are built in seq order: each A-scan pairs with the
    # next companion observation. Companions are DIFFERENT targets, so
    # we must scan every guppi file in the data dirs for this epoch.
    gbt_pat = _re.compile(
        r'guppi_(\d+)_(\d+)_([A-Za-z0-9+\-.]+?)_\d+\.(?:rawspec|gpuspec)\.\d+\.h5$')

    gbt_by_mjd = {}  # mjd -> [(seq, target_tok, fname)]
    for d in _all_fine_dirs():
        if not os.path.isdir(d):
            continue
        for f in os.listdir(d):
            m = pat.match(f)
            if m:
                mjd, seq = m.group(1), m.group(2)
                is_on = ('_' + target + '_S_') in f
                tel = 'Parkes'
                if mjd not in epochs:
                    epochs[mjd] = {'mjd_int': int(mjd), 'telescope': tel,
                                   'files': [], 'seqs': []}
                epochs[mjd]['files'].append((seq, is_on, f))
            else:
                m = gbt_pat.search(f)
                if m:
                    gbt_by_mjd.setdefault(m.group(1), []).append(
                        (m.group(2), m.group(3).upper(), f))

    # Build GBT epochs for THIS target only (A-scans = files whose target
    # token matches). Pairs: each target file (seq order) + next companion.
    tgt_u = str(target).strip().upper()
    for mjd, entries in gbt_by_mjd.items():
        tgt_files = sorted([e for e in entries if e[1] == tgt_u],
                           key=lambda x: int(x[0]))
        if not tgt_files:
            continue
        companions = sorted([e for e in entries if e[1] != tgt_u],
                            key=lambda x: int(x[0]))
        pairs = []
        used_off = set()
        for seq, _, fname in tgt_files:
            nxt = [c for c in companions
                   if int(c[0]) > int(seq) and c[2] not in used_off]
            if nxt:
                used_off.add(nxt[0][2])
                pairs.append((fname, nxt[0][2]))
        epochs[mjd] = {
            'mjd_int': int(mjd), 'telescope': 'GBT',
            'files': [(e[0], e[1] == tgt_u, e[2]) for e in sorted(
                entries, key=lambda x: int(x[0]))],
            'seqs': pairs,  # GBT: seqs holds (on_fname, off_fname) pairs
            'gbt_pairs': pairs,
        }

    # Build ON/OFF pairs from discovered files
    for mjd, info in epochs.items():
        if info.get('telescope') == 'GBT':
            # Pairs already built from filenames above. Cadence check:
            # ABACAD = target observed 2+ times (normally 3) with
            # companions; each pair needs one companion.
            info['n_on'] = sum(1 for _, is_on, _ in info['files'] if is_on)
            info['n_off'] = len(info['files']) - info['n_on']
            info['cadence_ok'] = (info['n_on'] >= 2 and
                                  len(info['gbt_pairs']) >= 2)
            del info['files']
            continue
        files = sorted(info['files'], key=lambda x: x[0])
        pairs = []
        i = 0
        while i < len(files) - 1:
            seq_on, is_on, fname_on = files[i]
            seq_off, is_off, fname_off = files[i + 1]
            # Pair pattern: S (ON) followed by R (OFF)
            if is_on and not is_off:
                pairs.append((seq_on, seq_off))
                i += 2
            else:
                i += 1
        info['seqs'] = pairs
        # 3C cadence accounting: canonical Parkes fine epoch = 3 ON + 3 OFF
        info['n_on'] = sum(1 for _, is_on, _ in files if is_on)
        info['n_off'] = len(files) - info['n_on']
        if info.get('telescope') == 'Parkes':
            info['cadence_ok'] = (len(pairs) >= 3
                                  and info['n_on'] >= 3 and info['n_off'] >= 3)
        # Clean up: remove the files list, keep seqs
        del info['files']

    # Merge with hardcoded epochs for PROXCEN (hardcoded wins if discovery missed pairs)
    if target.upper() == 'PROXCEN':
        for mjd, info in _HARDCODED_EPOCHS.items():
            if mjd not in epochs:
                epochs[mjd] = info
            elif not epochs[mjd]['seqs']:
                epochs[mjd]['seqs'] = info['seqs']

    return dict(sorted(epochs.items()))


# Auto-discover at import time (default target)
EPOCHS = _discover_epochs()


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def get_available_epochs(target='PROXCEN'):
    """Return a dict of available epochs for the given target.

    Re-runs discovery each call so newly downloaded epochs appear immediately.
    """
    return _discover_epochs(target)


# ---------------------------------------------------------------------------
# Core functions (unchanged algorithm)
# ---------------------------------------------------------------------------

def find_h5(filename):
    """Find an HDF5 file across all known data directories."""
    for d in _all_fine_dirs():
        path = os.path.join(d, filename)
        if os.path.isfile(path):
            return path
    return None


# --- Direct HDF5 window readers (blimpy-free) -------------------------------
# blimpy's Waterfall(load_data=True, f_start, f_stop) decompresses full time
# rows of these ~12 GB fine files before trimming to the window: measured
# 4-8 GB RSS transients per file (dashboard peaked at 16.8 GB during a
# 100 MHz chunked stack, 2026-08-14). Plain h5py channel slicing needs the
# bitshuffle plugin registered, hence `import hdf5plugin` (the same one
# blimpy itself uses); with it, a 1M-channel block reads in ~0.01 s.

_H5_BLOCK_CHANS = 1_048_576  # matches on-disk chunk size of the fine files


def _h5_open_window(h5_path, f_start_mhz, f_stop_mhz):
    """Open a fine .h5 directly and locate the requested channel range.

    Returns (h5file, dataset, ch0, ch1, freqs_mhz) with freqs built from the
    header (fch1 + i*foff, MHz, float64). Caller must close h5file.
    Raises on any failure.
    """
    import h5py
    import hdf5plugin  # noqa: F401  registers bitshuffle decompressor

    f = h5py.File(h5_path, 'r')
    try:
        ds = f['data']
        attrs = ds.attrs
        fch1 = float(attrs['fch1'])   # MHz
        foff = float(attrs['foff'])   # MHz per channel (sign = direction)
        nchans = ds.shape[-1]

        i_a = (f_start_mhz - fch1) / foff
        i_b = (f_stop_mhz - fch1) / foff
        ch0 = max(0, min(nchans, int(math.floor(min(i_a, i_b)))))
        ch1 = max(0, min(nchans, int(math.ceil(max(i_a, i_b)))))
        if ch1 - ch0 < 2:
            raise ValueError(
                f'window [{f_start_mhz}, {f_stop_mhz}] MHz outside '
                f'{os.path.basename(h5_path)} (fch1={fch1}, foff={foff})')
        freqs = fch1 + np.arange(ch0, ch1, dtype=np.float64) * foff
        return f, ds, ch0, ch1, freqs
    except Exception:
        f.close()
        raise


def load_spectrum_window(h5_path, f_start_mhz, f_stop_mhz):
    """Load a frequency window from a fine HDF5 file as a 1D power spectrum.

    Direct h5py block reads with streaming time-average: peak RSS is one
    (n_tints x block_chans) float32 buffer instead of blimpy's full-file
    load. Averages all time integrations.
    Returns (freqs_mhz, power) or (None, None) on failure.
    """
    try:
        f, ds, ch0, ch1, freqs = _h5_open_window(h5_path, f_start_mhz, f_stop_mhz)
    except Exception as e:
        print(f"    ERROR opening {os.path.basename(h5_path)}: {e}")
        return None, None

    total = np.zeros(ch1 - ch0, dtype=np.float32)
    n_tints = 0
    try:
        try:
            for c0 in range(ch0, ch1, _H5_BLOCK_CHANS):
                c1 = min(c0 + _H5_BLOCK_CHANS, ch1)
                block = ds[:, 0, c0:c1]  # (n_tints, block_chans) float32
                total[c0 - ch0:c1 - ch0] = block.sum(axis=0, dtype=np.float32)
            n_tints = ds.shape[0]
        finally:
            f.close()
    except Exception as e:
        print(f"    ERROR reading {os.path.basename(h5_path)}: {e}")
        return None, None

    if n_tints == 0:
        return None, None
    return freqs, total / np.float32(n_tints)


def load_spectrum_window_2d(h5_path, f_start_mhz, f_stop_mhz):
    """Load a frequency window from a fine HDF5 file as a 2D time-series.

    Direct h5py block reads assembled into the final array: no full-file
    transient, only the returned (n_times, n_chans) float32 array itself.
    Returns (freqs_mhz, power_2d) or (None, None) on failure.
    """
    try:
        f, ds, ch0, ch1, freqs = _h5_open_window(h5_path, f_start_mhz, f_stop_mhz)
    except Exception as e:
        print(f"    ERROR opening 2D {os.path.basename(h5_path)}: {e}")
        return None, None

    out = np.empty((ds.shape[0], ch1 - ch0), dtype=np.float32)
    try:
        try:
            for c0 in range(ch0, ch1, _H5_BLOCK_CHANS):
                c1 = min(c0 + _H5_BLOCK_CHANS, ch1)
                out[:, c0 - ch0:c1 - ch0] = ds[:, 0, c0:c1]
        finally:
            f.close()
    except Exception as e:
        print(f"    ERROR reading 2D {os.path.basename(h5_path)}: {e}")
        return None, None

    return freqs, out


def build_common_grid(freq_center_mhz, width_mhz, chan_width_mhz=2.7939677e-6):
    """Build the common barycentric frequency grid.
    
    Uses the native Parkes channel width (2.79 Hz).
    Grid spans freq_center +/- width/2.
    """
    f_min = freq_center_mhz - width_mhz / 2
    f_max = freq_center_mhz + width_mhz / 2
    n_chans = int(width_mhz / chan_width_mhz)
    grid = np.linspace(f_min, f_max, n_chans)
    return grid


def process_epoch(epoch_label, epoch_info, target_ra, target_dec,
                  f_start_obs, f_stop_obs, common_grid, telescope='parkes',
                  progress_callback=None, return_time_series=False):
    """Process one epoch: load ON/OFF pairs, subtract, correct, interpolate.
    
    Returns the stacked (averaged) residual spectrum for this epoch,
    or None on failure.
    
    If *return_time_series* is True, also returns a 2D time-series residual
    (n_times, n_channels) from the first valid ON/OFF pair, interpolated
    onto the observed frequency grid.  The return value becomes
    (interpolated, time_series_2d) or (None, None).
    
    If *progress_callback* is provided it is called with a status dict at
    key milestones (pair load, epoch done).
    """
    mjd_int = epoch_info['mjd_int']
    seqs = epoch_info['seqs']
    
    print(f"\n  Epoch {epoch_label} (MJD {mjd_int}):")
    if progress_callback:
        progress_callback({
            'phase': 'epoch_start',
            'epoch': epoch_label,
            'mjd': mjd_int,
        })
    
    # Compute barycentric velocity for this epoch
    # Use the first ON file's MJD (most accurate)
    is_gbt = bool(epoch_info.get('gbt_pairs'))
    if is_gbt:
        first_on = epoch_info['gbt_pairs'][0][0]
    else:
        first_on = f"Parkes_{mjd_int}_{seqs[0][0]}_PROXCEN_S_fine.h5"
    mjd = extract_mjd_from_filename(first_on)
    v_bary = compute_barycentric_velocity(mjd, target_ra, target_dec, telescope)
    c = 299792458.0
    correction_factor = 1.0 - v_bary / c
    
    print(f"    MJD: {mjd:.5f}, velocity: {v_bary:.1f} m/s, correction: {correction_factor:.10f}")
    
    residuals = []  # One residual spectrum per ON/OFF pair
    
    for pair_idx, pair in enumerate(seqs):
        if is_gbt:
            on_file, off_file = pair  # actual filenames
        else:
            on_file = f"Parkes_{mjd_int}_{pair[0]}_PROXCEN_S_fine.h5"
            off_file = f"Parkes_{mjd_int}_{pair[1]}_PROXCEN_R_fine.h5"

        on_path = find_h5(on_file)
        off_path = find_h5(off_file)
        
        if not on_path or not off_path:
            print(f"    SKIP pair {on_seq}/{off_seq}: file not found")
            continue
        
        print(f"    Loading ON: {on_file}...", end='', flush=True)
        if progress_callback:
            progress_callback({
                'phase': 'file_load',
                'epoch': epoch_label,
                'file': on_file,
                'pair': pair_idx + 1,
                'total_pairs': len(seqs),
                'type': 'ON',
            })
        on_freqs, on_power = load_spectrum_window(on_path, f_start_obs, f_stop_obs)
        print(f" {len(on_power) if on_power is not None else 'FAIL'} chans")
        
        if on_freqs is None:
            continue
        
        print(f"    Loading OFF: {off_file}...", end='', flush=True)
        if progress_callback:
            progress_callback({
                'phase': 'file_load',
                'epoch': epoch_label,
                'file': off_file,
                'pair': pair_idx + 1,
                'total_pairs': len(seqs),
                'type': 'OFF',
            })
        off_freqs, off_power = load_spectrum_window(off_path, f_start_obs, f_stop_obs)
        print(f" {len(off_power) if off_power is not None else 'FAIL'} chans")
        
        if off_freqs is None:
            continue
        
        # Subtract OFF from ON in observed frame
        if len(off_freqs) != len(on_freqs) or not np.allclose(off_freqs, on_freqs, rtol=1e-12):
            off_power = np.interp(on_freqs, off_freqs, off_power)
        
        # Normalize each spectrum by its own median BEFORE subtracting.
        # Parkes ON/OFF = same star, bandpasses cancel on plain subtraction.
        # GBT ABACAD ON/OFF = DIFFERENT stars with wildly different flux and
        # receiver states (measured 14x DC offset between epochs, residual
        # medians in the billions): plain subtraction leaves bandpass garbage
        # that dominates everything (200 fake SNR-400 peaks, 2026-08-18).
        # Dividing by per-spectrum medians cancels the DC/bandpass offset
        # and makes residuals comparable across telescopes and epochs.
        on_med = np.median(on_power)
        off_med = np.median(off_power)
        if on_med != 0 and off_med != 0:
            residual = (on_power / on_med) - (off_power / off_med)
        else:
            residual = on_power - off_power  # kills steady RFI (legacy path)
        residuals.append((on_freqs, residual))
        print(f"    Residual: mean={np.mean(residual):.2e}, std={np.std(residual):.2e}")
    
    if not residuals:
        print(f"    No valid pairs for epoch {epoch_label}")
        if progress_callback:
            progress_callback({
                'phase': 'epoch_done',
                'epoch': epoch_label,
                'status': 'no_valid_pairs',
            })
        if return_time_series:
            return None, None
        return None
    
    # Average residuals across the 3 ON/OFF pairs within this epoch
    ref_freqs = residuals[0][0]
    avg_residual = np.zeros(len(ref_freqs), dtype=np.float32)
    count = 0
    for freqs, res in residuals:
        if len(freqs) != len(ref_freqs) or not np.allclose(freqs, ref_freqs, rtol=1e-12):
            res = np.interp(ref_freqs, freqs, res)
        avg_residual += res
        count += 1
    avg_residual /= count
    
    # Apply barycentric correction: f_bary = f_obs * (1 - v/c)
    bary_freqs = ref_freqs * correction_factor
    
    # Interpolate onto common barycentric grid
    sort_idx = np.argsort(bary_freqs)
    bary_freqs_sorted = bary_freqs[sort_idx]
    avg_residual_sorted = avg_residual[sort_idx]
    
    interpolated = np.interp(common_grid, bary_freqs_sorted, avg_residual_sorted).astype(np.float32)

    # Per-epoch RFI zones (observed frame) mapped through this epoch's
    # barycentric correction. Masked bins vote NaN in nanmean downstream.
    _zones = rfi_zones.epoch_zones(epoch_label) if rfi_zones is not None else []
    if _zones:
        _m = np.zeros(len(common_grid), dtype=bool)
        for _z in _zones:
            _m |= (common_grid >= _z['f_start'] * correction_factor) & \
                  (common_grid <= _z['f_stop'] * correction_factor)
        if _m.any():
            interpolated[_m] = np.nan
            _zd = ', '.join('{:.3f}-{:.3f}'.format(z['f_start'], z['f_stop'])
                            for z in _zones)
            print(f"    RFI zone mask: {int(_m.sum())} bins set NaN "
                  f"({_zd} MHz obs frame)")
    
    print(f"    Interpolated onto common grid: {len(common_grid)} bins")
    _fin = interpolated[np.isfinite(interpolated)]
    if _fin.size:
        print(f"    Stack spectrum: mean={np.mean(_fin):.2e}, std={np.std(_fin):.2e}")
    
    if progress_callback:
        progress_callback({
            'phase': 'epoch_done',
            'epoch': epoch_label,
            'status': 'ok',
            'n_pairs': count,
            'mean': float(np.mean(_fin)) if _fin.size else 0.0,
            'std': float(np.std(_fin)) if _fin.size else 0.0,
        })
    
    if return_time_series:
        # Build 2D time-series residual from the first valid ON/OFF pair.
        # We need to reload with 2D data for the first pair that produced a residual.
        time_series_2d = None
        for pair_idx, pair in enumerate(seqs):
            if is_gbt:
                on_file, off_file = pair
            else:
                on_file = f"Parkes_{mjd_int}_{pair[0]}_PROXCEN_S_fine.h5"
                off_file = f"Parkes_{mjd_int}_{pair[1]}_PROXCEN_R_fine.h5"
            on_path = find_h5(on_file)
            off_path = find_h5(off_file)
            if not on_path or not off_path:
                continue
            
            on_freqs_2d, on_2d = load_spectrum_window_2d(on_path, f_start_obs, f_stop_obs)
            if on_freqs_2d is None:
                continue
            off_freqs_2d, off_2d = load_spectrum_window_2d(off_path, f_start_obs, f_stop_obs)
            if off_freqs_2d is None:
                continue
            
            # Interpolate OFF onto ON frequency grid if needed
            if len(off_freqs_2d) != len(on_freqs_2d) or not np.allclose(off_freqs_2d, on_freqs_2d, rtol=1e-12):
                off_2d_interp = np.empty_like(on_2d)
                for t in range(off_2d.shape[0]):
                    off_2d_interp[t] = np.interp(on_freqs_2d, off_freqs_2d, off_2d[t])
                off_2d = off_2d_interp
            
            # Ensure same number of time steps (use min)
            n_times = min(on_2d.shape[0], off_2d.shape[0])
            residual_2d = on_2d[:n_times] - off_2d[:n_times]
            time_series_2d = residual_2d
            break
        
        return interpolated, time_series_2d
    
    return interpolated


def epoch_file_band_mhz(h5_path):
    """(lo, hi) MHz coverage of a fine h5 from its header. None on error."""
    try:
        import h5py
        with h5py.File(h5_path, 'r') as f:
            attrs = f['data'].attrs
            fch1 = float(attrs['fch1'])
            nchans = int(attrs['nchans'])
            foff = float(attrs['foff'])
        lo = min(fch1, fch1 + nchans * foff)
        hi = max(fch1, fch1 + nchans * foff)
        return (lo, hi)
    except Exception:
        return None


def compute_epoch_overlap(target, epoch_labels):
    """Shared frequency coverage (lo, hi) MHz across epochs of a target.

    Uses one representative file per epoch (first pair's ON file), reading
    the band from the h5 header. Returns None if the epochs share no
    coverage (or files are missing)."""
    epochs = _discover_epochs(target)
    ranges = []
    for label in epoch_labels:
        info = epochs.get(label)
        if not info:
            continue
        if info.get('gbt_pairs'):
            fname = info['gbt_pairs'][0][0]
        elif info.get('seqs'):
            fname = f"Parkes_{info['mjd_int']}_{info['seqs'][0][0]}_{target}_S_fine.h5"
        else:
            continue
        path = find_h5(fname)
        if not path:
            continue
        band = epoch_file_band_mhz(path)
        if band:
            ranges.append(band)
    if not ranges:
        return None
    lo = max(r[0] for r in ranges)
    hi = min(r[1] for r in ranges)
    return (lo, hi) if hi > lo else None


def clip_window_to_overlap(target, epoch_labels, freq_center, width):
    """Intersect the requested window with the epochs' shared coverage.

    Returns (center, width, overlap) with the window adjusted (shrunk or
    shifted) to the overlap, or (None, None, overlap) when there is no
    usable intersection."""
    ov = compute_epoch_overlap(target, epoch_labels)
    if ov is None:
        return None, None, None
    w_lo = max(freq_center - width / 2, ov[0])
    w_hi = min(freq_center + width / 2, ov[1])
    if w_hi <= w_lo:
        return None, None, ov
    return ((w_lo + w_hi) / 2.0, w_hi - w_lo, ov)


def find_peaks(spectrum, grid, n_sigma=5, min_channels=3):
    """Find peaks in the stacked spectrum above n*sigma threshold.
    
    Returns list of dicts with freq_mhz, power, snr, width_chans.
    """
    finite = spectrum[np.isfinite(spectrum)]
    if finite.size == 0:
        return []
    median = np.median(finite)
    mad = np.median(np.abs(finite - median))
    sigma = 1.4826 * mad  # convert MAD to sigma
    
    if not np.isfinite(sigma) or sigma == 0:
        return []
    
    threshold = median + n_sigma * sigma
    
    above = spectrum > threshold
    
    peaks = []
    i = 0
    while i < len(above):
        if above[i]:
            j = i
            while j < len(above) and above[j]:
                j += 1
            width = j - i
            if width >= 1:
                peak_idx = i + np.argmax(spectrum[i:j])
                peak_freq = grid[peak_idx]
                peak_power = spectrum[peak_idx]
                peak_snr = (peak_power - median) / sigma
                peaks.append({
                    'freq_mhz': float(peak_freq),
                    'power': float(peak_power),
                    'snr': float(peak_snr),
                    'width_chans': int(width),
                })
            i = j
        else:
            i += 1
    
    peaks.sort(key=lambda p: p['snr'], reverse=True)
    return peaks


# ---------------------------------------------------------------------------
# Internal helper: process_single_chunk
# ---------------------------------------------------------------------------

def process_single_chunk(target, freq_center, width, epoch_labels,
                          target_ra, target_dec, n_sigma, telescope,
                          progress_callback=None, _cb=None,
                          chunk_index=None, total_chunks=None,
                          spectrum_dir=None):
    """Core stacking logic for a single frequency window.

    Shared by both run_stack_job() and run_stack_job_chunked().

    Returns
    -------
    dict with keys:
        success     : bool
        peaks       : list of dicts
        stack       : np.ndarray (the stacked spectrum)
        common_grid : np.ndarray
        median      : float
        sigma       : float
        used_epochs : list of str
        n_epochs    : int
        epoch_info  : list of dicts
        epoch_spectra : list of np.ndarray (for plotting)
    """
    padding_mhz = 0.5
    f_start_obs = freq_center - width / 2 - padding_mhz
    f_stop_obs = freq_center + width / 2 + padding_mhz

    common_grid = build_common_grid(freq_center, width)

    # Re-discover epochs for THIS target: the module-level EPOCHS global
    # is from import time (PROXCEN default) and won't contain other
    # targets' epochs (GJ447 etc.)
    global EPOCHS
    if epoch_labels and any(l not in EPOCHS for l in epoch_labels):
        EPOCHS = _discover_epochs(target)

    epoch_spectra = []
    used_epochs = []
    epoch_info = []

    total_epochs = len(epoch_labels)
    for epoch_idx, label in enumerate(epoch_labels):
        if label not in EPOCHS:
            print(f"  Unknown epoch {label}, skipping")
            continue

        # Wrap callback to inject epoch_index, total_epochs, and chunk info
        def _epoch_cb(status, _idx=epoch_idx, _label=label):
            status['epoch_index'] = _idx
            status['total_epochs'] = total_epochs
            status['epoch_label'] = _label
            if chunk_index is not None:
                status['chunk_index'] = chunk_index
                status['total_chunks'] = total_chunks
            if _cb:
                _cb(status)

        spec = process_epoch(
            label, EPOCHS[label], target_ra, target_dec,
            f_start_obs, f_stop_obs, common_grid, telescope,
            progress_callback=_epoch_cb if (_cb or progress_callback) else None,
        )
        if spec is not None:
            if not np.isfinite(spec).any():
                print(f"  Epoch {label}: window fully masked by RFI zones, excluded")
                continue
            epoch_spectra.append(spec)
            used_epochs.append(label)
            _finite = spec[np.isfinite(spec)]
            ep_median = float(np.median(_finite))
            ep_mad = float(np.median(np.abs(_finite - ep_median)))
            ep_sigma = float(1.4826 * ep_mad)
            epoch_info.append({
                'label': label,
                'median': ep_median,
                'sigma': ep_sigma,
            })

    if len(epoch_spectra) < 2:
        return {
            'success': False,
            'error': f'Need at least 2 epochs for stacking, got {len(epoch_spectra)}',
        }

    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', RuntimeWarning)
        stack = np.nanmean(epoch_spectra, axis=0)
    n = len(epoch_spectra)

    _sfinite = stack[np.isfinite(stack)]
    if _sfinite.size == 0:
        return {'success': False,
                'error': 'All epochs masked by RFI zones in this window'}
    median = float(np.median(_sfinite))
    mad = float(np.median(np.abs(_sfinite - median)))
    sigma = float(1.4826 * mad)

    peaks = find_peaks(stack, common_grid, n_sigma=n_sigma)

    # Flush spectra to per-chunk .npy files when requested (chunked runs):
    # nothing big stays resident across chunks
    grid_path = power_path = None
    if spectrum_dir is not None:
        _sd = Path(spectrum_dir)
        _sd.mkdir(parents=True, exist_ok=True)
        grid_path = str(_sd / f'chunk_{chunk_index:03d}_grid.npy')
        power_path = str(_sd / f'chunk_{chunk_index:03d}_power.npy')
        np.save(grid_path, common_grid)
        np.save(power_path, stack.astype(np.float32, copy=False))

    return {
        'success': True,
        'peaks': peaks,
        'grid_path': grid_path,
        'power_path': power_path,
        'stack': stack,
        'common_grid': common_grid,
        'median': median,
        'sigma': sigma,
        'used_epochs': used_epochs,
        'n_epochs': n,
        'epoch_info': epoch_info,
        'epoch_spectra': epoch_spectra,
    }


# ---------------------------------------------------------------------------
# Spectrum artifact helpers (mmap-friendly .npy pair + meta)
# ---------------------------------------------------------------------------

def _save_spectrum_meta(json_path, n_bins):
    meta_path = json_path.replace('.json', '_meta.json')
    with open(meta_path, 'w') as f:
        json.dump({
            'n_bins': int(n_bins),
            'dtype_grid': 'float64',
            'dtype_power': 'float32',
        }, f)


def _save_spectrum_artifacts(json_path, grid, power):
    """Write grid/power .npy + meta.json next to a job's results JSON."""
    try:
        np.save(json_path.replace('.json', '_grid.npy'),
                np.asarray(grid, dtype=np.float64))
        np.save(json_path.replace('.json', '_power.npy'),
                np.asarray(power, dtype=np.float32))
        _save_spectrum_meta(json_path, len(grid))
        print(f"Spectrum artifacts saved: {json_path.replace('.json', '_grid.npy')}")
        return True
    except Exception as e:
        print(f"Spectrum artifact save failed: {e}")
        return False


def _merge_chunk_npies(pairs, json_path):
    """Concatenate per-chunk grid/power .npy files into full-band artifacts.

    Streams through memmaps so peak RAM stays ~one chunk regardless of
    total band size (207M bins for the full 580 MHz band). Returns the
    merged bin count, or None on failure.
    """
    import numpy.lib.format as _fmt
    grid_out = json_path.replace('.json', '_grid.npy')
    power_out = json_path.replace('.json', '_power.npy')
    try:
        shapes = []
        n_total = 0
        for gp, pp in pairs:
            g = np.load(gp, mmap_mode='r')
            shapes.append(g.shape[0])
            n_total += g.shape[0]
        grid_mm = _fmt.open_memmap(grid_out, mode='w+', dtype=np.float64,
                                   shape=(n_total,))
        power_mm = _fmt.open_memmap(power_out, mode='w+', dtype=np.float32,
                                    shape=(n_total,))
        pos = 0
        for (gp, pp), n in zip(pairs, shapes):
            grid_mm[pos:pos + n] = np.load(gp, mmap_mode='r')
            power_mm[pos:pos + n] = np.load(pp, mmap_mode='r')
            pos += n
        grid_mm.flush()
        power_mm.flush()
        del grid_mm, power_mm
        _save_spectrum_meta(json_path, n_total)
        print(f"Merged spectrum: {n_total} bins -> {grid_out}")
        return n_total
    except Exception as e:
        print(f"Spectrum merge failed: {e}")
        return None


# ---------------------------------------------------------------------------
# Importable API: run_stack_job
# ---------------------------------------------------------------------------

def run_stack_job(params, progress_callback=None):
    """Run the full incoherent stacking pipeline, importable from Flask.
    
    Parameters
    ----------
    params : dict
        Required keys:
            target          : str   (e.g. 'PROXCEN')
            freq_center     : float  (MHz)
            width           : float  (MHz)
            epochs          : list of str  (epoch labels)
            n_sigma         : float  (peak threshold)
            telescope       : str   (e.g. 'parkes')
            output_png      : str or None  (path to save plot)
            output_json     : str or None  (path to save JSON results)
    progress_callback : callable or None
        Called with a status dict at milestones:
            phase='start'            -- job initialised
            phase='epoch_start'      -- beginning an epoch
            phase='file_load'        -- loading an HDF5 file
            phase='epoch_done'       -- epoch finished
            phase='stacking'         -- all epochs done, averaging
            phase='peak_finding'     -- searching for peaks
            phase='plotting'         -- generating plot
            phase='complete'         -- all done (results inside)
            phase='error'            -- fatal error (message field)
    
    Returns
    -------
    dict with keys:
        peaks, stack_median, stack_sigma, used_epochs, n_epochs,
        snr_improvement, epoch_info, grid_n_bins, success
    """
    def _cb(status):
        if progress_callback:
            try:
                progress_callback(status)
            except Exception:
                pass  # never let a callback error kill the job

    target = params['target']
    freq_center = params['freq_center']
    width = params['width']
    epoch_labels = params.get('epochs', list(EPOCHS.keys()))
    n_sigma = params.get('n_sigma', 5.0)
    telescope = params.get('telescope', 'parkes')
    output_png = params.get('output_png')
    output_json = params.get('output_json')

    _cb({'phase': 'start', 'target': target, 'freq_center': freq_center,
         'width': width, 'epochs': epoch_labels})

    # Resolve target coordinates (registry-first; legacy dict retired)
    target_ra, target_dec, _src = resolve_target_coords(target)
    if target_ra is None:
        msg = f"Unknown target: {target} (not in target registry)"
        _cb({'phase': 'error', 'message': msg})
        return {'success': False, 'error': msg}

    # Use shared helper for the core stacking logic
    _cb({'phase': 'stacking', 'n_epochs': len(epoch_labels)})

    chunk_result = process_single_chunk(
        target=target,
        freq_center=freq_center,
        width=width,
        epoch_labels=epoch_labels,
        target_ra=target_ra,
        target_dec=target_dec,
        n_sigma=n_sigma,
        telescope=telescope,
        _cb=_cb,
    )

    if not chunk_result['success']:
        _cb({'phase': 'error', 'message': chunk_result['error']})
        return {'success': False, 'error': chunk_result['error']}

    stack = chunk_result['stack']
    common_grid = chunk_result['common_grid']
    peaks = chunk_result['peaks']
    used_epochs = chunk_result['used_epochs']
    n = chunk_result['n_epochs']
    median = chunk_result['median']
    sigma = chunk_result['sigma']
    epoch_info = chunk_result['epoch_info']
    epoch_spectra = chunk_result['epoch_spectra']

    _cb({'phase': 'peak_finding', 'n_sigma': n_sigma})

    # Plot
    if output_png:
        _cb({'phase': 'plotting', 'output_png': output_png})
        try:
            import matplotlib
            matplotlib.use('Agg')
            import matplotlib.pyplot as plt

            fig, axes = plt.subplots(
                len(epoch_spectra) + 1, 1,
                figsize=(14, 3 * (len(epoch_spectra) + 1)),
                sharex=True,
            )

            for i, label in enumerate(used_epochs):
                axes[i].plot(common_grid, epoch_spectra[i],
                             linewidth=0.3, color='blue', alpha=0.5)
                axes[i].set_ylabel(f'Epoch {label}\nPower')
                axes[i].axhline(np.nanmedian(epoch_spectra[i]),
                                color='green', linestyle='--', alpha=0.5)

            axes[-1].plot(common_grid, stack, linewidth=0.3, color='red')
            axes[-1].set_ylabel(f'Stacked (N={n})\nPower')
            axes[-1].set_xlabel('Barycentric Frequency (MHz)')

            for p in peaks[:10]:
                axes[-1].axvline(p['freq_mhz'], color='orange',
                                 alpha=0.7, linewidth=0.5)

            fig.suptitle(
                f'Incoherent Stack - {target} - {freq_center} MHz '
                f'({width} MHz window)',
                fontsize=14,
            )
            plt.tight_layout()
            plt.savefig(output_png, dpi=150)
            plt.close(fig)
            print(f"Plot saved: {output_png}")
        except Exception as e:
            print(f"Plot failed: {e}")

    # Assemble results.
    # Inline grid/power lists only for narrow windows; wide ones are carried
    # by the on-disk .npy artifacts (see run_stack_job_chunked).
    _MAX_INLINE_BINS = 1_000_000
    if len(common_grid) <= _MAX_INLINE_BINS:
        grid_freqs_list = common_grid.tolist()
        stack_power_list = stack.tolist()
        # NaN (RFI-zoned bins) -> None so browser JSON.parse doesn't choke
        stack_power_list = [None if v != v else v for v in stack_power_list]
    else:
        grid_freqs_list, stack_power_list = [], []
    results = {
        'success': True,
        'target': target,
        'freq_center_mhz': freq_center,
        'width_mhz': width,
        'epochs': used_epochs,
        'n_epochs': len(used_epochs),
        'snr_improvement': float(np.sqrt(n)),
        'n_peaks': len(peaks),
        'peaks': peaks[:50],
        'stack_median': median,
        'stack_sigma': sigma,
        'epoch_info': epoch_info,
        'grid_n_bins': len(common_grid),
        'grid_freqs': grid_freqs_list,
        'stack_power': stack_power_list,
    }

    # Save JSON (without large arrays for readability)
    if output_json:
        results_compact = {k: v for k, v in results.items()
                          if k not in ('grid_freqs', 'stack_power')}
        with open(output_json, 'w') as f:
            json.dump(results_compact, f, indent=2)
        print(f"Results saved: {output_json}")

    # Save spectrum as raw .npy pair + meta for Plotly rendering across
    # restarts (mmap-friendly; the old .npz forced a full decompress-load
    # per spectrum view)
    if output_json:
        _save_spectrum_artifacts(output_json, common_grid, stack)

    _cb({'phase': 'complete', 'results': {k: v for k, v in results.items()
                                          if k != 'peaks'},
         'n_peaks': len(peaks)})

    return results


# ---------------------------------------------------------------------------
# Importable API: run_stack_job_chunked (full-band processing with recovery)
# ---------------------------------------------------------------------------

def run_stack_job_chunked(params, progress_callback=None):
    """Run incoherent stacking across a wide band, split into chunks.

    Each chunk is processed independently through the full pipeline
    (load ON/OFF, subtract, barycentric correct, interpolate, stack,
    find peaks). Results are saved to disk after each chunk completes,
    enabling crash recovery and resume.

    Parameters
    ----------
    params : dict
        Required keys (same as run_stack_job plus):
            target          : str
            freq_center     : float  (MHz) -- centre of the FULL band
            width           : float  (MHz) -- total bandwidth
            epochs          : list of str
            n_sigma         : float
            telescope       : str
            output_png      : str or None
            output_json     : str or None  (combined results)
        Additional keys:
            chunk_size_mhz  : float  (default 50.0)
            output_dir      : str    (directory for per-chunk JSON files)
    progress_callback : callable or None
        Same phases as run_stack_job, plus:
            phase='chunk_start'   -- beginning a chunk
            phase='chunk_done'    -- chunk finished
            phase='chunk_skipped' -- chunk already processed (resume)
        Epoch-level callbacks include chunk_index and total_chunks.

    Returns
    -------
    dict (same format as run_stack_job, with all peaks from all chunks)
    """
    def _cb(status):
        if progress_callback:
            try:
                progress_callback(status)
            except Exception:
                pass

    target = params['target']
    freq_center = params['freq_center']
    width = params['width']
    epoch_labels = params.get('epochs', list(EPOCHS.keys()))
    n_sigma = params.get('n_sigma', 5.0)
    telescope = params.get('telescope', 'parkes')
    output_png = params.get('output_png')
    output_json = params.get('output_json')
    chunk_size_mhz = params.get('chunk_size_mhz', 50.0)
    output_dir = Path(params.get('output_dir', '.'))

    output_dir.mkdir(parents=True, exist_ok=True)

    _cb({'phase': 'start', 'target': target, 'freq_center': freq_center,
         'width': width, 'epochs': epoch_labels,
         'chunked': True, 'chunk_size_mhz': chunk_size_mhz})

    # Resolve target coordinates (registry-first; legacy dict retired)
    target_ra, target_dec, _src = resolve_target_coords(target)
    if target_ra is None:
        msg = f"Unknown target: {target} (not in target registry)"
        _cb({'phase': 'error', 'message': msg})
        return {'success': False, 'error': msg}
    full_band_start = freq_center - width / 2
    full_band_end = freq_center + width / 2
    n_chunks = max(1, math.ceil(width / chunk_size_mhz))

    print(f"\nChunked stacking: {width:.1f} MHz band in {n_chunks} chunks "
          f"of ~{chunk_size_mhz:.1f} MHz each")

    all_peaks = []
    chunk_results = []  # (chunk_idx, grid, stack) for combined plot

    for i in range(n_chunks):
        chunk_start = full_band_start + chunk_size_mhz * i
        chunk_end = min(chunk_start + chunk_size_mhz, full_band_end)
        chunk_width = chunk_end - chunk_start
        chunk_center = (chunk_start + chunk_end) / 2

        chunk_file = output_dir / f'chunk_{i:03d}.json'

        # --- Resume support: skip if chunk file exists ---
        if chunk_file.exists():
            _gp = output_dir / f'chunk_{i:03d}_grid.npy'
            _pp = output_dir / f'chunk_{i:03d}_power.npy'
            if not (_gp.exists() and _pp.exists()):
                print(f"  Chunk {i+1}: JSON saved but spectra .npy missing, "
                      f"reprocessing for spectrum artifacts")
            else:
                try:
                    with open(chunk_file, 'r') as f:
                        saved = json.load(f)
                    all_peaks.extend(saved.get('peaks', []))
                    chunk_results.append((i, saved, str(_gp), str(_pp)))
                    print(f"\n  Chunk {i+1}/{n_chunks}: SKIPPED (already saved, "
                          f"{len(saved.get('peaks', []))} peaks)")
                    _cb({
                        'phase': 'chunk_skipped',
                        'chunk_index': i,
                        'total_chunks': n_chunks,
                        'n_peaks': len(saved.get('peaks', [])),
                    })
                    continue
                except (json.JSONDecodeError, KeyError) as e:
                    print(f"  Chunk {i+1}: corrupt chunk file, reprocessing ({e})")
                    # Fall through to reprocess

        # --- Process this chunk ---
        print(f"\n  Chunk {i+1}/{n_chunks}: {chunk_center:.4f} MHz "
              f"({chunk_width:.2f} MHz wide)")
        _cb({
            'phase': 'chunk_start',
            'chunk_index': i,
            'total_chunks': n_chunks,
            'chunk_center': chunk_center,
            'chunk_width': chunk_width,
        })

        chunk_result = process_single_chunk(
            target=target,
            freq_center=chunk_center,
            width=chunk_width,
            epoch_labels=epoch_labels,
            target_ra=target_ra,
            target_dec=target_dec,
            n_sigma=n_sigma,
            telescope=telescope,
            _cb=_cb,
            chunk_index=i,
            total_chunks=n_chunks,
            spectrum_dir=str(output_dir),
        )

        if not chunk_result['success']:
            print(f"  Chunk {i+1} FAILED: {chunk_result.get('error')}")
            # Save empty result so resume doesn't get stuck
            chunk_data = {
                'chunk_index': i,
                'freq_center': chunk_center,
                'width': chunk_width,
                'peaks': [],
                'stack_median': None,
                'stack_sigma': None,
                'error': chunk_result.get('error'),
            }
            with open(chunk_file, 'w') as f:
                json.dump(chunk_data, f, indent=2)
            _cb({
                'phase': 'chunk_done',
                'chunk_index': i,
                'total_chunks': n_chunks,
                'n_peaks': 0,
                'status': 'failed',
            })
            continue

        # Save chunk results immediately
        chunk_data = {
            'chunk_index': i,
            'freq_center': chunk_center,
            'width': chunk_width,
            'peaks': chunk_result['peaks'],
            'stack_median': chunk_result['median'],
            'stack_sigma': chunk_result['sigma'],
            'epoch_info': chunk_result.get('epoch_info', []),
        }
        with open(chunk_file, 'w') as f:
            json.dump(chunk_data, f, indent=2)
        print(f"  Chunk {i+1} saved: {chunk_file} "
              f"({len(chunk_result['peaks'])} peaks)")

        all_peaks.extend(chunk_result['peaks'])
        chunk_results.append((i, chunk_data,
                              chunk_result.get('grid_path'),
                              chunk_result.get('power_path')))
        # Spectra are on disk now; drop the array refs so the next chunk's
        # blimpy load starts from a clean slate
        chunk_result.pop('stack', None)
        chunk_result.pop('common_grid', None)
        chunk_result.pop('epoch_spectra', None)

        _cb({
            'phase': 'chunk_done',
            'chunk_index': i,
            'total_chunks': n_chunks,
            'n_peaks': len(chunk_result['peaks']),
        })

    # --- Final merge ---
    all_peaks.sort(key=lambda p: p['snr'], reverse=True)

    # Compute combined stats from all chunk results
    # Chunks loaded from disk (resume): (idx, saved_dict)
    # Chunks processed fresh: (idx, chunk_data, grid, stack)
    all_medians = []
    all_sigmas = []
    for cr in chunk_results:
        saved = cr[1] if isinstance(cr[1], dict) else cr[1]
        m = saved.get('stack_median')
        s = saved.get('stack_sigma')
        if m is not None:
            all_medians.append(m)
        if s is not None:
            all_sigmas.append(s)
    combined_median = float(np.mean(all_medians)) if all_medians else 0.0
    combined_sigma = float(np.mean(all_sigmas)) if all_sigmas else 0.0

    # Determine used epochs from the first successful chunk
    used_epochs = epoch_labels  # fallback
    n_epochs = len(used_epochs)

    # --- Merge chunk spectra on disk BEFORE plotting ---
    # Streams per-chunk .npy files into full-band artifacts through memmaps:
    # peak RAM ~ one chunk instead of the whole 207M-bin band.
    _merge_pairs = []
    for cr in chunk_results:
        if len(cr) >= 4 and cr[2] and cr[3] \
                and os.path.isfile(cr[2]) and os.path.isfile(cr[3]):
            _merge_pairs.append((cr[2], cr[3]))
    merged_bins = None
    if output_json and _merge_pairs:
        merged_bins = _merge_chunk_npies(_merge_pairs, output_json)

    # Combined plot from the merged on-disk spectrum (decimated for draw)
    if output_png:
        _cb({'phase': 'plotting', 'output_png': output_png})
        try:
            import matplotlib
            matplotlib.use('Agg')
            import matplotlib.pyplot as plt

            grid_mm = power_mm = None
            if merged_bins:
                grid_mm = np.load(output_json.replace('.json', '_grid.npy'),
                                  mmap_mode='r')
                power_mm = np.load(output_json.replace('.json', '_power.npy'),
                                   mmap_mode='r')
            else:
                _cands = [cr for cr in chunk_results
                          if len(cr) >= 4 and cr[2] and os.path.isfile(cr[2])]
                if _cands:
                    grid_mm = np.load(_cands[0][2], mmap_mode='r')
                    power_mm = np.load(_cands[0][3], mmap_mode='r')

            if grid_mm is not None:
                stride = max(1, len(grid_mm) // 2_000_000)
                fig, ax = plt.subplots(1, 1, figsize=(16, 4))
                ax.plot(grid_mm[::stride], power_mm[::stride],
                        linewidth=0.2, color='red')
                ax.set_ylabel(f'Stacked (N={n_epochs})\nPower')
                ax.set_xlabel('Barycentric Frequency (MHz)')

                for p in all_peaks[:20]:
                    ax.axvline(p['freq_mhz'], color='orange',
                               alpha=0.5, linewidth=0.3)

                fig.suptitle(
                    f'Incoherent Stack (Chunked) - {target} - '
                    f'{freq_center - width/2:.0f}-{freq_center + width/2:.0f} MHz '
                    f'({width:.0f} MHz, {n_chunks} chunks)',
                    fontsize=14,
                )
                plt.tight_layout()
                plt.savefig(output_png, dpi=150)
                plt.close(fig)
                print(f"Combined plot saved: {output_png}")
        except Exception as e:
            print(f"Combined plot failed: {e}")

    # Collect epoch_info from the first successful chunk (all chunks process
    # the same epochs, so any chunk's epoch_info is representative)
    combined_epoch_info = []
    for cr in chunk_results:
        saved = cr[1] if isinstance(cr[1], dict) else cr[1]
        ei = saved.get('epoch_info', [])
        if ei:
            combined_epoch_info = ei
            break

    # Assemble combined results. Wide-band spectra live in the merged .npy
    # artifacts on disk; /api/stack/spectrum reads them via mmap.
    grid_n_bins = merged_bins if merged_bins else (
        int(width / 2.7939677e-6))  # fallback estimate

    results = {
        'success': True,
        'target': target,
        'freq_center_mhz': freq_center,
        'width_mhz': width,
        'epochs': used_epochs,
        'n_epochs': n_epochs,
        'snr_improvement': float(np.sqrt(n_epochs)),
        'n_peaks': len(all_peaks),
        'peaks': all_peaks[:200],
        'stack_median': combined_median,
        'stack_sigma': combined_sigma,
        'epoch_info': combined_epoch_info,
        'grid_n_bins': grid_n_bins,
        'grid_freqs': [],
        'stack_power': [],
        'has_spectrum_npy': bool(merged_bins),
        'chunked': True,
        'n_chunks': n_chunks,
        'chunk_size_mhz': chunk_size_mhz,
    }

    # Save combined JSON (exclude large arrays - they're in the .npz)
    if output_json:
        results_compact = {k: v for k, v in results.items()
                          if k not in ('grid_freqs', 'stack_power')}
        with open(output_json, 'w') as f:
            json.dump(results_compact, f, indent=2)
        print(f"Combined results saved: {output_json}")

    _cb({'phase': 'complete',
         'results': {k: v for k, v in results.items()
                     if k not in ('peaks', 'grid_freqs', 'stack_power')},
         'n_peaks': len(all_peaks)})

    return results


# ---------------------------------------------------------------------------
# CLI (backward compatible)
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description='Incoherent power spectrum stacking across epochs (Phase 2C)')
    parser.add_argument('--target', default='PROXCEN', help='Target name')
    parser.add_argument('--freq-center', type=float, default=3000.0,
                        help='Center frequency in MHz')
    parser.add_argument('--width', type=float, default=10.0,
                        help='Frequency window width in MHz')
    parser.add_argument('--epochs', nargs='*', default=None,
                        help='Epoch labels to use (default: all available)')
    parser.add_argument('--telescope', default='parkes', help='Telescope name')
    parser.add_argument('--n-sigma', type=float, default=5.0,
                        help='Peak detection threshold in sigma')
    parser.add_argument('--plot', action='store_true',
                        help='Save matplotlib plot to PNG')
    parser.add_argument('--output', default=None,
                        help='Save results to JSON file')

    args = parser.parse_args()

    epoch_labels = args.epochs if args.epochs else list(EPOCHS.keys())

    # Build output paths (CLI mode -- write to CWD)
    stem = f'stack_{args.target}_{args.freq_center:.0f}MHz_{args.width:.0f}MHz'

    output_png = f'{stem}.png' if args.plot else None
    output_json = args.output or f'{stem}.json'

    params = {
        'target': args.target,
        'freq_center': args.freq_center,
        'width': args.width,
        'epochs': epoch_labels,
        'n_sigma': args.n_sigma,
        'telescope': args.telescope,
        'output_png': output_png,
        'output_json': output_json,
    }

    # Print header info (CLI nicety)
    target_ra, target_dec, _src = resolve_target_coords(args.target)
    print(f"Target: {args.target} (RA={target_ra}h, Dec={target_dec}deg)")
    print(f"Epochs: {epoch_labels}")
    common_grid = build_common_grid(args.freq_center, args.width)
    print(f"Frequency window: {args.freq_center - args.width/2:.4f} - "
          f"{args.freq_center + args.width/2:.4f} MHz")
    padding_mhz = 0.5
    f_start_obs = args.freq_center - args.width / 2 - padding_mhz
    f_stop_obs = args.freq_center + args.width / 2 + padding_mhz
    print(f"Observed load range: {f_start_obs:.4f} - {f_stop_obs:.4f} MHz "
          f"(with {padding_mhz} MHz padding)")
    print(f"Common grid: {len(common_grid)} bins, "
          f"{2.7939677e-6 * 1e6:.4f} Hz/bin")

    results = run_stack_job(params, progress_callback=None)

    if not results.get('success'):
        sys.exit(1)

    # CLI-only summary prints
    n = results['n_epochs']
    sigma = results['stack_sigma']
    median = results['stack_median']
    print(f"\n{'='*60}")
    print(f"STACKING COMPLETE: {n} epochs")
    print(f"{'='*60}")
    print(f"Epochs stacked: {n}")
    print(f"Theoretical SNR improvement: sqrt({n}) = {np.sqrt(n):.2f}x")
    print(f"Stack noise: median={median:.4e}, sigma={sigma:.4e}")

    peaks = results['peaks']
    print(f"\nPeaks above {args.n_sigma} sigma:")
    if not peaks:
        print(f"  No peaks found")
    else:
        print(f"  Found {len(peaks)} peaks:")
        for i, p in enumerate(peaks[:20]):
            print(f"    #{i+1}: {p['freq_mhz']:.6f} MHz  "
                  f"SNR={p['snr']:.2f}  width={p['width_chans']} chans")

    print(f"\nPer-epoch noise (for SNR comparison):")
    for ei in results['epoch_info']:
        ratio = ei['sigma'] / sigma if sigma else 0
        print(f"  Epoch {ei['label']}: sigma={ei['sigma']:.4e}  "
              f"(stack sigma={sigma:.4e}, ratio={ratio:.2f}x)")

    print(f"\nResults saved: {output_json}")
    if output_png:
        print(f"Plot saved: {output_png}")


if __name__ == '__main__':
    main()
