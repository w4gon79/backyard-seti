"""Two-layer SETI filter pipeline: cross-epoch barycentric filter + targeted stack.

Layer 1: Cross-Epoch Barycentric Filter
    Find hits that appear at the same barycentric-corrected frequency
    across multiple epochs (months apart). RFI cannot maintain barycentric
    consistency across epochs because it isn't from the target star.
    Only ON-only hits (absent in OFF) that match across >= min_epochs survive.

Layer 2: Targeted Incoherent Stack
    For each surviving candidate frequency, extract a narrow spectrum window
    centered on that frequency from each epoch's ON/OFF pair data.
    Stack only those windows. A real signal gets sqrt(N) SNR boost.
    RFI that happened to slip through layer 1 won't coherently stack.

Usage:
    python ml/two_layer_pipeline.py --target PROXCEN --tolerance-hz 10 --min-epochs 3 --min-snr 8
    python ml/two_layer_pipeline.py --target PROXCEN --tolerance-hz 10 --min-epochs 2 --min-snr 8 --stack-width 0.1

Output:
    - Prints summary to console
    - Saves results JSON to G:\seti\results\two_layer\
    - If candidates found, saves stacked spectra for each
"""

import os
import sys
import json
import time
import argparse
import numpy as np
import sqlite3

SETI_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SETI_ROOT)
sys.path.insert(0, os.path.join(SETI_ROOT, 'src'))

from barycentric_correct import cross_epoch_match, TARGET_COORDS
from incoherent_stack import (
    EPOCHS, find_h5, load_spectrum_window, build_common_grid, find_peaks,
    process_epoch, compute_barycentric_velocity, extract_mjd_from_filename,
)
from ml.layer25_analysis import analyze_all_candidates


def get_scan_dirs(target='PROXCEN'):
    """Find scan result directories for a target.
    
    Only returns scans that have COMPLETE barycentric corrections
    (barycentric/combined_corrected.json exists). Scans with only raw
    hits are excluded to prevent cross_epoch_match from auto-correcting
    partial/incomplete scans on the fly.
    """
    results_dir = os.path.join(SETI_ROOT, 'results')
    scan_dirs = []
    if os.path.isdir(results_dir):
        for d in sorted(os.listdir(results_dir)):
            full = os.path.join(results_dir, d)
            if os.path.isdir(full) and target in d:
                # ONLY include scans with pre-computed barycentric data
                combined = os.path.join(full, 'barycentric', 'combined_corrected.json')
                if os.path.isfile(combined):
                    scan_dirs.append(full)
    return scan_dirs


def run_cross_epoch_filter(scan_dirs, tolerance_hz=10, min_epochs=3, min_snr=8):
    """Layer 1: Run cross-epoch barycentric filter.
    
    Returns list of candidate frequencies with cross-epoch metadata.
    """
    print(f"\n{'='*60}")
    print("LAYER 1: CROSS-EPOCH BARYCENTRIC FILTER")
    print(f"{'='*60}")
    print(f"  Scans: {len(scan_dirs)}")
    print(f"  Tolerance: {tolerance_hz} Hz")
    print(f"  Min epochs: {min_epochs}")
    print(f"  Min SNR: {min_snr}")
    
    t0 = time.time()
    result = cross_epoch_match(
        scan_dirs,
        freq_tolerance_hz=tolerance_hz,
        min_epochs=min_epochs,
        min_snr=min_snr,
    )
    elapsed = time.time() - t0
    
    candidates = result.get('candidates', [])
    summary = result.get('summary', {})
    
    print(f"\n  Completed in {elapsed:.1f}s")
    print(f"  Total ON frequencies checked: {summary.get('total_on_frequencies', '?')}")
    print(f"  Candidates found: {len(candidates)}")
    
    if candidates:
        print(f"\n  === Surviving Candidates ===")
        for i, c in enumerate(candidates):
            print(f"  [{i+1}] {c['barycentric_freq_mhz']:.8f} MHz")
            print(f"      Epochs: {c['epoch_count']}/{summary.get('total_epochs', '?')}")
            print(f"      Max SNR: {c['max_snr']:.1f}")
            print(f"      Mean drift: {c['mean_drift_rate']:.6f} Hz/s")
            print(f"      Bary freq std: {c['barycentric_freq_std_mhz']:.2e} MHz")
            print(f"      Log false alarm prob: {c.get('log_false_alarm_prob', '?')}")
    
    return result


