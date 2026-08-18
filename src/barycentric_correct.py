#!/usr/bin/env python3
"""
barycentric_correct.py - Barycentric velocity correction for SETI hit analysis.

Converts observed topocentric frequencies to solar-system-barycentric (rest-frame)
frequencies so that a signal from a distant source appears at the same frequency
across multiple observation epochs.  RFI (terrestrial) will NOT align after
correction, which enables cross-epoch RFI rejection.

Uses astropy to compute the Earth's velocity along the line of sight to the
target at the observation time, then applies the non-relativistic Doppler
correction:

    f_bary = f_obs * (1 - v_los / c)

where v_los is the barycentric radial velocity correction (positive = receding).

Author: Carl & Joel
Created: 2026-08-08
"""

import os
import sys
import json
import glob as glob_module
import numpy as np
from pathlib import Path

# Astropy imports
from astropy.time import Time
from astropy.coordinates import SkyCoord, EarthLocation
from astropy import units as u
import astropy.constants as const


# ─── Telescope Database ───────────────────────────────────────────────

TELESCOPE_LOCATIONS = {
    'parkes': {
        'name': 'Parkes Observatory',
        'lat': -32.9999,
        'lon': 148.2621,
        'elev': 414.0,
    },
    'gbt': {
        'name': 'Green Bank Telescope',
        'lat': 38.4331,
        'lon': -79.8397,
        'elev': 807.0,
    },
    'fast': {
        'name': 'FAST',
        'lat': 25.6525,
        'lon': 106.8566,
        'elev': 1110.0,
    },
    'meerkat': {
        'name': 'MeerKAT',
        'lat': -30.7210,
        'lon': 21.4111,
        'elev': 1086.0,
    },
    'effelsberg': {
        'name': 'Effelsberg',
        'lat': 50.5247,
        'lon': 6.8836,
        'elev': 415.0,
    },
}

# Telescope ID mapping from HDF5 headers (turbo_seti / blimpy convention)
TELESCOPE_ID_MAP = {
    0: 'parkes',   #blimpy uses 0 for Parkes in some files
    6: 'parkes',
    4: 'gbt',
    # Add more as needed
}


# ─── Target Coordinate Database ───────────────────────────────────────

# Legacy TARGET_COORDS dict retired 2026-08-15: the SQLite target
# registry (target_registry.py) is the single source of truth for target
# coordinates. resolve_target_coords() below resolves manual coords >
# registry; no static fallback remains.


# ─── Core Barycentric Correction ──────────────────────────────────────

def _get_earth_location(telescope='parkes'):
    """Get EarthLocation for a telescope name or ID."""
    if isinstance(telescope, int):
        telescope = TELESCOPE_ID_MAP.get(telescope, 'parkes')
    if isinstance(telescope, str):
        telescope = telescope.lower()
    if telescope not in TELESCOPE_LOCATIONS:
        raise ValueError(f"Unknown telescope: {telescope}. Known: {list(TELESCOPE_LOCATIONS.keys())}")
    t = TELESCOPE_LOCATIONS[telescope]
    return EarthLocation(
        lat=t['lat'] * u.deg,
        lon=t['lon'] * u.deg,
        height=t['elev'] * u.m,
    )


def compute_barycentric_velocity(mjd, ra_hours, dec_deg, telescope='parkes'):
    """
    Compute the barycentric line-of-sight velocity correction.

    Parameters
    ----------
    mjd : float
        Modified Julian Date of the observation (UTC).
    ra_hours : float
        Right ascension of the target in hours (e.g. 14.495 for Proxima).
    dec_deg : float
        Declination of the target in degrees (e.g. -62.68 for Proxima).
    telescope : str or int
        Telescope name ('parkes', 'gbt', ...) or blimpy telescope_id integer.

    Returns
    -------
    float
        Barycentric velocity along line of sight in m/s.
        Positive = receding (Earth moving away from target).
        Negative = approaching (Earth moving toward target).
    """
    t = Time(mjd, format='mjd', scale='utc')
    coord = SkyCoord(ra=ra_hours * u.hour, dec=dec_deg * u.deg)
    loc = _get_earth_location(telescope)

    # radial_velocity_correction gives the velocity to ADD to observed
    # radial velocities to correct them.  Positive = receding.
    v = coord.radial_velocity_correction(obstime=t, location=loc)
    return v.to(u.m / u.s).value


