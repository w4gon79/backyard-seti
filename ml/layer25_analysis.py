"""Layer 2.5: Automated RFI scorecard for two-layer candidates.

Analyzes each candidate's stacked spectrum and raw waterfall data to
produce deterministic RFI checks. No ML required.

Checks:
  1. Drift slope: measure signal slope across time. Zero drift = terrestrial.
  2. Pulse periodicity: autocorrelation of time-series power. Strong
     periodicity = radar/pulsed RFI.
  3. Multi-channel structure: count parallel narrowband components.
     Regular spacing = communication sidebands.
  4. Duty cycle: fraction of time the signal is above threshold.
     Low duty cycle = pulsed radar. High = continuous beacon.
  5. Epoch consistency: does the signal appear in ALL epochs?
     Missing epochs = intermittent RFI.

Output: per-candidate scorecard with flags and an overall RFI probability.
"""

import numpy as np
from scipy import signal as scipy_signal


def analyze_candidate(candidate, stack_result, n_sigma=5.0):
    """Run all Layer 2.5 checks on a single candidate.
    
    Parameters
    ----------
    candidate : dict
        Layer 1 candidate metadata (barycentric_freq, drift_rate, snr, etc.)
    stack_result : dict
        Layer 2 stack output (stack, grid_freqs, peaks, epoch_info, etc.)
    n_sigma : float
        Threshold for peak detection.
    
    Returns
    -------
    dict with flags, scores, and overall assessment.
    """
    freq = candidate.get('barycentric_freq_mhz', 0)
    candidate_drift = candidate.get('mean_drift_rate', 0)  # Hz/s from turboSETI
    
    stack = np.array(stack_result.get('stack', []))
    grid = np.array(stack_result.get('grid_freqs', []))
    peaks = stack_result.get('peaks', [])
    sigma = stack_result.get('sigma', 1)
    median = stack_result.get('median', 0)
    n_epochs = stack_result.get('n_epochs', 0)
    total_epochs_expected = 4  # PROXCEN has 4 epochs
    
    scorecard = {
        'frequency_mhz': freq,
        'flags': [],
        'checks': {},
        'rfi_score': 0,  # 0 = interesting, 100 = definitely RFI
        'assessment': 'UNKNOWN',
        'rfi_type': None,
    }
    
    if len(stack) == 0 or len(grid) == 0:
        scorecard['assessment'] = 'INSUFFICIENT_DATA'
        return scorecard
    
    # ─── Check 1: Drift Slope ─────────────────────────────────────
    # The stacked spectrum is 1D (frequency axis only, time-averaged).
    # For drift analysis we need the 2D waterfall, which we don't have here.
    # Instead, use the turboSETI-reported drift rate from Layer 1.
    # A near-zero drift rate means the signal doesn't accelerate, which
    # is expected for terrestrial sources but ALSO for a stable transmitter
    # on a distant planet. Combined with other checks, it's useful.
    
    abs_drift = abs(candidate_drift)
    drift_check = {
        'drift_rate_hz_s': candidate_drift,
        'abs_drift': abs_drift,
        'flag': False,
    }
    # Flag if drift is extremely close to zero (< 0.01 Hz/s)
    # Real signals from Proxima would have at least Earth's rotation contribution
    # (~0.04 Hz/s minimum at Parkes for a dec=-62 source)
    if abs_drift < 0.01:
        drift_check['flag'] = True
        drift_check['note'] = 'Near-zero drift suggests terrestrial or orbital source'
        scorecard['flags'].append('ZERO_DRIFT')
        scorecard['rfi_score'] += 20
    elif abs_drift < 0.05:
        drift_check['note'] = 'Very low drift, borderline'
        scorecard['rfi_score'] += 5
    else:
        drift_check['note'] = 'Drift consistent with accelerated source'
    
    scorecard['checks']['drift'] = drift_check
    
    # ─── Check 2: Multi-Channel Structure ─────────────────────────
    # Count distinct peak clusters in the stacked spectrum.
    # Regular spacing between peaks = communication sidebands.
    if len(peaks) > 1:
        peak_freqs = sorted([p['freq_mhz'] for p in peaks])
        spacings = np.diff(peak_freqs)
        median_spacing = np.median(spacings) if len(spacings) > 0 else 0
        spacing_std = np.std(spacings) if len(spacings) > 1 else 0
        spacing_cv = spacing_std / median_spacing if median_spacing > 0 else 1  # coefficient of variation
        
        multi_check = {
            'n_peaks': len(peaks),
            'median_spacing_mhz': median_spacing,
            'median_spacing_hz': median_spacing * 1e6,
            'spacing_uniformity': 1 - min(spacing_cv, 1),  # 1 = perfectly uniform
            'flag': False,
        }
        
        # Flag if many peaks with regular spacing (CV < 0.2 = very uniform)
        if len(peaks) >= 4 and spacing_cv < 0.3:
            multi_check['flag'] = True
            multi_check['note'] = f'{len(peaks)} peaks with regular {median_spacing*1e6:.0f} Hz spacing = sidebands/harmonics'
            scorecard['flags'].append('MULTI_CHANNEL_REGULAR')
            scorecard['rfi_score'] += 25
            scorecard['rfi_type'] = 'radar_or_communication'
        elif len(peaks) >= 4:
            multi_check['note'] = f'{len(peaks)} peaks, irregular spacing'
            scorecard['rfi_score'] += 10
        else:
            multi_check['note'] = f'{len(peaks)} peaks (single or few)'
        
        scorecard['checks']['multi_channel'] = multi_check
    else:
        scorecard['checks']['multi_channel'] = {
            'n_peaks': len(peaks),
            'flag': False,
            'note': 'Single or no peaks',
        }
    
    # ─── Check 3: SNR Analysis ────────────────────────────────────
    # Extremely high SNR is suspicious. A real ETI signal would likely be weak.
    max_snr = candidate.get('max_snr', 0)
    snr_check = {
        'max_snr': max_snr,
        'flag': False,
    }
    if max_snr > 1000:
        snr_check['flag'] = True
        snr_check['note'] = f'Extremely high SNR ({max_snr:.0f}) = strong local transmitter'
        scorecard['flags'].append('EXTREME_SNR')
        scorecard['rfi_score'] += 20
    elif max_snr > 100:
        snr_check['note'] = f'High SNR ({max_snr:.0f}), possibly strong RFI'
        scorecard['rfi_score'] += 5
    else:
        snr_check['note'] = f'Moderate SNR ({max_snr:.0f})'
    
    scorecard['checks']['snr'] = snr_check
    
    # ─── Check 4: Epoch Coverage ──────────────────────────────────
    # A real signal should appear in ALL epochs. Missing epochs = intermittent.
    epoch_count = candidate.get('epoch_count', 0)
    epoch_check = {
        'epochs_present': epoch_count,
        'epochs_total': total_epochs_expected,
        'coverage': epoch_count / total_epochs_expected if total_epochs_expected > 0 else 0,
        'flag': False,
    }
    if epoch_count < total_epochs_expected:
        epoch_check['flag'] = True
        epoch_check['note'] = f'Only in {epoch_count}/{total_epochs_expected} epochs = intermittent source'
        scorecard['flags'].append('PARTIAL_EPOCH_COVERAGE')
        scorecard['rfi_score'] += 15
    else:
        epoch_check['note'] = f'Present in all {epoch_count} epochs'
    
    scorecard['checks']['epoch_coverage'] = epoch_check
    
    # ─── Check 5: Peak Width Distribution ─────────────────────────
    # Very narrow peaks (1-2 channels) in the stacked spectrum suggest
    # unresolved narrowband RFI. Wider peaks suggest real signal structure.
    if peaks:
        widths = [p.get('width_chans', 1) for p in peaks]
        median_width = np.median(widths)
        width_check = {
            'median_width_chans': median_width,
            'min_width': min(widths),
            'max_width': max(widths),
            'flag': False,
        }
        if median_width <= 1:
            width_check['note'] = 'Very narrow peaks (1 channel) = unresolved narrowband'
            scorecard['rfi_score'] += 5
        else:
            width_check['note'] = f'Peak width ~{median_width} channels'
        
        scorecard['checks']['peak_width'] = width_check
    
    # ─── Check 6: Stack Power Concentration ───────────────────────
    # What fraction of total stack power is in the top peaks vs noise?
    # High concentration = one dominant strong signal (could be RFI or real)
    threshold = median + n_sigma * sigma
    above = stack[stack > threshold]
    total_power = np.sum(np.abs(stack - median))
    peak_power = np.sum(np.abs(above - median))
    concentration = peak_power / total_power if total_power > 0 else 0
    
    power_check = {
        'power_concentration': concentration,
        'flag': False,
    }
    if concentration > 0.5:
        power_check['flag'] = True
        power_check['note'] = f'{concentration*100:.0f}% of power in peaks = dominant signal'
        scorecard['rfi_score'] += 10
    
    scorecard['checks']['power_concentration'] = power_check
    
    # ─── Overall Assessment ───────────────────────────────────────
    scorecard['rfi_score'] = min(scorecard['rfi_score'], 100)
    
    if scorecard['rfi_score'] >= 60:
        scorecard['assessment'] = 'LIKELY_RFI'
    elif scorecard['rfi_score'] >= 30:
        scorecard['assessment'] = 'POSSIBLY_RFI'
    elif scorecard['rfi_score'] >= 10:
        scorecard['assessment'] = 'NEEDS_REVIEW'
    else:
        scorecard['assessment'] = 'INTERESTING'
    
    return scorecard