def _compute_pulse_periodicity(time_series_2d, common_grid, freq_center, stack_width_mhz):
    """Extract power curve at peak frequency and compute autocorrelation.
    
    Returns dict: {period_s, confidence, duty_cycle, has_periodicity}
    """
    if time_series_2d is None or time_series_2d.ndim != 2 or time_series_2d.shape[0] < 4:
        return {'period_s': None, 'confidence': 0.0, 'duty_cycle': 0.0,
                'has_periodicity': False}
    
    n_times, n_chans = time_series_2d.shape
    
    # Find the peak channel in the mean spectrum
    mean_spec = np.mean(time_series_2d, axis=0)
    peak_idx = np.argmax(mean_spec)
    
    # Extract 1D power curve at the peak channel
    power_curve = time_series_2d[:, peak_idx]
    
    # Mean-subtract and normalize
    pc_mean = np.mean(power_curve)
    pc_centered = power_curve - pc_mean
    pc_std = np.std(pc_centered)
    if pc_std == 0:
        return {'period_s': None, 'confidence': 0.0, 'duty_cycle': 0.0,
                'has_periodicity': False}
    pc_normalized = pc_centered / pc_std
    
    # Compute autocorrelation (positive lags only)
    autocorr = np.correlate(pc_normalized, pc_normalized, mode='full')
    # Keep only positive lags (second half), exclude lag=0
    mid = len(autocorr) // 2
    pos_lags = autocorr[mid + 1:]
    lags = np.arange(1, len(pos_lags) + 1)
    
    if len(pos_lags) < 2:
        return {'period_s': None, 'confidence': 0.0, 'duty_cycle': 0.0,
                'has_periodicity': False}
    
    # Find the highest peak in the autocorrelation
    peak_lag_idx = np.argmax(pos_lags)
    peak_height = pos_lags[peak_lag_idx]
    peak_lag = lags[peak_lag_idx]
    
    # Noise floor: median absolute deviation of autocorrelation (excluding the peak region)
    mask = np.ones(len(pos_lags), dtype=bool)
    # Exclude a window around the peak (+/- 3 lags)
    win = 3
    mask[max(0, peak_lag_idx - win):min(len(mask), peak_lag_idx + win + 1)] = False
    if mask.sum() > 0:
        noise_floor = np.median(np.abs(pos_lags[mask]))
    else:
        noise_floor = np.median(np.abs(pos_lags))
    
    if noise_floor > 0:
        confidence = float(min(peak_height / (noise_floor * 3), 1.0))  # 3x MAD = strong
    else:
        confidence = 0.0
    
    # Estimate duty cycle: fraction of time the signal is above its mean
    above = power_curve > pc_mean
    duty_cycle = float(np.mean(above))
    
    # Estimate period in seconds (Parkes typical integration time ~1.07s per record)
    # The HDF5 time resolution is in the header; we approximate with common Parkes value
    dt_seconds = 1.07  # seconds per time integration (Parkes fine channel)
    period_s = float(peak_lag * dt_seconds)
    
    has_periodicity = confidence > 0.5 and peak_height > 0.3
    
    return {
        'period_s': period_s if has_periodicity else None,
        'confidence': round(confidence, 3),
        'duty_cycle': round(duty_cycle, 3),
        'has_periodicity': has_periodicity,
    }


