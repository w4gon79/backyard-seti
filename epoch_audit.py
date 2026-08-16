#!/usr/bin/env python3
"""epoch_audit.py - Per-epoch RFI zone audit.

Slides 4 MHz windows across each epoch's band, measures the ON-OFF
residual mean against the epoch's own noise floor, and flags windows
where |mean| > RATIO_THRESHOLD x floor. Unambiguous excursions that
confirm across >=2 ON/OFF pairs are auto-written to data/rfi_zones.json
via rfi_zones.add_zone() (HDF5 files are never touched).

Catches band-start gain plateaus (like 57904's 2743.957-2764 shelf,
+24 dB) without eyeballing stack plots.

Usage:
    python epoch_audit.py                     # audit all epochs
    python epoch_audit.py --epoch 57904       # audit one epoch
    python epoch_audit.py --dry-run           # report only, no zone writes
    python epoch_audit.py --window 4 --threshold 10

Output: console table + results/epoch_audit/audit_<epoch>.json
"""
import os
import sys
import json
import time
import argparse

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from incoherent_stack import find_h5, load_spectrum_window, _discover_epochs  # noqa: E402
import rfi_zones  # noqa: E402

WINDOW_MHZ = 4.0
RATIO_THRESHOLD = 10.0     # |window mean| vs epoch noise floor
CONFIRM_PAIRS = 2          # pairs that must agree before auto-zoning
MERGE_GAP_WIN = 1          # merge flagged windows separated by <=1 clean window
MIN_ZONE_MHZ = 2.0         # discard sub-2MHz zones (edge glitches)

BAND_START = 2743.957
BAND_STOP = 3444.152


def _pair_residual(mjd_int, on_seq, off_seq, f0, f1, target='PROXCEN'):
    """ON-OFF time-averaged residual for one window, or None."""
    on_path = find_h5(f"Parkes_{mjd_int}_{on_seq}_{target}_S_fine.h5")
    off_path = find_h5(f"Parkes_{mjd_int}_{off_seq}_{target}_R_fine.h5")
    if not on_path or not off_path:
        return None
    _, on_p = load_spectrum_window(on_path, f0, f1)
    if on_p is None:
        return None
    _, off_p = load_spectrum_window(off_path, f0, f1)
    if off_p is None:
        return None
    return on_p - off_p