def correct_frequency(freq_mhz, velocity_mps):
    """
    Apply barycentric correction to an observed frequency.

    Parameters
    ----------
    freq_mhz : float
        Observed topocentric frequency in MHz.
    velocity_mps : float
        Barycentric velocity correction in m/s (positive = receding).

    Returns
    -------
    float
        Barycentric (rest-frame) frequency in MHz.
    """
    c = const.c.to(u.m / u.s).value  # 299,792,458 m/s
    return freq_mhz * (1.0 - velocity_mps / c)


def correct_frequency_array(freqs_mhz, velocity_mps):
    """Vectorized frequency correction for a numpy array of frequencies."""
    c = const.c.to(u.m / u.s).value
    return freqs_mhz * (1.0 - velocity_mps / c)


# ─── MJD Extraction ───────────────────────────────────────────────────

def extract_mjd_from_filename(filename):
    """
    Extract MJD from a BL filename.
    
    Example: Parkes_57791_72989_PROXCEN_S_fine.h5 -> 57791.72989
    Example: spliced_blc00_guppi_57532_03272_GJ447_0009.gpuspec.0000.h5 -> 57532
    """
    parts = filename.replace('.h5', '').split('_')
    # GBT grammar: (spliced_)blcNN_guppi_MJD_SEQ_TARGET_SCAN.PROD.TIER
    # MJD is the token immediately after 'guppi'; integer only (no fraction).
    if 'guppi' in parts:
        i = parts.index('guppi')
        if i + 1 < len(parts):
            try:
                return float(parts[i + 1])
            except ValueError:
                pass
    if len(parts) >= 3:
        try:
            mjd_int = int(parts[1])
            mjd_frac = int(parts[2]) / 100000.0  # 5-digit fractional day
            return mjd_int + mjd_frac
        except (ValueError, IndexError):
            pass
    # Fallback: try to get from HDF5 header
    return None


def get_mjd_from_h5(h5_path):
    """Read tstart (MJD) from HDF5 file header."""
    try:
        import h5py
        with h5py.File(h5_path, 'r') as f:
            if 'data' in f and 'tstart' in f['data'].attrs:
                return float(f['data'].attrs['tstart'])
    except Exception:
        pass
    return None


def get_coords_from_h5(h5_path):
    """Read RA (hours) and Dec (degrees) from HDF5 header."""
    try:
        import h5py
        with h5py.File(h5_path, 'r') as f:
            if 'data' in f:
                attrs = f['data'].attrs
                ra = float(attrs.get('src raj', 0))
                dec = float(attrs.get('src dej', 0))
                telescope_id = int(attrs.get('telescope_id', 6))
                return ra, dec, telescope_id
    except Exception:
        pass
    return None, None, None


def _h5_search_dirs(target_name=None):
    """Data dirs searched for h5 files: per-target archive (3B) first,
    then staging/legacy. Relative dirs are seti_root-relative; D: dirs
    are absolute."""
    dirs = ['data/fine', 'data/mid', 'data/h5']
    t = (str(target_name).strip().upper().replace(' ', '_')
         if target_name else '')
    if t:
        dirs.insert(0, os.path.join('data', t))
        dirs.append(os.path.join(r'D:\seti_data', t, 'fine'))
    dirs += ['data/PROXCEN', r'D:\seti_data\fine']
    return dirs


def resolve_target_coords(target_name, ra=None, dec=None):
    """
    Resolve target coordinates from name if RA/Dec not provided.
    
    Returns (ra_hours, dec_deg, source) where source is 'manual' or
    'registry' (SQLite targets table), or (None, None, None) if the
    target is unknown.
    """
    if ra is not None and dec is not None:
        return float(ra), float(dec), 'manual'
    
    # Phase 3A: target registry (SQLite) first; registry wins over dict
    try:
        from target_registry import get_target
        t = get_target(target_name)
        if t and t.get('ra_hours') is not None:
            return t['ra_hours'], t['dec_deg'], 'registry'
    except Exception:
        pass
    
    # Legacy static dict fallback removed 2026-08-15: registry is the
    # single source of truth; unknown targets resolve to None.
    return None, None, None