def targeted_stack(candidate_freq, stack_width_mhz, epoch_labels, target='PROXCEN',
                   telescope='parkes', n_sigma=5.0):
    """Layer 2: Stack a narrow window around a single candidate frequency.
    
    Extracts ON-OFF residual spectra from each epoch at this frequency,
    applies barycentric correction, and stacks.
    
    Returns dict with stack result or None on failure.
    """
    target_info = TARGET_COORDS.get(target, TARGET_COORDS.get('PROXCEN'))
    # TARGET_COORDS values are (ra_hours, dec_degrees) tuples
    target_ra, target_dec = target_info
    
    freq_center = candidate_freq
    
    # Process each epoch
    padding_mhz = 0.1
    f_start_obs = freq_center - stack_width_mhz / 2 - padding_mhz
    f_stop_obs = freq_center + stack_width_mhz / 2 + padding_mhz
    
    common_grid = build_common_grid(freq_center, stack_width_mhz)
    
    epoch_spectra = []
    used_epochs = []
    epoch_info = []
    time_series_2d = None  # from first epoch for pulse analysis
    
    for label in epoch_labels:
        if label not in EPOCHS:
            continue
        
        spec, ts = process_epoch(
            label, EPOCHS[label], target_ra, target_dec,
            f_start_obs, f_stop_obs, common_grid, telescope,
            return_time_series=True,
        )
        
        if spec is not None:
            if not np.isfinite(spec).any():
                print(f"  Epoch {label}: fully masked by RFI zones, excluded")
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
            # Keep the first available time-series for pulse analysis
            if time_series_2d is None and ts is not None:
                time_series_2d = ts
    
    if len(epoch_spectra) < 2:
        return None
    
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', RuntimeWarning)
        stack = np.nanmean(epoch_spectra, axis=0)
    n = len(epoch_spectra)
    
    _sfinite = stack[np.isfinite(stack)]
    if _sfinite.size == 0:
        return None
    median = float(np.median(_sfinite))
    mad = float(np.median(np.abs(_sfinite - median)))
    sigma = float(1.4826 * mad)
    
    peaks = find_peaks(stack, common_grid, n_sigma=n_sigma)
    
    # Compute power concentration scalar
    if sigma > 0:
        threshold = median + n_sigma * sigma
        above = stack[stack > threshold]
        total_power = np.nansum(np.abs(stack - median))
        peak_power = np.sum(np.abs(above - median))
        power_concentration = float(peak_power / total_power) if total_power > 0 else 0.0
    else:
        power_concentration = 0.0
    
    # Compute pulse periodicity from time-series data
    pulse_periodicity = _compute_pulse_periodicity(
        time_series_2d, common_grid, freq_center, stack_width_mhz)
    
    return {
        'freq_center': freq_center,
        'stack_width_mhz': stack_width_mhz,
        'stack': [None if v != v else v for v in stack.tolist()],
        'grid_freqs': common_grid.tolist(),
        'median': median,
        'sigma': sigma,
        'peaks': peaks,
        'used_epochs': used_epochs,
        'n_epochs': n,
        'epoch_info': epoch_info,
        'snr_improvement': float(np.sqrt(n)),
        'power_concentration': power_concentration,
        'pulse_periodicity': pulse_periodicity,
    }