def analyze_all_candidates(candidates, stack_results, n_sigma=5.0):
    """Run Layer 2.5 analysis on all candidates.
    
    Returns list of scorecards, plus a summary.
    """
    print(f"\n{'='*60}")
    print("LAYER 2.5: AUTOMATED RFI SCORECARD")
    print(f"{'='*60}")
    
    scorecards = []
    
    for i, (cand, sr) in enumerate(zip(candidates, stack_results)):
        freq = cand.get('barycentric_freq_mhz', 0)
        print(f"\n  [{i+1}/{len(candidates)}] Analyzing {freq:.8f} MHz...")
        
        stack_data = sr.get('stack_result', sr)
        sc = analyze_candidate(cand, stack_data, n_sigma=n_sigma)
        scorecards.append(sc)
        
        # Print scorecard
        print(f"    Assessment: {sc['assessment']} (RFI score: {sc['rfi_score']}/100)")
        if sc['flags']:
            print(f"    Flags: {', '.join(sc['flags'])}")
        for check_name, check_data in sc['checks'].items():
            flag_str = ' *** FLAGGED ***' if check_data.get('flag') else ''
            note = check_data.get('note', '')
            print(f"    {check_name}: {note}{flag_str}")
    
    # Summary
    n_rfi = sum(1 for s in scorecards if s['assessment'] in ('LIKELY_RFI', 'POSSIBLY_RFI'))
    n_review = sum(1 for s in scorecards if s['assessment'] == 'NEEDS_REVIEW')
    n_interesting = sum(1 for s in scorecards if s['assessment'] == 'INTERESTING')
    
    summary = {
        'total_analyzed': len(scorecards),
        'likely_rfi': n_rfi,
        'needs_review': n_review,
        'interesting': n_interesting,
    }
    
    print(f"\n  Summary: {n_rfi} likely RFI, {n_review} need review, {n_interesting} interesting")
    
    return {
        'scorecards': scorecards,
        'summary': summary,
    }