# ─── File-Level Correction ────────────────────────────────────────────

def correct_hits_file(hits_path, ra_hours=None, dec_deg=None, telescope='parkes',
                      target_name=None, mjd=None, h5_dir=None):
    """
    Process a hits.json file and add barycentric_freq to each hit.

    Parameters
    ----------
    hits_path : str
        Path to the *_hits.json file.
    ra_hours : float, optional
        RA in hours. If None, tries target_name lookup or HDF5 header.
    dec_deg : float, optional
        Dec in degrees. If None, tries target_name lookup or HDF5 header.
    telescope : str or int
        Telescope identifier.
    target_name : str, optional
        Target name for coordinate database lookup.
    mjd : float, optional
        MJD of observation. If None, extracted from filename or HDF5.
    h5_dir : str, optional
        Directory containing HDF5 files (for header lookup fallback).

    Returns
    -------
    dict
        The corrected hits data dict with barycentric_freq added to each hit.
        Also adds top-level keys: barycentric_velocity_mps, mjd, ra_hours, dec_deg.
    """
    with open(hits_path) as f:
        data = json.load(f)

    filename = data.get('file', os.path.basename(hits_path))

    # Resolve MJD
    if mjd is None:
        mjd = extract_mjd_from_filename(filename)
        if mjd is None and h5_dir:
            h5_path = os.path.join(h5_dir, filename)
            if os.path.isfile(h5_path):
                mjd = get_mjd_from_h5(h5_path)
    if mjd is None:
        raise ValueError(f"Could not determine MJD for {filename}. Provide mjd parameter.")

    # Resolve coordinates
    if ra_hours is None or dec_deg is None:
        # Try HDF5 header
        h5_path = None
        if h5_dir:
            h5_path = os.path.join(h5_dir, filename)
        elif hasattr(h5_path, '__fspath__'):
            pass
        
        # Try to find the HDF5 file
        if h5_path is None or not os.path.isfile(h5_path):
            # Search common data directories
            seti_root = Path(hits_path).parents[3]  # results/scan/file_stem/hits.json -> seti root
            for d in _h5_search_dirs(target_name):
                if os.path.isabs(d):
                    candidate = os.path.join(d, filename)
                else:
                    candidate = os.path.join(seti_root, d, filename)
                if os.path.isfile(candidate):
                    h5_path = candidate
                    break

        if h5_path and os.path.isfile(h5_path):
            h_ra, h_dec, h_tel = get_coords_from_h5(h5_path)
            if ra_hours is None and h_ra is not None:
                ra_hours = h_ra
            if dec_deg is None and h_dec is not None:
                dec_deg = h_dec
            if h_tel is not None:
                telescope = h_tel

        # Try target name lookup
        if (ra_hours is None or dec_deg is None) and target_name:
            db_ra, db_dec, src = resolve_target_coords(target_name, ra_hours, dec_deg)
            if db_ra is not None:
                ra_hours, dec_deg = db_ra, db_dec

    if ra_hours is None or dec_deg is None:
        raise ValueError(f"Could not determine target coordinates for {filename}. "
                         f"Provide ra_hours and dec_deg parameters.")

    # Compute barycentric velocity
    v_bary = compute_barycentric_velocity(mjd, ra_hours, dec_deg, telescope)

    # Apply correction to all hits
    c = const.c.to(u.m / u.s).value
    correction_factor = 1.0 - v_bary / c

    for hit in data.get('hits', []):
        hit['barycentric_freq'] = hit['freq'] * correction_factor
        hit['barycentric_velocity_mps'] = round(v_bary, 2)

    # Add metadata
    data['barycentric_velocity_mps'] = round(v_bary, 2)
    data['barycentric_correction_factor'] = correction_factor
    data['barycentric_mjd'] = mjd
    data['barycentric_ra_hours'] = ra_hours
    data['barycentric_dec_deg'] = dec_deg
    data['barycentric_telescope'] = telescope if isinstance(telescope, str) else TELESCOPE_ID_MAP.get(telescope, str(telescope))

    return data


