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
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'src'))
from barycentric_correct import (
    compute_barycentric_velocity,
    extract_mjd_from_filename,
    TARGET_COORDS,
)

# ---------------------------------------------------------------------------
# Module-level data -- extensible from external code (e.g. dashboard/app.py)
# ---------------------------------------------------------------------------

# Data file locations (checked in order)
FINE_DIRS = [
    r'D:\seti_data\fine',
    r'G:\seti\data\fine',
]

# Epoch definitions: label -> {mjd_int, seqs: [(on_seq, off_seq), ...]}
EPOCHS = {
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


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def get_available_epochs():
    """Return a copy of the EPOCHS dict (safe for inspection by callers)."""
    return dict(EPOCHS)


# ---------------------------------------------------------------------------
# Core functions (unchanged algorithm)
# ---------------------------------------------------------------------------

def find_h5(filename):
    """Find an HDF5 file across all known data directories."""
    for d in FINE_DIRS:
        path = os.path.join(d, filename)
        if os.path.isfile(path):
            return path
    return None


def load_spectrum_window(h5_path, f_start_mhz, f_stop_mhz):
    """Load a frequency window from an HDF5 file as a 1D power spectrum.
    
    Averages all time integrations to produce a single spectrum.
    Returns (freqs_mhz, power) or (None, None) on failure.
    """
    from blimpy import Waterfall
    
    try:
        wf = Waterfall(h5_path, load_data=True, f_start=f_start_mhz, f_stop=f_stop_mhz)
        data = np.array(wf.data, dtype=np.float64)  # shape: (n_tints, 1, n_chans)
        
        if data.ndim == 3:
            data = data[:, 0, :]  # squeeze IF
        
        # Average across time integrations -> 1D spectrum
        spectrum = np.mean(data, axis=0)
        
        # Get frequency axis
        try:
            freqs = np.array(wf.container.sf_freqs, dtype=np.float64)
        except Exception:
            # Fallback: compute from header
            h = wf.header
            fch1 = float(h['fch1'])
            nchans = int(h['nchans'])
            foff = float(h['foff'])
            freqs = np.array([fch1 + i * foff for i in range(nchans)])
        
        # Ensure freqs matches data length
        if len(freqs) != len(spectrum):
            n = len(spectrum)
            freqs = np.linspace(f_start_mhz, f_stop_mhz, n)
        
        return freqs, spectrum
    
    except Exception as e:
        print(f"    ERROR loading {os.path.basename(h5_path)}: {e}")
        return None, None
    except BaseException as e:
        # Some blimpy HDF5 corruption errors are BaseException, not Exception.
        print(f"    FATAL loading {os.path.basename(h5_path)}: {e}")
        return None, None


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
                  progress_callback=None):
    """Process one epoch: load ON/OFF pairs, subtract, correct, interpolate.
    
    Returns the stacked (averaged) residual spectrum for this epoch,
    or None on failure.
    
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
    first_on = f"Parkes_{mjd_int}_{seqs[0][0]}_PROXCEN_S_fine.h5"
    mjd = extract_mjd_from_filename(first_on)
    v_bary = compute_barycentric_velocity(mjd, target_ra, target_dec, telescope)
    c = 299792458.0
    correction_factor = 1.0 - v_bary / c
    
    print(f"    MJD: {mjd:.5f}, velocity: {v_bary:.1f} m/s, correction: {correction_factor:.10f}")
    
    residuals = []  # One residual spectrum per ON/OFF pair
    
    for pair_idx, (on_seq, off_seq) in enumerate(seqs):
        on_file = f"Parkes_{mjd_int}_{on_seq}_PROXCEN_S_fine.h5"
        off_file = f"Parkes_{mjd_int}_{off_seq}_PROXCEN_R_fine.h5"
        
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
        
        residual = on_power - off_power  # kills steady RFI
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
        return None
    
    # Average residuals across the 3 ON/OFF pairs within this epoch
    ref_freqs = residuals[0][0]
    avg_residual = np.zeros(len(ref_freqs))
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
    
    interpolated = np.interp(common_grid, bary_freqs_sorted, avg_residual_sorted)
    
    print(f"    Interpolated onto common grid: {len(common_grid)} bins")
    print(f"    Stack spectrum: mean={np.mean(interpolated):.2e}, std={np.std(interpolated):.2e}")
    
    if progress_callback:
        progress_callback({
            'phase': 'epoch_done',
            'epoch': epoch_label,
            'status': 'ok',
            'n_pairs': count,
            'mean': float(np.mean(interpolated)),
            'std': float(np.std(interpolated)),
        })
    
    return interpolated


def find_peaks(spectrum, grid, n_sigma=5, min_channels=3):
    """Find peaks in the stacked spectrum above n*sigma threshold.
    
    Returns list of dicts with freq_mhz, power, snr, width_chans.
    """
    median = np.median(spectrum)
    mad = np.median(np.abs(spectrum - median))
    sigma = 1.4826 * mad  # convert MAD to sigma
    
    if sigma == 0:
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

    # Resolve target coordinates
    target_key = target.upper().replace(' ', '_')
    if target_key not in TARGET_COORDS:
        msg = f"Unknown target: {target}"
        _cb({'phase': 'error', 'message': msg})
        return {'success': False, 'error': msg}
    target_ra, target_dec = TARGET_COORDS[target_key]

    # Build observed frequency load range (pad for Doppler)
    padding_mhz = 0.5
    f_start_obs = freq_center - width / 2 - padding_mhz
    f_stop_obs = freq_center + width / 2 + padding_mhz

    common_grid = build_common_grid(freq_center, width)

    # Process each epoch
    epoch_spectra = []
    used_epochs = []
    epoch_info = []

    for label in epoch_labels:
        if label not in EPOCHS:
            print(f"  Unknown epoch {label}, skipping")
            continue

        spec = process_epoch(
            label, EPOCHS[label], target_ra, target_dec,
            f_start_obs, f_stop_obs, common_grid, telescope,
            progress_callback=_cb if progress_callback else None,
        )
        if spec is not None:
            epoch_spectra.append(spec)
            used_epochs.append(label)
            ep_median = float(np.median(spec))
            ep_mad = float(np.median(np.abs(spec - ep_median)))
            ep_sigma = float(1.4826 * ep_mad)
            epoch_info.append({
                'label': label,
                'median': ep_median,
                'sigma': ep_sigma,
            })

    if len(epoch_spectra) < 2:
        msg = f"Need at least 2 epochs for stacking, got {len(epoch_spectra)}"
        _cb({'phase': 'error', 'message': msg})
        return {'success': False, 'error': msg}

    # Stack
    _cb({'phase': 'stacking', 'n_epochs': len(epoch_spectra)})

    stack = np.mean(epoch_spectra, axis=0)
    n = len(epoch_spectra)

    median = float(np.median(stack))
    mad = float(np.median(np.abs(stack - median)))
    sigma = float(1.4826 * mad)

    # Find peaks
    _cb({'phase': 'peak_finding', 'n_sigma': n_sigma})

    peaks = find_peaks(stack, common_grid, n_sigma=n_sigma)

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
                axes[i].axhline(np.median(epoch_spectra[i]),
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

    # Assemble results
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
    }

    # Save JSON
    if output_json:
        with open(output_json, 'w') as f:
            json.dump(results, f, indent=2)
        print(f"Results saved: {output_json}")

    _cb({'phase': 'complete', 'results': {k: v for k, v in results.items()
                                          if k != 'peaks'},
         'n_peaks': len(peaks)})

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
    target_key = args.target.upper().replace(' ', '_')
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
    target_ra, target_dec = TARGET_COORDS[target_key]
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
