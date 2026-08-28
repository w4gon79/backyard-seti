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


def _load_zones():
    if not os.path.isfile(ZONES_PATH):
        return {}
    try:
        with open(ZONES_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


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

    results = []
    n_zone = n_zero = n_high = n_cluster = n_spread = 0
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
                        'drift_spread': n_spread},
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