def save_corrected_hits(hits_path, output_path=None):
    """
    Load, correct, and save a hits.json file with barycentric frequencies.
    
    Parameters
    ----------
    hits_path : str
        Path to the original hits.json.
    output_path : str, optional
        Output path. Default: same as input but with _bary.json suffix.
    
    Returns
    -------
    str
        Path to the output file.
    """
    data = correct_hits_file(hits_path)
    if output_path is None:
        base = hits_path.replace('_hits.json', '_bary_hits.json')
        output_path = base
    with open(output_path, 'w') as f:
        json.dump(data, f, indent=2)
    return output_path


# ─── Scan-Level Correction ────────────────────────────────────────────

def correct_scan(scan_dir, ra_hours=None, dec_deg=None, telescope='parkes',
                 target_name=None):
    """
    Process all hits files in a scan directory.

    Walks the scan directory, finds all *_hits.json files, applies barycentric
    correction to each, and writes a combined corrected dataset.

    Parameters
    ----------
    scan_dir : str
        Path to the scan results directory (e.g. results/PROXCEN_2026-08-07_1911).
    ra_hours : float, optional
        RA in hours. Auto-detected if not provided.
    dec_deg : float, optional
        Dec in degrees. Auto-detected if not provided.
    telescope : str or int
        Telescope identifier.
    target_name : str, optional
        Target name for coordinate lookup.

    Returns
    -------
    dict
        Summary with keys:
        - scan_dir: path
        - files_corrected: list of corrected file paths
        - total_hits: total hit count
        - barycentric_dir: output directory path
        - corrections: per-file velocity corrections
    """
    scan_dir = os.path.abspath(scan_dir)
    
    # Auto-detect target from scan_meta.json
    if target_name is None:
        meta_path = os.path.join(scan_dir, 'scan_meta.json')
        if os.path.isfile(meta_path):
            with open(meta_path) as f:
                meta = json.load(f)
            target_name = meta.get('target', '')
    
    # Try to auto-detect coordinates from first HDF5 header
    if ra_hours is None or dec_deg is None:
        for hits_path in glob_module.glob(
            os.path.join(scan_dir, '**/*_hits.json'), recursive=True):
            with open(hits_path) as f:
                data = json.load(f)
            filename = data.get('file', '')
            
            # Try HDF5 header
            seti_root = Path(scan_dir).parent.parent
            for d in _h5_search_dirs(target_name):
                if os.path.isabs(d):
                    h5_candidate = os.path.join(d, filename)
                else:
                    h5_candidate = os.path.join(seti_root, d, filename)
                if os.path.isfile(h5_candidate):
                    h_ra, h_dec, h_tel = get_coords_from_h5(h5_candidate)
                    if ra_hours is None and h_ra is not None:
                        ra_hours = h_ra
                    if dec_deg is None and h_dec is not None:
                        dec_deg = h_dec
                    break
            if ra_hours is not None and dec_deg is not None:
                break
        
        # Try target name lookup
        if (ra_hours is None or dec_deg is None) and target_name:
            db_ra, db_dec, src = resolve_target_coords(target_name, ra_hours, dec_deg)
            if db_ra is not None:
                ra_hours, dec_deg = db_ra, db_dec
    
    if ra_hours is None or dec_deg is None:
        raise ValueError("Could not auto-detect target coordinates. "
                         "Please provide ra_hours and dec_deg.")

    # Create output directory
    bary_dir = os.path.join(scan_dir, 'barycentric')
    os.makedirs(bary_dir, exist_ok=True)

    files_corrected = []
    total_hits = 0
    corrections = {}
    all_corrected_hits = []

    for hits_path in sorted(glob_module.glob(
        os.path.join(scan_dir, '**/*_hits.json'), recursive=True)):
        
        # Skip already-corrected files
        if '_bary' in hits_path:
            continue

        data = correct_hits_file(
            hits_path, ra_hours=ra_hours, dec_deg=dec_deg,
            telescope=telescope, target_name=target_name,
        )

        # Save individual corrected file
        fname = os.path.basename(hits_path).replace('_hits.json', '_bary_hits.json')
        out_path = os.path.join(bary_dir, fname)
        with open(out_path, 'w') as f:
            json.dump(data, f, indent=2)
        
        files_corrected.append(out_path)
        total_hits += len(data.get('hits', []))
        
        filename = data.get('file', '')
        v = data.get('barycentric_velocity_mps', 0)
        corrections[filename] = v

        # Collect hits for combined output
        is_on = '_S_' in filename
        for hit in data.get('hits', []):
            hit['on_off'] = 'ON' if is_on else 'OFF'
            hit['source_file'] = filename
            hit['mjd'] = data.get('barycentric_mjd', 0)
            all_corrected_hits.append(hit)

    # Save combined corrected dataset
    combined_path = os.path.join(bary_dir, 'combined_corrected.json')
    combined = {
        'scan_dir': scan_dir,
        'target': target_name or 'unknown',
        'ra_hours': ra_hours,
        'dec_deg': dec_deg,
        'telescope': telescope if isinstance(telescope, str) else TELESCOPE_ID_MAP.get(telescope, str(telescope)),
        'total_hits': total_hits,
        'files_corrected': len(files_corrected),
        'corrections': corrections,
        'hits': all_corrected_hits,
    }
    with open(combined_path, 'w') as f:
        json.dump(combined, f, indent=2)

    return {
        'scan_dir': scan_dir,
        'barycentric_dir': bary_dir,
        'files_corrected': files_corrected,
        'total_hits': total_hits,
        'corrections': corrections,
        'combined_path': combined_path,
    }


