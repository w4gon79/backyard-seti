"""Single-epoch candidate triage: Layer 2.5 scorecard checks that do NOT
require multiple epochs. Runs on a scan's ON/OFF rejection survivors.

Checks (deterministic, no ML):
  1. RFI zone match: barycentric (or topocentric) freq inside a known
     local RFI zone from data/rfi_zones.json (epoch MJD zones for Parkes,
     gbt/<band> zones for GBT).
  2. Near-zero drift: |drift_rate| < 0.001 Hz/s = fixed transmitter.
  3. High SNR: extremely strong = local.
  4. Cluster: many candidates packed within +/-150 kHz = RFI family /
     comms sidebands.
  5. Drift spread: same 10 kHz bin reported with drift spread > 2 Hz/s
     across files = inconsistent tracking, RFI.

Output: per-candidate flags + rfi_score (0-100), verdict buckets
(likely_rfi >= 60, suspicious >= 30, interesting < 30). Saved to
results/<scan_id>/triage/triage_results.json.
"""
import json
import os
import sqlite3

SETI_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(SETI_ROOT, 'data', 'seti_hits.db')
ZONES_PATH = os.path.join(SETI_ROOT, 'data', 'rfi_zones.json')

ZERO_DRIFT = 0.001        # Hz/s
HIGH_SNR = 1000.0
EXTREME_SNR = 10000.0
CLUSTER_HZ = 150_000.0   # +/- window for neighbor counting
CLUSTER_MIN = 5           # neighbors needed to flag
DRIFT_SPREAD = 2.0        # Hz/s within a 10 kHz bin
COMB_MIN_SPACING_HZ = 1000.0    # comb teeth closer than this = noise, ignore
COMB_MAX_SPACING_HZ = 10e6      # comb teeth wider than this = coincidence
COMB_TOL_HZ = 500.0              # membership tolerance around arithmetic grid
COMB_MIN_MEMBERS = 4             # 4+ hits on one arithmetic grid = comb
SPEC_COMB_WIN_MHZ = 0.25         # +/- spectrum window for comb check
SPEC_COMB_THR_DB = 12.0          # peak threshold above spectrum median
SPEC_COMB_MIN_PEAKS = 6          # peaks needed in window to test regularity
SPEC_COMB_TOL = 0.05             # spacing must agree within 5% of median
SPEC_COMB_MAX_READS = 400        # cap on H5 window reads per triage run


def _comb_in_spectrum(h5_path, freq_mhz):
    """Detect an evenly spaced RFI comb in the actual spectrum around a
    candidate. Loads a +/- SPEC_COMB_WIN_MHZ window, time-averages it,
    finds peaks above SPEC_COMB_THR_DB, and checks whether their spacing
    is regular. Returns (n_peaks, spacing_hz) or None."""
    if not os.path.isfile(h5_path):
        return None
    try:
        import numpy as np
        import sys as _sys
        if SETI_ROOT not in _sys.path:
            _sys.path.insert(0, SETI_ROOT)
        from incoherent_stack import load_spectrum_window_2d
        freqs, data = load_spectrum_window_2d(
            h5_path, freq_mhz - SPEC_COMB_WIN_MHZ, freq_mhz + SPEC_COMB_WIN_MHZ)
        if freqs is None or data is None or data.size == 0:
            return None
        spec = data.mean(axis=0)
        pos = spec[spec > 0]
        if pos.size == 0:
            return None
        db = 10.0 * np.log10(np.maximum(spec, 1.0) / np.median(pos))
        n = len(db)
        peaks = []
        i = 1
        while i < n - 1:
            if db[i] >= SPEC_COMB_THR_DB and db[i] >= db[i-1] and db[i] >= db[i+1]:
                if not peaks or i - peaks[-1] >= 10:
                    peaks.append(i)
                elif db[i] > db[peaks[-1]]:
                    peaks[-1] = i
            i += 1
        if len(peaks) < SPEC_COMB_MIN_PEAKS:
            return None
        pf = np.array([freqs[p] for p in peaks])
        sp = np.diff(pf) * 1e6          # Hz
        med = float(np.median(sp))
        if med < COMB_MIN_SPACING_HZ:
            return None
        n_reg = int(np.sum(np.abs(sp - med) <= SPEC_COMB_TOL * med))
        # a real comb: nearly every consecutive spacing matches the median
        if n_reg >= len(sp) - 2 and n_reg >= SPEC_COMB_MIN_PEAKS - 2:
            return (len(peaks), med)
        return None
    except Exception:
        return None


def _resolve_fine_path(source_file):
    for cand in (os.path.join(SETI_ROOT, 'data', 'fine', source_file),
                 os.path.join(SETI_ROOT, 'data', source_file)):
        if os.path.isfile(cand):
            return cand
    return None


