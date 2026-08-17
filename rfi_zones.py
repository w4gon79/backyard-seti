"""Per-epoch RFI zone handling for the SETI pipeline.

Zones live in data/rfi_zones.json, keyed by epoch label (MJD int as str):

{
  "57904": [
    {"f_start": 2743.957, "f_stop": 2764.0,
     "reason": "band-start gain plateau, measured 2026-08-14"}
  ]
}

The 12 GB HDF5 files are NEVER modified. Consumers:
- incoherent_stack.process_epoch: masks zoned bins (NaN) before barycentric
  interpolation; stack math uses nan-aware stats so masked bins simply
  don't vote.
- fine_res_pipeline: trims/skips sub-bands that fall inside zones.
- db import: flags hits landing inside zones as rfi_zoned=1.
- epoch_audit: measures residuals and writes new zones (auto for
  unambiguous >10x excursions).
"""

import json
import os

SETI_ROOT = os.path.dirname(os.path.abspath(__file__))
ZONES_PATH = os.path.join(SETI_ROOT, 'data', 'rfi_zones.json')

_cache = {'mtime': None, 'zones': {}}


def _load():
    mtime = os.path.getmtime(ZONES_PATH) if os.path.isfile(ZONES_PATH) else None
    if mtime != _cache['mtime']:
        zones = {}
        if mtime is not None:
            try:
                with open(ZONES_PATH) as f:
                    raw = json.load(f)
                for k, v in raw.items():
                    zones[str(k)] = [
                        {'f_start': float(z['f_start']),
                         'f_stop': float(z['f_stop']),
                         'reason': str(z.get('reason', ''))}
                        for z in v
                    ]
            except (ValueError, KeyError, TypeError):
                zones = {}
        _cache['mtime'] = mtime
        _cache['zones'] = zones
    return _cache['zones']


def epoch_zones(epoch_label):
    """Return list of zone dicts for an epoch label ('57904')."""
    return _load().get(str(epoch_label), [])


def zone_mask(freqs_mhz, epoch_label):
    """Boolean mask: True where freq is INSIDE a zone for this epoch."""
    import numpy as np
    zones = epoch_zones(epoch_label)
    if not zones:
        return None
    m = np.zeros(len(freqs_mhz), dtype=bool)
    for z in zones:
        m |= (freqs_mhz >= z['f_start']) & (freqs_mhz <= z['f_stop'])
    return m


def in_zone(freq_mhz, epoch_label):
    """True if a single frequency is zoned for this epoch."""
    for z in epoch_zones(epoch_label):
        if z['f_start'] <= freq_mhz <= z['f_stop']:
            return True
    return False


def trim_window(f_start, f_stop, epoch_label):
    """Trim a [f_start, f_stop] window against this epoch's zones.

    Handles zones at the window edges (the common case: band-start
    plateaus). Returns (new_start, new_stop) or (None, None) if the
    window is fully zoned. Zones fully inside the window are NOT
    cut (callers wanting interior gaps should use zone_mask instead).
    """
    fs, fe = float(f_start), float(f_stop)
    for z in epoch_zones(epoch_label):
        zs, ze = z['f_start'], z['f_stop']
        if zs <= fs and ze >= fe:
            return None, None          # fully covered
        if zs <= fs < ze:
            fs = ze                    # zone covers the start edge
        elif zs < fe <= ze:
            fe = zs                    # zone covers the stop edge
    if fe - fs <= 0:
        return None, None
    return fs, fe


def coverage_fraction(f_start, f_stop, epoch_label):
    """Fraction of the window covered by zones (0..1).

    Frequency order agnostic: GBT h5 files run descending (f_start > f_stop),
    so normalize orientation before measuring. A truly zero-width window is
    treated as fully covered (degenerate).
    """
    total = 0.0
    if f_start > f_stop:
        f_start, f_stop = f_stop, f_start
    w = float(f_stop) - float(f_start)
    if w <= 0:
        return 1.0
    for z in epoch_zones(epoch_label):
        ovl = min(z['f_stop'], f_stop) - max(z['f_start'], f_start)
        if ovl > 0:
            total += ovl
    return min(1.0, total / w)


def add_zone(epoch_label, f_start, f_stop, reason, zones_path=None):
    """Append a zone (merging overlaps) and persist. Returns zones list."""
    path = zones_path or ZONES_PATH
    raw = {}
    if os.path.isfile(path):
        with open(path) as f:
            try:
                raw = json.load(f)
            except ValueError:
                raw = {}
    lst = raw.get(str(epoch_label), [])
    new = {'f_start': float(f_start), 'f_stop': float(f_stop),
           'reason': str(reason)}
    # merge with any overlapping zone
    merged = False
    for z in lst:
        if (new['f_start'] <= z['f_stop'] and z['f_start'] <= new['f_stop']):
            z['f_start'] = min(z['f_start'], new['f_start'])
            z['f_stop'] = max(z['f_stop'], new['f_stop'])
            z['reason'] = z['reason'] or new['reason']
            merged = True
            break
    if not merged:
        lst.append(new)
    lst.sort(key=lambda z: z['f_start'])
    raw[str(epoch_label)] = lst
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        json.dump(raw, f, indent=2)
    _cache['mtime'] = None  # invalidate
    return lst