# ─── Cross-Epoch Comparison ───────────────────────────────────────────

def cross_epoch_match(scan_dirs, freq_tolerance_hz=10, min_epochs=2, min_snr=0):
    """
    Find barycentric frequencies present in ON frames across multiple epochs
    but absent from OFF frames.

    This is the key SETI analysis: a real transmitter at a distant star would
    appear at the same barycentric-corrected frequency in every ON observation
    of that target, but would NOT appear in OFF (reference sky) observations.
    RFI, being terrestrial, would appear at different barycentric frequencies
    across epochs and would likely show up in OFF frames too.

    Algorithm:
    1. Load corrected hits from each scan (run correct_scan first).
    2. Bucket all hits by barycentric frequency (to freq_tolerance_hz precision).
    3. For each unique frequency bucket, count how many ON epochs and OFF frames
       contain a hit at that frequency.
    4. Candidates: frequency buckets with >= min_epochs ON detections and 0 OFF.

    Uses frequency bucketing for O(N) complexity, not O(N*M).

    Parameters
    ----------
    scan_dirs : list of str
        List of scan directory paths. Each must have a barycentric/ subdirectory
        (run correct_scan() first) OR raw hits that can be corrected on-the-fly.
    freq_tolerance_hz : float
        Frequency tolerance for matching across epochs in Hz. Default 10 Hz.
        At Parkes fine-res (2.79 Hz/channel), 10 Hz is ~3.6 channels.
    min_epochs : int
        Minimum number of ON epochs required for a candidate. Default 2.
    min_snr : float
        Post-filter threshold. Only hits with SNR >= min_snr are considered.
        Default 0 (no filter, use all hits from the original search).
        Since turboSETI's SNR threshold is a simple cutoff on the detection
        statistic, post-filtering existing results is equivalent to re-running
        turboSETI at the higher threshold. No re-scan needed.

    Returns
    -------
    dict
        {
        'candidates': list of candidate dicts sorted by epoch count desc, then SNR desc,
        'summary': {total_frequencies_checked, total_candidates, ...}
        }
    """
    freq_tolerance_mhz = freq_tolerance_hz * 1e-6  # Convert Hz to MHz
    # Grid resolution: buckets per MHz
    grid_resolution = int(1.0 / freq_tolerance_mhz)

    # Collect hits from all scans
    # bucket_key -> list of hit records
    all_on_hits = {}   # key: (freq_bucket, epoch_id)
    all_off_hits = {}  # key: (freq_bucket, epoch_id)
    epoch_info = {}    # epoch_id -> {mjd, target, scan_dir}

    for epoch_id, scan_dir in enumerate(scan_dirs):
        scan_dir = os.path.abspath(scan_dir)
        bary_dir = os.path.join(scan_dir, 'barycentric')
        
        # Prefer pre-computed combined file
        combined_path = os.path.join(bary_dir, 'combined_corrected.json')
        if os.path.isfile(combined_path):
            with open(combined_path) as f:
                combined = json.load(f)
            hits = combined.get('hits', [])
            epoch_info[epoch_id] = {
                'mjd': combined.get('mjd', 0),
                'target': combined.get('target', ''),
                'scan_dir': scan_dir,
            }
        else:
            # Try to correct on-the-fly
            try:
                result = correct_scan(scan_dir)
                with open(result['combined_path']) as f:
                    combined = json.load(f)
                hits = combined.get('hits', [])
                epoch_info[epoch_id] = {
                    'mjd': combined.get('mjd', 0),
                    'target': combined.get('target', ''),
                    'scan_dir': scan_dir,
                }
            except Exception as e:
                # Last resort: load raw hits files
                hits = []
                for hits_path in glob_module.glob(
                    os.path.join(scan_dir, '**/*_hits.json'), recursive=True):
                    if '_bary' in hits_path:
                        continue
                    with open(hits_path) as f:
                        data = json.load(f)
                    filename = data.get('file', '')
                    is_on = '_S_' in filename
                    for h in data.get('hits', []):
                        h['on_off'] = 'ON' if is_on else 'OFF'
                        h['source_file'] = filename
                        hits.append(h)
                epoch_info[epoch_id] = {
                    'mjd': 0,
                    'target': '',
                    'scan_dir': scan_dir,
                    'warning': f'Used uncorrected hits: {e}',
                }

        # Bucket hits (apply min_snr post-filter)
        for hit in hits:
            if hit.get('snr', 0) < min_snr:
                continue
            freq = hit.get('barycentric_freq', hit.get('freq', 0))
            if freq == 0:
                continue
            bucket = int(round(freq * grid_resolution))
            is_on = hit.get('on_off', 'ON' if '_S_' in hit.get('source_file', '') else 'OFF') == 'ON'

            if is_on:
                key = bucket
                if key not in all_on_hits:
                    all_on_hits[key] = []
                all_on_hits[key].append({
                    'epoch_id': epoch_id,
                    'freq_bary': freq,
                    'freq_obs': hit.get('freq', 0),
                    'drift_rate': hit.get('drift_rate', 0),
                    'snr': hit.get('snr', 0),
                    'source_file': hit.get('source_file', ''),
                    'mjd': hit.get('mjd', epoch_info[epoch_id].get('mjd', 0)),
                })
            else:
                key = bucket
                if key not in all_off_hits:
                    all_off_hits[key] = []
                all_off_hits[key].append({
                    'epoch_id': epoch_id,
                    'freq_bary': freq,
                    'drift_rate': hit.get('drift_rate', 0),
                    'snr': hit.get('snr', 0),
                    'source_file': hit.get('source_file', ''),
                })

    # Find candidate frequencies: present in ON across >= min_epochs, absent in OFF
    candidates = []
    total_freqs_checked = 0
    freqs_ge2 = 0            # unique ON freqs seen in 2+ epochs
    freqs_ge_min = 0         # unique ON freqs meeting the min_epochs filter

    for bucket, on_hits_list in all_on_hits.items():
        # Count distinct epochs with an ON hit at this frequency
        epoch_ids = set(h['epoch_id'] for h in on_hits_list)
        n_epochs = len(epoch_ids)
        total_freqs_checked += 1
        if n_epochs >= 2:
            freqs_ge2 += 1

        if n_epochs < min_epochs:
            continue
        freqs_ge_min += 1

        # Check OFF frames at this frequency (and adjacent buckets for safety)
        off_count = 0
        for check_bucket in range(bucket - 1, bucket + 2):
            if check_bucket in all_off_hits:
                off_count += len(all_off_hits[check_bucket])

        if off_count > 0:
            continue  # Present in OFF frame -> likely RFI

        # This is a candidate!
        # Aggregate stats across epochs
        drift_rates = [h['drift_rate'] for h in on_hits_list]
        snrs = [h['snr'] for h in on_hits_list]
        freqs_bary = [h['freq_bary'] for h in on_hits_list]
        freqs_obs = [h['freq_obs'] for h in on_hits_list]

        candidates.append({
            'barycentric_freq_mhz': round(np.mean(freqs_bary), 8),
            'barycentric_freq_std_mhz': round(np.std(freqs_bary), 8),
            'observed_freqs_mhz': [round(f, 6) for f in freqs_obs],
            'mean_drift_rate': round(np.mean(drift_rates), 6),
            'drift_rates': [round(d, 6) for d in drift_rates],
            'max_snr': max(snrs),
            'snrs': [round(s, 2) for s in snrs],
            'on_count': len(on_hits_list),
            'epoch_count': n_epochs,
            'off_count': 0,
            'epochs': sorted(epoch_ids),
            'epoch_details': on_hits_list,
        })

    # Sort by epoch count (desc), then SNR (desc)
    candidates.sort(key=lambda c: (c['epoch_count'], c['max_snr']), reverse=True)

    # Compute false alarm probability for each candidate
    # P(coincidence) = (N_hits_epoch1 / N_bins) * (N_hits_epoch2 / N_bins) * ...
    # For a simple estimate: P = product of (n_hits_i / n_bins) for each epoch
    # where n_bins = observation bandwidth / frequency_resolution
    # We use a simpler Bayesian estimate
    n_bins_per_epoch = {}
    for eid in epoch_info:
        n_bins_per_epoch[eid] = len([1 for b, hits in all_on_hits.items()
                                      if any(h['epoch_id'] == eid for h in hits)])
    
    for cand in candidates:
        # Simple false alarm: probability of k-epoch coincidence by chance
        # P = product of (hit_rate_i) for involved epochs
        # hit_rate_i = n_hits_in_epoch_i / total_frequency_buckets
        total_buckets = len(all_on_hits) if all_on_hits else 1
        log_p = 0
        for eid in cand['epochs']:
            n = n_bins_per_epoch.get(eid, 0)
            if total_buckets > 0 and n > 0:
                log_p += np.log(n / total_buckets)
        cand['log_false_alarm_prob'] = round(log_p, 4)

    return {
        'candidates': candidates,
        'summary': {
            'total_scans': len(scan_dirs),
            'total_epochs': len(epoch_info),
            'epoch_info': {str(k): v for k, v in epoch_info.items()},
            'total_on_frequencies': total_freqs_checked,
            'freqs_ge2_epochs': freqs_ge2,
            'freqs_meeting_min_epochs': freqs_ge_min,
            'total_candidates': len(candidates),
            'freq_tolerance_hz': freq_tolerance_hz,
            'min_epochs': min_epochs,
            'min_snr': min_snr,
            'scan_ids': [os.path.basename(os.path.abspath(sd)) for sd in scan_dirs],
        },
    }