def _load_zones():
    if not os.path.isfile(ZONES_PATH):
        return {}
    try:
        with open(ZONES_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def _detect_combs(cands):
    """Find evenly spaced frequency combs (RFI sidebands / digitizer spurs).

    Seed spacings from adjacent sorted freq gaps, then greedily chain
    candidates that sit on the same arithmetic grid (f0 + k*spacing).
    Returns {freq_mhz: (n_members, spacing_hz)} for comb members only.
    """
    import bisect
    freqs = sorted({round(c.get('freq', 0) or 0, 6) for c in cands})
    if len(freqs) < COMB_MIN_MEMBERS:
        return {}

    # Candidate spacings: ALL pairwise diffs within max spacing, keeping
    # spacings supported by >= COMB_MIN_MEMBERS-1 independent pairs.
    # (Adjacent-gap seeding misses combs whose members are interleaved
    # with unrelated candidates, which is the common case.)
    diffs = []
    for i, f in enumerate(freqs):
        j = i + 1
        while j < len(freqs) and freqs[j] - f <= COMB_MAX_SPACING_HZ / 1e6:
            diffs.append(freqs[j] - f)
            j += 1
    diffs.sort()
    tol = COMB_TOL_HZ / 1e6
    seeds = []
    lo = 0
    for hi in range(len(diffs)):
        while diffs[hi] - diffs[lo] > tol:
            lo += 1
        if (hi - lo + 1) >= COMB_MIN_MEMBERS - 1:
            seeds.append(diffs[(lo + hi) // 2])
            lo = hi + 1          # consume this run, look for the next
    seeds = [s for s in seeds
             if s >= COMB_MIN_SPACING_HZ / 1e6][:200]   # cost cap

    combs = {}
    for d in seeds:
        i = 0
        while i < len(freqs):
            # walk the arithmetic chain from freqs[i]
            chain = [freqs[i]]
            cur = freqs[i]
            while True:
                j = bisect.bisect_left(freqs, cur + d - tol)
                if j < len(freqs) and abs(freqs[j] - (cur + d)) <= tol:
                    chain.append(freqs[j])
                    cur = freqs[j]
                else:
                    break
            if len(chain) >= COMB_MIN_MEMBERS:
                for f in chain:
                    # keep the largest chain a freq belongs to
                    if f not in combs or len(chain) > combs[f][0]:
                        combs[f] = (len(chain), d * 1e6)
                i += len(chain)          # skip past this chain
            else:
                i += 1
    return combs


def _applicable_zones(zones, scan_id, files):
    """Parkes zones are keyed by epoch MJD (matches scan_id part 2);
    GBT zones are keyed 'gbt/<band>' and apply to any guppi scan."""
    out = []
    is_gbt = any('guppi' in os.path.basename(str(f)) for f in files or [])
    parts = scan_id.split('_')
    epoch = parts[1] if len(parts) > 1 and parts[1].isdigit() else None
    for key, zlist in zones.items():
        if key.startswith('gbt/'):
            if is_gbt:
                out.extend(zlist)
        elif key.isdigit():
            if epoch and key == epoch:
                out.extend(zlist)
    return out


def run_triage(scan_id, scan_dir):
    reject_path = os.path.join(scan_dir, 'rejection', 'rejection_results.json')
    if not os.path.isfile(reject_path):
        return {'error': 'No rejection run for this scan yet. Run ON/OFF rejection first.'}
    with open(reject_path) as f:
        rej = json.load(f)
    cands = rej.get('candidates', [])
    if not cands:
        return {'error': 'Rejection produced zero candidates - nothing to triage.'}

    # --- barycentric freqs from DB, keyed (source_file, freq) ---
    bary = {}
    try:
        conn = sqlite3.connect(DB_PATH)
        for row in conn.execute(
                "select source_file, freq, barycentric_freq from hits "
                "where scan_id=? and barycentric_freq is not null "
                "and on_off='ON'", (scan_id,)):
            bary[(row[0], row[1])] = row[2]
        conn.close()
    except Exception:
        bary = {}

    zones = _applicable_zones(_load_zones(), scan_id,
                              rej.get('parameters', {}).get('files') or
                              [c.get('source_file', '') for c in cands])

    freqs = [c.get('freq', 0) for c in cands]
    freqs_sorted = sorted(freqs)

    comb_members = _detect_combs(cands)

    results = []
    n_zone = n_zero = n_high = n_cluster = n_spread = n_comb = 0
    for c in cands:
        sf = c.get('source_file') or c.get('file', '')
        bf = bary.get((sf, c.get('freq')))
        score = 0
        flags = []

        # 1. RFI zone (bary first, then topocentric)
        f_check = bf if bf is not None else c.get('freq', 0)
        for z in zones:
            if z['f_start'] <= f_check <= z['f_stop']:
                flags.append('rfi_zone:' + z.get('reason', '')[:40])
                score += 100
                n_zone += 1
                break

        # 2. near-zero drift
        dr = abs(c.get('drift_rate', 0) or 0)
        if dr < ZERO_DRIFT:
            flags.append('zero_drift')
            score += 25
            n_zero += 1

        # 3. high SNR
        snr = c.get('snr', 0) or 0
        if snr > EXTREME_SNR:
            flags.append('extreme_snr')
            score += 35
            n_high += 1
        elif snr > HIGH_SNR:
            flags.append('high_snr')
            score += 20
            n_high += 1

        # 4. cluster: neighbors within +/- CLUSTER_HZ
        fq = c.get('freq', 0)
        import bisect
        lo = bisect.bisect_left(freqs_sorted, fq - CLUSTER_HZ / 1e6)
        hi = bisect.bisect_right(freqs_sorted, fq + CLUSTER_HZ / 1e6)
        neighbors = (hi - lo) - 1
        if neighbors >= CLUSTER_MIN:
            flags.append(f'cluster:{neighbors}')
            score += 20
            n_cluster += 1

        # 6. comb: member of an evenly spaced frequency grid (sidebands)
        cm = comb_members.get(round(c.get('freq', 0) or 0, 6))
        if cm:
            flags.append(f'comb:{cm[0]}x{cm[1]/1e3:.1f}kHz')
            score += 60
            n_comb += 1

        # 5. drift spread within 10 kHz bin
        bin_f = round(fq, 5)
        drifts = [abs(d.get('drift_rate', 0) or 0) for d in cands
                  if abs(d.get('freq', 0) - bin_f) < 0.005]
        if drifts and (max(drifts) - min(drifts)) > DRIFT_SPREAD:
            flags.append('drift_spread')
            score += 15
            n_spread += 1

        score = min(100, score)
        verdict = ('likely_rfi' if score >= 60 else
                   'suspicious' if score >= 30 else 'interesting')
        results.append({
            'freq': c.get('freq'),
            'barycentric_freq': bf,
            'drift_rate': c.get('drift_rate'),
            'snr': round(snr, 1),
            'source_file': sf,
            'rfi_score': score,
            'flags': flags,
            'verdict': verdict,
        })

    # --- spectrum comb pass: would-be-interesting candidates only ---
    # Group clean candidates by 50 kHz bin per file, load one H5 window per
    # group, and flag the whole group if the spectrum shows a regular comb.
    # Skips silently when the fine files are no longer on disk.
    reads = 0
    groups = {}
    for r in results:
        if r['rfi_score'] >= 30 or reads >= SPEC_COMB_MAX_READS:
            continue
        key = (r['source_file'], int((r['freq'] or 0) * 20))   # 50 kHz bins
        g = groups.setdefault(key, [])
        g.append(r)
    for (sf, _kb), members in groups.items():
        if reads >= SPEC_COMB_MAX_READS:
            break
        rep = max(members, key=lambda r: r['snr'] or 0)
        path = _resolve_fine_path(sf)
        if not path:
            continue
        reads += 1
        hit = _comb_in_spectrum(path, rep['freq'])
        if not hit:
            continue
        for r in members:
            r['flags'].append(f"comb:spec:{hit[0]}x{hit[1]/1e3:.1f}kHz")
            r['rfi_score'] = min(100, r['rfi_score'] + 60)
            r['verdict'] = ('likely_rfi' if r['rfi_score'] >= 60 else
                            'suspicious' if r['rfi_score'] >= 30 else 'interesting')
            n_comb += 1

    # interesting first, then by score ascending
    order = {'interesting': 0, 'suspicious': 1, 'likely_rfi': 2}
    results.sort(key=lambda r: (order[r['verdict']], r['rfi_score'],
                                -(r['snr'] or 0)))

    out = {
        'scan_id': scan_id,
        'n_candidates': len(cands),
        'n_bary_matched': sum(1 for r in results if r['barycentric_freq'] is not None),
        'flag_counts': {'rfi_zone': n_zone, 'zero_drift': n_zero,
                        'high_snr': n_high, 'cluster': n_cluster,
                        'drift_spread': n_spread, 'comb': n_comb},
        'verdict_counts': {v: sum(1 for r in results if r['verdict'] == v)
                           for v in ('interesting', 'suspicious', 'likely_rfi')},
        'candidates': results,
    }
    tdir = os.path.join(scan_dir, 'triage')
    os.makedirs(tdir, exist_ok=True)
    with open(os.path.join(tdir, 'triage_results.json'), 'w') as f:
        json.dump(out, f, indent=1)
    return out


if __name__ == '__main__':
    import sys
    sid = sys.argv[1]
    sdir = os.path.join(SETI_ROOT, 'results', sid)
    r = run_triage(sid, sdir)
    print(json.dumps({k: v for k, v in r.items() if k != 'candidates'},
                     indent=1))
    for c in r.get('candidates', [])[:20]:
        print(c)