def run_targeted_stacks(candidates, stack_width_mhz, epoch_labels, target='PROXCEN'):
    """Layer 2: Run targeted stack on each surviving candidate frequency."""
    print(f"\n{'='*60}")
    print("LAYER 2: TARGETED INCOHERENT STACK")
    print(f"{'='*60}")
    print(f"  Candidates to stack: {len(candidates)}")
    print(f"  Stack width: {stack_width_mhz} MHz")
    print(f"  Epochs: {epoch_labels}")
    
    results = []
    
    for i, cand in enumerate(candidates):
        freq = cand['barycentric_freq_mhz']
        print(f"\n  [{i+1}/{len(candidates)}] Stacking {freq:.8f} MHz...")
        t0 = time.time()
        
        stack_result = targeted_stack(
            freq, stack_width_mhz, epoch_labels, target=target,
        )
        
        if stack_result is None:
            print(f"    FAILED: Need >= 2 epochs with data")
            results.append({
                'candidate': cand,
                'stack_success': False,
                'error': 'Insufficient epoch data',
            })
            continue
        
        elapsed = time.time() - t0
        n_peaks = len(stack_result['peaks'])
        
        print(f"    Stacked {stack_result['n_epochs']} epochs ({elapsed:.1f}s)")
        print(f"    Stack sigma: {stack_result['sigma']:.4e}")
        print(f"    Peaks above {5}sigma: {n_peaks}")
        
        if n_peaks > 0:
            for p in stack_result['peaks'][:5]:
                print(f"      {p['freq_mhz']:.6f} MHz  SNR={p['snr']:.2f}  width={p['width_chans']} chans")
        
        results.append({
            'candidate': cand,
            'stack_success': True,
            'stack_result': stack_result,
        })
    
    return results


