"""RFI zone handling for the SETI pipeline.

Zones live in data/rfi_zones.json. Two scopes:

1. Per-epoch, keyed by epoch label (MJD int as str): "57904"
   For junk specific to one observing session (bad sub-band,
   one-off gain plateau).

2. Telescope+band, keyed "gbt/L", "parkes/L": persistent site/band
   emitters (UPCS ~1921.4 at GBT L-band, radar, GPS sidelobes).
   These apply to EVERY epoch from that telescope+band, so they
   are written once instead of duplicated per epoch.

{
  "gbt/L": [
    {"f_start": 1918.0, "f_stop": 1925.0,
     "reason": "UPCS emitter ~1921.4 MHz, persistent at GBT L-band",
     "source": "manual", "zoned": "2026-08-19"}
  ],
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
- dashboard /api/rfi/zones: read/add/delete via the Stack page UI.

File writes (add_zone/delete_zone) are guarded by a threading lock and
atomic replace so concurrent stack jobs reading via the mtime cache
never see a torn JSON.
"""

import datetime
import json
import os
import re
import tempfile
import threading

SETI_ROOT = os.path.dirname(os.path.abspath(__file__))
ZONES_PATH = os.path.join(SETI_ROOT, 'data', 'rfi_zones.json')

_cache = {'mtime': None, 'zones': {}}
_lock = threading.Lock()

# GBT grammar: ..._guppi_<MJD>_<SEQ>_<TARGET>_<SCAN>.gpuspec.<tier>
# Band is encoded in the blcNNNN node string (0001020304050607 = L-band).
_GBT_PAT = re.compile(r'(?:spliced_)?(blc\d+)_guppi_(\d+)_')


def telescope_band_for_source_file(source_file):
    """Derive ('gbt'|'parkes'|None, 'L'|'S'|...|None) from a source
    filename. GBT: blc node maps to band letter. Parkes:
    'Parkes_<MJD>_...' carries no band info (single L-band receiver)."""
    base = os.path.basename(str(source_file or ''))
    m = _GBT_PAT.search(base)
    if m:
        blc = m.group(1)
        # blc node strings: 00-07 = L-band, 10-17 = S-band, 20-27 = C,
        # 30-37 = X, 50-57 = Ku (BL coset convention, first two digits).
        try:
            digits = [blc[3 + i * 2:5 + i * 2] for i in range(len(blc[3:]) // 2)]
            first = int(digits[0]) if digits else -1
            band = {0: 'L', 1: 'S', 2: 'C', 3: 'X', 5: 'Ku'}.get(first // 10, None)
        except (ValueError, IndexError):
            band = None
        return 'gbt', band
    if base.startswith('Parkes_'):
        return 'parkes', 'L'
    return None, None


def scope_key(telescope, band):
    """Canonical scope key for a telescope+band zone, e.g. 'gbt/L'."""
    tel = str(telescope or '').strip().lower()
    b = str(band or '').strip().upper()
    if not tel or not b or '/' in tel or '/' in b:
        return None
    return f'{tel}/{b}'


def _is_scope_key(key):
    """True if a JSON top-level key is a telescope/band scope."""
    return '/' in str(key)


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


def band_zones(telescope, band):
    """Zone list for a telescope+band scope ('gbt', 'L') -> gbt/L zones."""
    key = scope_key(telescope, band)
    return _load().get(key, []) if key else []


def zones_for(epoch_label, telescope=None, band=None):
    """Union of per-epoch zones and (if given) telescope+band zones."""
    out = list(_load().get(str(epoch_label), []))
    if telescope and band:
        out.extend(band_zones(telescope, band))
    return out


# ── Back-compat wrappers: telescope/band optional ────────────────────

def epoch_zones(epoch_label):
    """Return list of zone dicts for an epoch label ('57904') only."""
    return _load().get(str(epoch_label), [])


def zone_mask(freqs_mhz, epoch_label, telescope=None, band=None):
    """Boolean mask: True where freq is INSIDE a zone (epoch + band)."""
    import numpy as np
    zones = zones_for(epoch_label, telescope, band)
    if not zones:
        return None
    m = np.zeros(len(freqs_mhz), dtype=bool)
    for z in zones:
        m |= (freqs_mhz >= z['f_start']) & (freqs_mhz <= z['f_stop'])
    return m


def in_zone(freq_mhz, epoch_label, telescope=None, band=None):
    """True if a single frequency is zoned (epoch + band scopes)."""
    for z in zones_for(epoch_label, telescope, band):
        if z['f_start'] <= freq_mhz <= z['f_stop']:
            return True
    return False


def trim_window(f_start, f_stop, epoch_label, telescope=None, band=None):
    """Trim a [f_start, f_stop] window against zones.

    Handles zones at the window edges (the common case: band-start
    plateaus). Returns (new_start, new_stop) or (None, None) if the
    window is fully zoned. Zones fully inside the window are NOT
    cut (callers wanting interior gaps should use zone_mask instead).
    """
    fs, fe = float(f_start), float(f_stop)
    for z in zones_for(epoch_label, telescope, band):
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


def coverage_fraction(f_start, f_stop, epoch_label, telescope=None, band=None):
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
    for z in zones_for(epoch_label, telescope, band):
        ovl = min(z['f_stop'], f_stop) - max(z['f_start'], f_start)
        if ovl > 0:
            total += ovl
    return min(1.0, total / w)


# ── Mutation (thread-safe, atomic) ───────────────────────────────────

def _write_atomic(raw, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix='.tmp')
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(raw, f, indent=2)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
    _cache['mtime'] = None  # invalidate


def add_zone(scope, f_start, f_stop, reason, zones_path=None):
    """Append a zone (merging overlaps) and persist. Returns zones list.

    scope: epoch label ('57904') OR telescope/band scope ('gbt/L').
    """
    path = zones_path or ZONES_PATH
    with _lock:
        raw = {}
        if os.path.isfile(path):
            with open(path) as f:
                try:
                    raw = json.load(f)
                except ValueError:
                    raw = {}
        lst = raw.get(str(scope), [])
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
        raw[str(scope)] = lst
        _write_atomic(raw, path)
    return raw.get(str(scope), [])


def delete_zone(scope, f_start, f_stop, zones_path=None):
    """Delete the zone in `scope` matching f_start/f_stop (float match).

    Returns the updated list for that scope.
    """
    path = zones_path or ZONES_PATH
    with _lock:
        raw = {}
        if os.path.isfile(path):
            with open(path) as f:
                try:
                    raw = json.load(f)
                except ValueError:
                    raw = {}
        lst = raw.get(str(scope), [])
        lst = [z for z in lst
               if not (abs(z['f_start'] - float(f_start)) < 1e-9
                       and abs(z['f_stop'] - float(f_stop)) < 1e-9)]
        if lst:
            raw[str(scope)] = lst
        else:
            raw.pop(str(scope), None)
        _write_atomic(raw, path)
    return raw.get(str(scope), [])


def all_zones():
    """Whole file for UI display: {scope_key: [zones]} (mtime-cached read)."""
    return _load()