# ─── CLI ──────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(
        description='Barycentric correction for SETI hit analysis')
    sub = parser.add_subparsers(dest='command')

    # Compute velocity
    p_vel = sub.add_parser('velocity', help='Compute barycentric velocity for a given MJD and target')
    p_vel.add_argument('mjd', type=float, help='Modified Julian Date')
    p_vel.add_argument('--ra', type=float, help='RA in hours')
    p_vel.add_argument('--dec', type=float, help='Dec in degrees')
    p_vel.add_argument('--target', type=str, default='PROXCEN', help='Target name')
    p_vel.add_argument('--telescope', type=str, default='parkes', help='Telescope name')

    # Correct a single file
    p_file = sub.add_parser('file', help='Correct a single hits.json file')
    p_file.add_argument('hits_path', help='Path to hits.json')
    p_file.add_argument('--ra', type=float, help='RA in hours')
    p_file.add_argument('--dec', type=float, help='Dec in degrees')
    p_file.add_argument('--target', type=str, help='Target name')
    p_file.add_argument('--telescope', type=str, default='parkes')

    # Correct a full scan
    p_scan = sub.add_parser('scan', help='Correct all hits in a scan directory')
    p_scan.add_argument('scan_dir', help='Path to scan results directory')
    p_scan.add_argument('--ra', type=float, help='RA in hours')
    p_scan.add_argument('--dec', type=float, help='Dec in degrees')
    p_scan.add_argument('--target', type=str, help='Target name')
    p_scan.add_argument('--telescope', type=str, default='parkes')

    # Cross-epoch comparison
    p_cross = sub.add_parser('cross-epoch', help='Cross-epoch candidate search')
    p_cross.add_argument('scan_dirs', nargs='+', help='Scan directory paths')
    p_cross.add_argument('--tolerance-hz', type=float, default=10, help='Frequency tolerance in Hz')
    p_cross.add_argument('--min-epochs', type=int, default=2, help='Minimum ON epochs for candidate')
    p_cross.add_argument('--min-snr', type=float, default=0,
                        help='Post-filter: only use hits with SNR >= this value (no re-scan needed)')

    args = parser.parse_args()

    if args.command == 'velocity':
        ra, dec = args.ra, args.dec
        if ra is None or dec is None:
            ra, dec, _ = resolve_target_coords(args.target, ra, dec)
        v = compute_barycentric_velocity(args.mjd, ra, dec, args.telescope)
        c = const.c.to(u.m / u.s).value
        correction_ppm = v / c * 1e6
        print(f"Target: {args.target}")
        print(f"MJD: {args.mjd}")
        print(f"RA: {ra} hours, Dec: {dec} deg")
        print(f"Telescope: {args.telescope}")
        print(f"Barycentric velocity: {v:.1f} m/s ({v/1000:.3f} km/s)")
        print(f"Frequency correction: {correction_ppm:+.3f} ppm")
        print(f"  (positive = observed freq redshifted, divide out)")

    elif args.command == 'file':
        data = correct_hits_file(
            args.hits_path, ra_hours=args.ra, dec_deg=args.dec,
            target_name=args.target, telescope=args.telescope)
        v = data['barycentric_velocity_mps']
        print(f"File: {data.get('file', '?')}")
        print(f"Barycentric velocity: {v:.1f} m/s")
        print(f"Hits corrected: {len(data.get('hits', []))}")
        print(f"MJD: {data.get('barycentric_mjd', '?')}")

    elif args.command == 'scan':
        result = correct_scan(
            args.scan_dir, ra_hours=args.ra, dec_deg=args.dec,
            target_name=args.target, telescope=args.telescope)
        print(f"Scan: {result['scan_dir']}")
        print(f"Output: {result['barycentric_dir']}")
        print(f"Files corrected: {len(result['files_corrected'])}")
        print(f"Total hits: {result['total_hits']}")
        print("Per-file corrections:")
        for fname, v in result['corrections'].items():
            print(f"  {fname}: {v:.1f} m/s")

    elif args.command == 'cross-epoch':
        result = cross_epoch_match(
            args.scan_dirs,
            freq_tolerance_hz=args.tolerance_hz,
            min_epochs=args.min_epochs)
        print(f"Scans analyzed: {result['summary']['total_scans']}")
        print(f"ON frequencies checked: {result['summary']['total_on_frequencies']}")
        print(f"Candidates: {result['summary']['total_candidates']}")
        for i, cand in enumerate(result['candidates'][:20]):
            print(f"  #{i+1}: {cand['barycentric_freq_mhz']:.6f} MHz "
                  f"({cand['epoch_count']} epochs, SNR max={cand['max_snr']:.1f}, "
                  f"log P={cand['log_false_alarm_prob']:.2f})")

    else:
        parser.print_help()


if __name__ == '__main__':
    main()