def main():
    parser = argparse.ArgumentParser(description='Two-layer SETI filter pipeline')
    parser.add_argument('--target', default='PROXCEN')
    parser.add_argument('--tolerance-hz', type=float, default=10,
                        help='Barycentric freq match tolerance in Hz (default: 10)')
    parser.add_argument('--min-epochs', type=int, default=3,
                        help='Min epochs for cross-epoch match (default: 3)')
    parser.add_argument('--min-snr', type=float, default=8,
                        help='Min SNR for cross-epoch filter (default: 8)')
    parser.add_argument('--stack-width', type=float, default=0.05,
                        help='Stack window width in MHz per candidate (default: 0.05 = 50 kHz)')
    parser.add_argument('--n-sigma', type=float, default=5.0,
                        help='Peak detection threshold in sigma (default: 5)')
    parser.add_argument('--epochs', type=str, default=None,
                        help='Comma-separated epoch labels (default: all available)')
    args = parser.parse_args()
    
    target = args.target
    
    # Determine epochs
    if args.epochs:
        epoch_labels = [e.strip() for e in args.epochs.split(',')]
    else:
        epoch_labels = list(EPOCHS.keys())
    
    print(f"\n{'='*60}")
    print(f"TWO-LAYER SETI FILTER PIPELINE")
    print(f"Target: {target}")
    print(f"Epochs: {epoch_labels}")
    print(f"{'='*60}")
    
    # Find scan directories
    scan_dirs = get_scan_dirs(target)
    if len(scan_dirs) < 2:
        print(f"ERROR: Need at least 2 scan directories, found {len(scan_dirs)}")
        print(f"  Scans: {scan_dirs}")
        sys.exit(1)
    
    print(f"Scan directories: {len(scan_dirs)}")
    for sd in scan_dirs:
        print(f"  {os.path.basename(sd)}")
    
    total_start = time.time()
    
    # ─── Layer 1: Cross-Epoch Filter ───────────────────────────────
    xepoch_result = run_cross_epoch_filter(
        scan_dirs,
        tolerance_hz=args.tolerance_hz,
        min_epochs=args.min_epochs,
        min_snr=args.min_snr,
    )
    
    candidates = xepoch_result.get('candidates', [])
    
    # ─── Layer 2: Targeted Stack (only if candidates exist) ────────
    if len(candidates) == 0:
        print(f"\n{'='*60}")
        print("NO CANDIDATES SURVIVED CROSS-EPOCH FILTER")
        print(f"{'='*60}")
        print(f"\nAll {xepoch_result['summary'].get('total_on_frequencies', '?')} ON frequencies")
        print(f"were eliminated as single-epoch RFI or OFF-present RFI.")
        print(f"\nThis is the expected result. The filter is working correctly.")
        print(f"When a real signal is present, it will survive both layers.")
        
        # Save summary even with zero candidates
        output_dir = os.path.join(SETI_ROOT, 'results', 'two_layer')
        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir,
            f'{target}_tol{args.tolerance_hz}_ep{args.min_epochs}_snr{args.min_snr}.json')
        
        with open(output_path, 'w') as f:
            json.dump({
                'target': target,
                'timestamp': time.strftime('%Y-%m-%dT%H:%M:%S'),
                'layer1': xepoch_result,
                'layer2': {'results': [], 'n_candidates_stacked': 0},
                'summary': {
                    'total_on_freqs': xepoch_result['summary'].get('total_on_frequencies', 0),
                    'candidates_after_layer1': 0,
                    'candidates_after_layer2': 0,
                    'verdict': 'NO_CANDIDATES',
                },
            }, f, indent=2, default=str)
        
        print(f"\nResults saved to {output_path}")
        print(f"\nTotal time: {time.time()-total_start:.1f}s")
        return
    
    # Run targeted stacks
    stack_results = run_targeted_stacks(
        candidates, args.stack_width, epoch_labels, target=target,
    )
    
    # ─── Layer 2.5: Automated RFI Scorecard ─────────────────────
    layer25_result = analyze_all_candidates(
        candidates, stack_results, n_sigma=args.n_sigma,
    )
    
    # ─── Summary ───────────────────────────────────────────────────
    n_stacked = sum(1 for r in stack_results if r.get('stack_success'))
    n_with_peaks = sum(1 for r in stack_results
                       if r.get('stack_result', {}).get('peaks'))
    
    print(f"\n{'='*60}")
    print("PIPELINE SUMMARY")
    print(f"{'='*60}")
    print(f"  Layer 1 candidates: {len(candidates)}")
    print(f"  Layer 2 successfully stacked: {n_stacked}")
    print(f"  Layer 2 with peaks above threshold: {n_with_peaks}")
    
    # Save full results
    output_dir = os.path.join(SETI_ROOT, 'results', 'two_layer')
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir,
        f'{target}_tol{args.tolerance_hz}_ep{args.min_epochs}_snr{args.min_snr}.json')
    
    # Strip large arrays from saved data (they're too big for JSON)
    save_results = []
    for r in stack_results:
        sr = r.get('stack_result', {})
        save_results.append({
            'candidate': r['candidate'],
            'stack_success': r.get('stack_success', False),
            'error': r.get('error'),
            'n_epochs': sr.get('n_epochs'),
            'median': sr.get('median'),
            'sigma': sr.get('sigma'),
            'peaks': sr.get('peaks', []),
            'used_epochs': sr.get('used_epochs', []),
            'snr_improvement': sr.get('snr_improvement'),
            'epoch_info': sr.get('epoch_info', []),
        })
    
    with open(output_path, 'w') as f:
        json.dump({
            'target': target,
            'timestamp': time.strftime('%Y-%m-%dT%H:%M:%S'),
            'layer1': {
                'summary': xepoch_result['summary'],
                'candidates': candidates,
            },
            'layer2': {
                'results': save_results,
                'n_candidates_stacked': n_stacked,
            },
            'layer25': layer25_result,
            'summary': {
                'total_on_freqs': xepoch_result['summary'].get('total_on_frequencies', 0),
                'candidates_after_layer1': len(candidates),
                'candidates_after_layer2': n_with_peaks,
                'candidates_after_layer25': layer25_result['summary']['interesting'] + layer25_result['summary']['needs_review'],
                'verdict': 'CANDIDATES_FOUND' if n_with_peaks > 0 else 'NO_PEAKS_IN_STACK',
            },
        }, f, indent=2, default=str)
    
    print(f"\n  Results saved to {output_path}")
    print(f"  Total time: {time.time()-total_start:.1f}s")


if __name__ == '__main__':
    main()