def audit_epoch(label, window_mhz=WINDOW_MHZ, threshold=RATIO_THRESHOLD,
                confirm_pairs=CONFIRM_PAIRS, dry_run=False, verbose=True,
                progress_callback=None, target='PROXCEN'):
    """Audit one epoch of any Parkes target. Returns dict report (also
    written to results/epoch_audit/).

    progress_callback: optional fn(dict) with {'phase': 'scanning',
    'window', 'total', 'pair'} / {'phase': 'confirming'} / {'phase': 'done',
    'report'} for live UI updates.
    """
    # Fresh scan every run so newly downloaded epochs are found without
    # restarting anything
    epochs = _discover_epochs(target)
    if label not in epochs:
        print(f"Unknown epoch {label} of {target} (have: {sorted(epochs)})")
        return None
    info = epochs[label]
    mjd_int, seqs = info['mjd_int'], info['seqs']
    t0 = time.time()

    # Clamp band edges to this epoch's actual file coverage (e.g. 57791's
    # files stop at 3324.035 MHz while the sweep default assumes 3444).
    band_stop = BAND_STOP
    import h5py
    for _p in seqs:
        _hp = find_h5(f"Parkes_{mjd_int}_{_p[0]}_{target}_S_fine.h5")
        if _hp:
            with h5py.File(_hp, 'r') as _f:
                _a = _f['data'].attrs
                _fmax = float(_a['fch1']) + abs(float(_a['foff'])) * (int(_a['nchans']) - 1)
            band_stop = min(BAND_STOP, _fmax)
            break

    edges = np.arange(BAND_START, band_stop, window_mhz)
    n_win = len(edges)

    # Pass 1: scan band with the FIRST valid pair
    scan_pair = None
    means = np.full(n_win, np.nan)
    floors = np.full(n_win, np.nan)
    for pair_idx, pair in enumerate(seqs):
        ok = True
        for i, f0 in enumerate(edges):
            if i % 25 == 0:
                print(f"  [{label}] window {i}/{n_win} ({f0:.1f} MHz)...", flush=True)
            if progress_callback:
                progress_callback({'phase': 'scanning', 'window': i + 1,
                                   'total': n_win, 'pair': pair_idx + 1})
            r = _pair_residual(mjd_int, pair[0], pair[1], f0, f0 + window_mhz,
                               target=target)
            if r is None:
                ok = False
                break
            means[i] = float(np.mean(r))
            floors[i] = float(np.median(np.abs(r - np.median(r))))
        if ok:
            scan_pair = pair
            break
    if scan_pair is None:
        print(f"[{label}] no loadable ON/OFF pair found")
        return None

    good = np.isfinite(means) & np.isfinite(floors) & (floors > 0)
    if good.sum() < 10:
        print(f"[{label}] too few valid windows ({good.sum()}), abort")
        return None
    # Robust epoch noise floor: median of per-window MADs, and median |mean|
    floor_mad = float(np.median(floors[good]))
    floor_mean = float(np.median(np.abs(means[good])))
    noise_floor = max(floor_mad, floor_mean, 1e-6)

    ratios = np.abs(means) / noise_floor
    flagged = np.where(good & (ratios > threshold))[0]

    report = {
        'epoch': label,
        'target': target,
        'window_mhz': window_mhz,
        'noise_floor': noise_floor,
        'n_windows': int(good.sum()),
        'flagged_windows': [
            {'f_start': float(edges[i]), 'f_stop': float(edges[i] + window_mhz),
             'ratio': float(ratios[i]), 'mean': float(means[i])}
            for i in flagged
        ],
        'zones_written': [],
    }

    if verbose:
        print(f"\n[{label}] {n_win} windows @ {window_mhz} MHz, "
              f"floor={noise_floor:.3e}, flagged={len(flagged)} "
              f"({time.time()-t0:.0f}s)")

    if len(flagged) == 0:
        _write_report(report)
        if progress_callback:
            progress_callback({'phase': 'done', 'report': report})
        return report

    # Merge contiguous flagged windows (allow MERGE_GAP_WIN clean gap)
    regions = []
    start = prev = flagged[0]
    for i in flagged[1:]:
        if i - prev <= MERGE_GAP_WIN + 1:
            prev = i
        else:
            regions.append((start, prev))
            start = prev = i
    regions.append((start, prev))
    regions = [(edges[s], edges[e] + window_mhz,
                float(np.max(ratios[s:e + 1])))
               for s, e in regions
               if (edges[e] + window_mhz) - edges[s] >= MIN_ZONE_MHZ]

    # Pass 2: confirm each region on ALL other pairs (not just the first:
    # 57910 had pair2 clean + pair3 dirty, and first-only sampling missed it)
    if progress_callback:
        progress_callback({'phase': 'confirming'})
    other_pairs = [p for p in seqs if p != scan_pair]
    for f0, f1, r_max in regions:
        votes = 1  # scan pair already voted
        for pair in other_pairs:
            r = _pair_residual(mjd_int, pair[0], pair[1], f0, f1, target=target)
            if r is None:
                continue
            m = float(np.mean(r))
            if abs(m) / noise_floor > threshold:
                votes += 1
        if votes >= confirm_pairs:
            reason = (f"epoch_audit auto-zone: {votes}/{len(seqs)} pairs, "
                      f"ratio {r_max:.0f}x, "
                      f"measured {time.strftime('%Y-%m-%d')}")
            if dry_run:
                print(f"  DRY-RUN would zone {f0:.3f}-{f1:.3f} ({reason})")
                report['zones_written'].append({'f_start': f0, 'f_stop': f1,
                                                'reason': reason, 'dry_run': True})
            else:
                rfi_zones.add_zone(label, f0, f1, reason)
                print(f"  ZONED {f0:.3f}-{f1:.3f} MHz ({reason})")
                report['zones_written'].append({'f_start': f0, 'f_stop': f1,
                                                'reason': reason})
        else:
            print(f"  region {f0:.3f}-{f1:.3f} only {votes} pair vote(s), "
                  f"NOT zoned (needs {confirm_pairs})")

    _write_report(report)
    if progress_callback:
        progress_callback({'phase': 'done', 'report': report})
    return report


def _write_report(report):
    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           'results', 'epoch_audit')
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"audit_{report['epoch']}.json")
    with open(path, 'w') as f:
        json.dump(report, f, indent=2)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--epoch', help='audit a single epoch label')
    ap.add_argument('--target', default='PROXCEN',
                    help='target token as it appears in filenames '
                         '(default PROXCEN)')
    ap.add_argument('--window', type=float, default=WINDOW_MHZ)
    ap.add_argument('--threshold', type=float, default=RATIO_THRESHOLD)
    ap.add_argument('--confirm-pairs', type=int, default=CONFIRM_PAIRS)
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    labels = [args.epoch] if args.epoch else sorted(_discover_epochs(args.target))
    for label in labels:
        audit_epoch(label, window_mhz=args.window,
                    threshold=args.threshold,
                    confirm_pairs=args.confirm_pairs,
                    dry_run=args.dry_run, target=args.target)


if __name__ == '__main__':
    main()
