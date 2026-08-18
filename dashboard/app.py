#!/usr/bin/env python3
"""
app.py - SETI Dashboard backend (Flask)

Serves the web UI and provides API endpoints for:
  - BL target search (proxy to seti.berkeley.edu/opendata API)
  - Pipeline control (start/stop/status scans)
  - Hit results (JSON from pipeline output)
  - Sky map data (target coordinates)

Port: 8070
"""

import os
import sys
import json
import glob as glob_module
import threading
import time
import subprocess
import re
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from flask import Flask, render_template, jsonify, request, send_from_directory, make_response
import numpy as np
from dotenv import load_dotenv

# Add src to path for imports
SETI_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(SETI_ROOT, 'src'))
sys.path.insert(0, SETI_ROOT)  # for incoherent_stack.py

# Load .env for local config (secondary data paths, etc.)
load_dotenv(os.path.join(SETI_ROOT, '.env'))

app = Flask(__name__, 
            template_folder='templates',
            static_folder='static')

# Force UTF-8 for all static file responses (d3-celestial data files contain
# Unicode star names that crash without proper charset)
app.config['JSON_AS_ASCII'] = False
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0

@app.after_request
def add_utf8_headers(response):
    if response.mimetype == 'application/json' or response.mimetype == 'text/plain':
        response.headers['Content-Type'] = response.mimetype + '; charset=utf-8'
    return response

# Configuration
DATA_DIR = os.path.join(SETI_ROOT, 'data')
RESULTS_DIR = os.path.join(SETI_ROOT, 'results')
FINE_DIR = os.path.join(DATA_DIR, 'fine')
MID_DIR = os.path.join(DATA_DIR, 'mid')
FILT_DIR = os.path.join(DATA_DIR, 'filterbank')
H5_DIR = os.path.join(DATA_DIR, 'h5')
PROXCEN_DIR = os.path.join(DATA_DIR, 'PROXCEN')
# Per-target archive root (3B): ARCHIVE_ROOT/{TARGET}/fine on D:
ARCHIVE_ROOT = r'D:\seti_data'

# Secondary data storage path (for large .h5 files moved off the main drive)
# Configured via .env: SETI_DATA_SECONDARY=D:\seti_data\fine
DATA_DIRS_SECONDARY = [
    d.strip() for d in os.environ.get('SETI_DATA_SECONDARY', '').split(';')
    if d.strip() and os.path.isdir(d.strip())
]


def _resolve_data_file(filepath):
    """Resolve a data file path, checking primary then secondary locations.

    Checks in order:
    1. Absolute path as-is
    2. SETI_ROOT/filepath
    3. SETI_ROOT/data/filepath
    4. DATA_DIR/filepath
    5. Each secondary data dir / filepath (e.g. D:\seti_data\fine / filepath)

    Returns the first existing path, or None if not found.
    """
    candidates = [
        filepath,
        os.path.join(SETI_ROOT, filepath),
        os.path.join(SETI_ROOT, 'data', filepath),
        os.path.join(DATA_DIR, filepath),
    ]
    # 3B per-target archive: D:\seti_data\{TARGET}\fine\<basename>
    _tgt = extract_target_name(filepath)
    if _tgt:
        candidates.append(os.path.join(
            ARCHIVE_ROOT, _tgt, 'fine',
            os.path.basename(filepath.replace('\\', '/'))))
    for sec_dir in DATA_DIRS_SECONDARY:
        candidates.append(os.path.join(sec_dir, filepath))
        # Also try without the 'fine/' prefix since secondary dir may already be the fine dir
        if filepath.startswith('fine/') or filepath.startswith('fine\\'):
            candidates.append(os.path.join(sec_dir, filepath[5:]))

    for c in candidates:
        if os.path.isfile(c):
            return c
    return None


def extract_target_name(filename):
    """Extract target name from any BL filename format.
    
    Parkes: Parkes_57791_72989_PROXCEN_S_fine.h5  -> PROXCEN (parts[3])
    GBT: spliced_blc00..._guppi_57544_50437_HIP113357_0030.gpuspec.0002.h5 -> HIP113357
    """
    parts = filename.split('_')
    # Parkes format: Telescope_MJD_Seq_TARGET_S/R_res.h5
    if len(parts) >= 4 and parts[0] in ('Parkes', 'GBT', 'FAST', 'MeerKAT', 'Effelsberg'):
        return parts[3]
    # GBT/blc format: look for a part that looks like a target name (uppercase letters/digits)
    for p in parts:
        upper = p.upper()
        if upper.startswith(('HIP', 'HD', 'HR', 'KIC', 'TIC', 'TOI', 'KEPLER', 'WO', 'GJ', 'GLIESE', 'NGC', 'M', 'PROXIMA', 'PROXCEN', 'TABBY')):
            # Strip any trailing file extension artifacts
            clean = p.split('.')[0]
            return clean
    # Fallback: parts[3] if it doesn't look like a number
    if len(parts) > 3:
        p3 = parts[3]
        if not p3.replace('.', '').isdigit():
            return p3
    return 'unknown'

# Track running scans
scan_state = {
    'active': False,
    'pid': None,
    'progress': {},
    'log_lines': [],
    'active_scan_id': None,  # Track which scan_id is running
    'scan_start_time': None,
    # Structured progress tracking
    'sub_bands_done': 0,
    'sub_bands_total': 0,
    'current_sub_band': 0,
    'current_freq_start': 0,
    'current_freq_stop': 0,
    'current_freq': 0,
    'total_hits': 0,
    'subband_hits': [],
    'recent_hits': [],
    'target': '',
    'freq_start': 0,
    'freq_end': 0,
    'scan_dir': '',
    # Fix 2: current file tracking
    'current_file': '',
    'current_file_index': 0,
    'file_total': 0,
    # Fix 3: ON/OFF hit accumulation
    'on_hits': 0,
    'off_hits': 0,
    # Fix 8: per-file hit tracking
    'file_hits': 0,
}

# Track downloads
download_state = {
    'queue': [],  # List of {url, filename, target_dir, status, progress, speed, eta, size_total, size_done}
    'active': None,  # Currently downloading item
}


# ─── Routes ────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/mission')
def mission():
    resp = make_response(render_template('mission.html'))
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    resp.headers['Pragma'] = 'no-cache'
    resp.headers['Expires'] = '0'
    return resp


# ─── API: Target Search ───────────────────────────────────────────────

def mjd_to_date(mjd_str):
    """Convert MJD string to YYYY-MM-DD date string."""
    try:
        mjd = int(mjd_str)
        d = datetime(1858, 11, 17) + timedelta(days=mjd)
        return d.strftime('%Y-%m-%d')
    except (ValueError, TypeError):
        return ''


# GBT local-data grammar: (spliced_)blcNN_guppi_MJD_SEQ_TARGET_SCAN.PROD.TIER.h5
_GBT_LOCAL_PAT = re.compile(
    r'(?:spliced_)?blc\d+_guppi_(\d+)_(\d+)_([A-Za-z0-9+\-.]+?)_(\d+)'
    r'\.(rawspec|gpuspec)\.(\d+)\.h5$')




def _file_is_on(fname, scan_target=''):
    """True if fname is an ON-source observation.

    Parkes: _S_ marker (ON) vs _R_ (OFF).
    GBT: guppi target token equals the scan target (ABACAD A-scans are ON).
    """
    fname = os.path.basename(str(fname or ''))
    if '_S_' in fname:
        return True
    if '_R_' in fname:
        return False
    if 'guppi_' in fname:
        m = _GBT_LOCAL_PAT.search(fname)
        if m:
            tok = m.group(3).upper()
            return bool(tok) and tok == str(scan_target or '').upper()
    return False
def _local_file_target_mjd(fname):
    """(target, mjd, grammar) for any local BL filename, or None.
    Parkes: Parkes_57910_34684_PROXCEN_S_fine.h5 -> (PROXCEN, 57910)
    GBT: spliced_blc.._guppi_57532_03953_GJ447_0011.gpuspec.0000.h5
         -> (GJ447, 57532)
    """
    parts = fname.split('_')
    if parts[0] in ('Parkes', 'GBT', 'APF', 'FAST', 'MeerKAT', 'Effelsberg') \
            and len(parts) >= 5:
        return parts[3], parts[1], 'parkes'
    m = _GBT_LOCAL_PAT.search(fname)
    if m:
        return m.group(3), m.group(1), 'gbt'
    return None


@app.route('/api/targets')
def api_targets():
    """List local data files grouped by target."""
    targets = {}

    def _entry(fname, base_dir, rel_prefix, size_div):
        info = _local_file_target_mjd(fname)
        if not info:
            return
        target, mjd, grammar = info
        if target not in targets:
            targets[target] = {'fine': [], 'mid': [], 'filterbank': [], 'h5': []}
        targets[target]['fine'].append({
            'name': fname,
            'size_gb': round(os.path.getsize(os.path.join(base_dir, fname)) / size_div, 2),
            'path': f'{rel_prefix}/{fname}',
            'date': mjd_to_date(mjd),
            'grammar': grammar,
        })

    # Scan fine-res (Parkes grammar + GBT guppi fine products)
    if os.path.isdir(FINE_DIR):
        for f in os.listdir(FINE_DIR):
            if f.endswith('.h5'):
                _entry(f, FINE_DIR, 'fine', 1e9)
    
    # Scan mid-res
    if os.path.isdir(MID_DIR):
        for f in os.listdir(MID_DIR):
            if f.endswith('.h5'):
                parts = f.split('_')
                if len(parts) >= 4:
                    target = parts[3]
                    if target not in targets:
                        targets[target] = {'fine': [], 'mid': [], 'filterbank': [], 'h5': []}
                    if target in targets:
                        targets[target]['mid'].append({
                            'name': f,
                            'size_gb': round(os.path.getsize(os.path.join(MID_DIR, f)) / 1e6, 1),
                            'path': f'mid/{f}',
                            'date': mjd_to_date(parts[1]) if len(parts) >= 2 else '',
                        })
    
    # Scan filterbank
    if os.path.isdir(FILT_DIR):
        for f in os.listdir(FILT_DIR):
            if f.endswith('.fil'):
                info = _local_file_target_mjd(f)
                target = info[0] if info else (f.split('_')[3] if len(f.split('_')) >= 4 else 'unknown')
                if target not in targets:
                    targets[target] = {'fine': [], 'mid': [], 'filterbank': [], 'h5': []}
                targets[target]['filterbank'].append({
                    'name': f,
                    'size_gb': round(os.path.getsize(os.path.join(FILT_DIR, f)) / 1e9, 2),
                    'path': f'filterbank/{f}',
                    'date': mjd_to_date(info[1]) if info else '',
                })
    
    # Scan generic h5 files (no resolution marker)
    if os.path.isdir(H5_DIR):
        for f in os.listdir(H5_DIR):
            if f.endswith('.h5'):
                target = extract_target_name(f)
                if target:
                    if target not in targets:
                        targets[target] = {'fine': [], 'mid': [], 'filterbank': [], 'h5': []}
                    targets[target]['h5'].append({
                        'name': f,
                        'size_gb': round(os.path.getsize(os.path.join(H5_DIR, f)) / 1e9, 2),
                        'path': f'h5/{f}',
                        'date': mjd_to_date(f.split('_')[1]) if len(f.split('_')) >= 2 else '',
                    })
    
    # Scan secondary data dirs (e.g. D:\seti_data\fine)
    for sec_dir in DATA_DIRS_SECONDARY:
        if not os.path.isdir(sec_dir):
            continue
        for f in os.listdir(sec_dir):
            if f.endswith('.h5'):
                info = _local_file_target_mjd(f)
                if not info:
                    continue
                target = info[0]
                if target not in targets:
                    targets[target] = {'fine': [], 'mid': [], 'filterbank': [], 'h5': []}
                # Avoid duplicates: skip if already listed from primary
                existing_names = [item['name'] for item in targets[target]['fine']]
                if f not in existing_names:
                    targets[target]['fine'].append({
                        'name': f,
                        'size_gb': round(os.path.getsize(os.path.join(sec_dir, f)) / 1e9, 2),
                        'path': f'fine/{f}',  # _resolve_data_file checks secondary dirs too
                        'date': mjd_to_date(info[1]),
                    })

    # Per-target archive dirs (3B): D:\seti_data\{TARGET}\fine
    if os.path.isdir(ARCHIVE_ROOT):
        for tdir in sorted(os.listdir(ARCHIVE_ROOT)):
            tfine = os.path.join(ARCHIVE_ROOT, tdir, 'fine')
            if not os.path.isdir(tfine):
                continue
            for f in os.listdir(tfine):
                if not f.endswith('.h5'):
                    continue
                info = _local_file_target_mjd(f)
                if not info:
                    continue
                target = info[0]
                if target not in targets:
                    targets[target] = {'fine': [], 'mid': [], 'filterbank': [], 'h5': []}
                existing_names = [item['name'] for item in targets[target]['fine']]
                if f not in existing_names:
                    targets[target]['fine'].append({
                        'name': f,
                        'size_gb': round(os.path.getsize(os.path.join(tfine, f)) / 1e9, 2),
                        'path': f'fine/{f}',  # _resolve_data_file finds archive paths
                        'date': mjd_to_date(info[1]),
                    })

    # Also scan old PROXCEN dir for backwards compat
    if os.path.isdir(PROXCEN_DIR):
        for f in os.listdir(PROXCEN_DIR):
            if f.endswith('.h5'):
                info = _local_file_target_mjd(f)
                if not info:
                    continue
                target = info[0]
                if target not in targets:
                    targets[target] = {'fine': [], 'mid': [], 'filterbank': [], 'h5': []}
                if len(targets[target]['mid']) == 0:
                    targets[target]['mid'].append({
                        'name': f,
                        'size_gb': round(os.path.getsize(os.path.join(PROXCEN_DIR, f)) / 1e6, 1),
                        'path': f'PROXCEN/{f}',
                        'date': mjd_to_date(info[1]),
                    })
    
    return jsonify(targets)


@app.route('/api/blsearch')
def api_blsearch():
    """Proxy search to Berkeley SETI open data API.

    Results are filtered to files whose target token (parsed from the
    filename itself) exactly matches the query: the BL API prefix-matches
    ?target= (HIP2 also returns HIP26, HIP225, ...), which used to pollute
    the dashboard with hundreds of wrong-target files. Pass raw=1 to see
    the unfiltered response."""
    import urllib.request
    import urllib.parse
    from bl_catalog import _exact_target

    target = request.args.get('target', '')
    if not target:
        return jsonify({'error': 'No target specified'}), 400

    api_url = f'https://seti.berkeley.edu/opendata/api/query-files?target={urllib.parse.quote(target)}'

    try:
        req = urllib.request.Request(api_url, headers={'User-Agent': 'BackyardSETI/1.0'})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    if request.args.get('raw', '0') != '1' and isinstance(data, dict):
        files = data.get('data', [])
        want = target.upper()
        exact = [f for f in files
                 if (_exact_target((f.get('url') or '').split('/')[-1]) or '')
                 .upper() == want]
        data['raw_count'] = len(files)
        data['data'] = exact
    return jsonify(data)


# ---------------------------------------------------------------------------
# API: BL Catalog (cached open browse over every BL target)
# ---------------------------------------------------------------------------

@app.route('/api/blcatalog')
def api_blcatalog():
    """Browse the cached BL catalog (populated by the background sweep)."""
    from bl_catalog import ensure_table
    from db import get_db
    ensure_table()
    q = request.args.get('q', '').strip()
    min_epochs = request.args.get('min_epochs', 0, type=int)
    require_onoff = request.args.get('require_onoff', '0') == '1'
    fine_only = request.args.get('fine_only', '0') == '1'
    conn = get_db()
    try:
        total_rows = conn.execute(
            'SELECT COUNT(*) FROM bl_catalog').fetchone()[0]
        sql = ('SELECT * FROM bl_catalog '
               'WHERE n_fine >= ? AND fine_epochs >= ?')
        args = [1 if fine_only else 0, min_epochs]
        if require_onoff:
            sql += ' AND fine_on > 0 AND fine_off > 0'
        if q:
            sql += ' AND UPPER(target) LIKE UPPER(?)'
            args.append(f'%{q}%')
        sql += ' ORDER BY n_fine DESC, fine_epochs DESC, target LIMIT 500'
        rows = [dict(r) for r in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()
    return jsonify({'targets': rows, 'total_rows': total_rows})


@app.route('/api/blcatalog/sweep', methods=['POST'])
def api_blcatalog_sweep():
    """Start/cancel the background BL catalog sweep."""
    from bl_catalog import ensure_table, start_sweep, sweep_state
    ensure_table()
    params = request.json or {}
    action = params.get('action', 'start')
    if action == 'cancel':
        sweep_state['cancel'] = True
        return jsonify({'success': True, 'cancelling': True})
    if action != 'start':
        return jsonify({'error': 'unknown action'}), 400
    started, info = start_sweep(force=bool(params.get('force')),
                                mode=params.get('mode'))
    if not started:
        return jsonify({'error': str(info), 'active': True}), 409
    return jsonify({'started': True, 'queued': info})


@app.route('/api/blcatalog/sweep/status')
def api_blcatalog_sweep_status():
    from bl_catalog import ensure_table, sweep_state
    from db import get_db
    ensure_table()
    conn = get_db()
    try:
        n = conn.execute('SELECT COUNT(*) FROM bl_catalog').fetchone()[0]
        n_fine = conn.execute(
            'SELECT COUNT(*) FROM bl_catalog WHERE n_fine > 0').fetchone()[0]
    finally:
        conn.close()
    return jsonify({**sweep_state, 'catalog_rows': n,
                    'catalog_fine_rows': n_fine})


# ─── API: Scan History ────────────────────────────────────────────────

def _load_scan_meta(scan_dir):
    """Load scan_meta.json from a scan directory."""
    meta_path = os.path.join(scan_dir, 'scan_meta.json')
    if os.path.isfile(meta_path):
        try:
            # Use utf-8-sig to handle BOM that PowerShell may have added
            with open(meta_path, encoding='utf-8-sig') as f:
                return json.load(f)
        except:
            pass
    return None


def _discover_scans():
    """Discover all scan result sets in the results directory.
    
    A scan is any directory under results/ that contains a scan_meta.json file.
    Also includes legacy directories (validation_50mhz) via migration.
    """
    scans = []
    
    if not os.path.isdir(RESULTS_DIR):
        return scans
    
    for entry in os.listdir(RESULTS_DIR):
        full_path = os.path.join(RESULTS_DIR, entry)
        if not os.path.isdir(full_path):
            continue
        
        meta = _load_scan_meta(full_path)
        if meta:
            scans.append(meta)
    
    # Sort by timestamp descending (newest first)
    scans.sort(key=lambda s: s.get('timestamp', ''), reverse=True)
    return scans


def _get_scan_dir(scan_id):
    """Resolve a scan_id to its directory path."""
    # Prevent path traversal
    if not re.match(r'^[A-Za-z0-9_-]+$', scan_id):
        return None
    scan_dir = os.path.join(RESULTS_DIR, scan_id)
    if os.path.isdir(scan_dir):
        return scan_dir
    return None


@app.route('/api/scans')
def api_scans_list():
    """List all scan result sets, newest first."""
    scans = _discover_scans()
    # Add hit counts by quickly scanning for _hits.json files
    for scan in scans:
        scan_dir = _get_scan_dir(scan['scan_id'])
        if scan_dir:
            # If meta already has total_hits, trust it
            if not scan.get('stats', {}).get('total_hits'):
                hit_files = glob_module.glob(
                    os.path.join(scan_dir, '**/*_hits.json'), recursive=True)
                on_count = 0
                off_count = 0
                total = 0
                for hf in hit_files:
                    fname = os.path.basename(hf)
                    is_on = _file_is_on(fname, scan.get('target', ''))
                    try:
                        with open(hf) as f:
                            data = json.load(f)
                        n = len(data.get('hits', []))
                        total += n
                        if is_on:
                            on_count += n
                        else:
                            off_count += n
                    except:
                        pass
                if 'stats' not in scan:
                    scan['stats'] = {}
                scan['stats']['total_hits'] = total
                scan['stats']['on_hits'] = on_count
                scan['stats']['off_hits'] = off_count
    return jsonify(scans)


@app.route('/api/scans/<scan_id>/results')
def api_scan_results(scan_id):
    """Get all hits for a specific scan."""
    scan_dir = _get_scan_dir(scan_id)
    if not scan_dir:
        return jsonify({'error': f'Scan not found: {scan_id}'}), 404
    
    results = []
    
    for fpath in glob_module.glob(
        os.path.join(scan_dir, '**/*_hits.json'), recursive=True):
        try:
            with open(fpath) as fh:
                data = json.load(fh)
            if isinstance(data, dict) and 'hits' in data:
                fname = os.path.basename(fpath)
                results.append({
                    'type': 'file',
                    'name': fname,
                    'data': data,
                })
        except:
            pass
    
    # Also check for summary files
    for fpath in glob_module.glob(os.path.join(scan_dir, '*_summary.json')):
        try:
            with open(fpath) as fh:
                data = json.load(fh)
            if isinstance(data, dict) and 'files' in data:
                results.append({
                    'type': 'summary',
                    'name': os.path.basename(fpath),
                    'data': data,
                })
        except:
            pass
    
    # Include scan metadata
    meta = _load_scan_meta(scan_dir) or {}
    _scan_target = str(meta.get('target', '') or '')
    
    # Include rejection results if they exist
    reject_path = os.path.join(scan_dir, 'rejection', 'rejection_results.json')
    rejection = None
    if os.path.isfile(reject_path):
        try:
            with open(reject_path) as f:
                rejection = json.load(f)
        except:
            pass
    
    return jsonify({
        'scan_id': scan_id,
        'meta': meta,
        'results': results,
        'rejection': rejection,
    })


def _epoch_label_from_files(files):
    """Extract the observation epoch (BL MJD, 5-digit str) from a scan's
    fine file list. Two grammars:
    Parkes: Telescope_MJD_Seq_TARGET_S/R_res.h5  -> parts[1]
    GBT:    (spliced_)blcNN_guppi_MJD_SEQ_TARGET_SCAN.PROD.TIER.h5
            -> the token right after 'guppi'
    """
    for f in files or []:
        fname = os.path.basename(str(f))
        parts = fname.replace('.h5', '').split('_')
        if len(parts) >= 5 and parts[1].isdigit() and len(parts[1]) == 5:
            return parts[1]
        # GBT: find the guppi marker, MJD follows it
        if 'guppi_' in fname:
            toks = fname.split('guppi_', 1)[1].split('_')
            if toks and toks[0].isdigit() and len(toks[0]) == 5:
                return toks[0]
    return None


@app.route('/api/scans/create', methods=['POST'])
def api_scans_create():
    """Create a new scan result set. Returns scan_id."""
    params = request.json or {}
    target = params.get('target', 'PROXCEN').upper()
    
    # Generate scan_id: TARGET_EPOCH_YYYY-MM-DD_HHMM (epoch = BL MJD, e.g.
    # PROXCEN_57791_2026-08-14_2149). Falls back to legacy TARGET_DATE_TIME
    # when no fine file list is available.
    now = datetime.now()
    date_time = now.strftime('%Y-%m-%d_%H%M')
    epoch = _epoch_label_from_files(params.get('files', []))
    if epoch:
        scan_id = f"{target}_{epoch}_{date_time}"
    else:
        scan_id = f"{target}_{date_time}"
    
    # Ensure uniqueness
    scan_dir = os.path.join(RESULTS_DIR, scan_id)
    counter = 1
    while os.path.isdir(scan_dir):
        suffix = f"_{counter}"
        scan_id = (f"{target}_{epoch}_{date_time}{suffix}" if epoch
                   else f"{target}_{date_time}{suffix}")
        scan_dir = os.path.join(RESULTS_DIR, scan_id)
        counter += 1
    
    os.makedirs(scan_dir)
    
    meta = {
        'scan_id': scan_id,
        'target': target,
        'mjd_start': float(epoch) if epoch else None,
        'timestamp': now.isoformat(timespec='seconds'),
        'status': 'running',
        'parameters': {
            'sub_band_chans': params.get('sub_band_chans', 262144),
            'overlap': params.get('overlap', 512),
            'max_drift': params.get('max_drift', 5.0),
            'snr': params.get('snr', 5.0),
            'f_start': params.get('f_start', None),
            'f_stop': params.get('f_stop', None),
            'files': params.get('files', []),
        },
        'stats': {},
    }
    
    meta_path = os.path.join(scan_dir, 'scan_meta.json')
    with open(meta_path, 'w') as f:
        json.dump(meta, f, indent=2)
    
    return jsonify({'scan_id': scan_id, 'scan_dir': scan_dir})


@app.route('/api/scans/<scan_id>/complete', methods=['POST'])
def api_scans_complete(scan_id):
    """Mark a scan as complete and update its metadata."""
    scan_dir = _get_scan_dir(scan_id)
    if not scan_dir:
        return jsonify({'error': f'Scan not found: {scan_id}'}), 404
    
    meta_path = os.path.join(scan_dir, 'scan_meta.json')
    meta = _load_scan_meta(scan_dir) or {}
    
    # Calculate stats from result files
    on_hits = 0
    off_hits = 0
    total_hits = 0
    
    for fpath in glob_module.glob(
        os.path.join(scan_dir, '**/*_hits.json'), recursive=True):
        try:
            with open(fpath) as f:
                data = json.load(f)
            hits = data.get('hits', [])
            fname = os.path.basename(fpath)
            is_on = _file_is_on(fname, _scan_target)
            count = len(hits)
            total_hits += count
            if is_on:
                on_hits += count
            else:
                off_hits += count
        except:
            pass
    
    # Update with any provided stats
    params = request.json or {}
    duration = params.get('duration_s', 0)
    
    meta['status'] = 'complete'
    meta['stats'] = {
        'total_hits': total_hits,
        'on_hits': on_hits,
        'off_hits': off_hits,
        'duration_s': duration,
    }
    
    with open(meta_path, 'w') as f:
        json.dump(meta, f, indent=2)
    
    return jsonify(meta)


# ─── API: Scan Results (scan-aware) ───────────────────────────────────

@app.route('/api/results')
def api_results():
    """Return hit results from pipeline output.
    
    Query params:
      - scan_id: If provided, load results from that scan's directory only.
      - If not provided, load from all known result directories (legacy behavior).
    """
    scan_id = request.args.get('scan_id', None)
    
    if scan_id:
        scan_dir = _get_scan_dir(scan_id)
        if not scan_dir:
            return jsonify({'error': f'Scan not found: {scan_id}'}), 404
        
        results = []
        for fpath in glob_module.glob(
            os.path.join(scan_dir, '**/*_hits.json'), recursive=True):
            try:
                with open(fpath) as fh:
                    data = json.load(fh)
                if isinstance(data, dict) and 'hits' in data:
                    results.append({
                        'type': 'file',
                        'name': os.path.basename(fpath),
                        'data': data,
                    })
            except:
                pass
        
        # Also check for summary files
        for fpath in glob_module.glob(os.path.join(scan_dir, '*_summary.json')):
            try:
                with open(fpath) as fh:
                    data = json.load(fh)
                if isinstance(data, dict) and 'files' in data:
                    results.append({
                        'type': 'summary',
                        'name': os.path.basename(fpath),
                        'data': data,
                    })
            except:
                pass
        
        return jsonify(results)
    
    # Legacy: load from all known result directories
    results = []
    
    # Scan validation results
    val_dir = os.path.join(RESULTS_DIR, 'validation_50mhz')
    if os.path.isdir(val_dir):
        for f in os.listdir(val_dir):
            if f.endswith('.json') and f != 'scan_meta.json':
                with open(os.path.join(val_dir, f)) as fh:
                    data = json.load(fh)
                    if isinstance(data, dict) and 'files' in data:
                        results.append({
                            'type': 'summary',
                            'name': f,
                            'data': data,
                        })
                    elif isinstance(data, dict) and 'hits' in data:
                        results.append({
                            'type': 'file',
                            'name': f,
                            'data': data,
                        })
    
    # Scan pipeline results
    pipe_dir = os.path.join(RESULTS_DIR, 'fine_pipeline')
    if os.path.isdir(pipe_dir):
        for target_dir in os.listdir(pipe_dir):
            target_path = os.path.join(pipe_dir, target_dir)
            if os.path.isdir(target_path):
                for f in os.listdir(target_path):
                    if f.endswith('_hits.json'):
                        with open(os.path.join(target_path, f)) as fh:
                            data = json.load(fh)
                            results.append({
                                'type': 'pipeline',
                                'name': f,
                                'data': data,
                            })
    
    return jsonify(results)


@app.route('/api/results/<path:subpath>')
def api_result_file(subpath):
    """Serve a specific result file."""
    full_path = os.path.join(RESULTS_DIR, subpath)
    if os.path.isfile(full_path) and subpath.endswith('.json'):
        with open(full_path) as f:
            return jsonify(json.load(f))
    return jsonify({'error': 'Not found'}), 404


# ─── API: File Header Info ────────────────────────────────────────────

@app.route('/api/header')
def api_header():
    """Get HDF5 header info for a file."""
    filepath = request.args.get('file', '')
    if not filepath:
        return jsonify({'error': 'No file specified'}), 400
    
    full_path = _resolve_data_file(filepath)
    
    if not full_path:
        return jsonify({'error': f'File not found: {filepath}'}), 404
    
    try:
        from blimpy import Waterfall
        wf = Waterfall(full_path, load_data=False)
        header = {}
        for k, v in wf.header.items():
            try:
                if hasattr(v, 'value'):
                    v = v.value
                if hasattr(v, 'item'):
                    v = v.item()
                header[k] = float(v) if isinstance(v, (int, float, np.integer, np.floating)) else str(v)
            except:
                header[k] = str(v)
        return jsonify({
            'header': header,
            'n_ints': wf.n_ints_in_file,
            'data_shape': list(wf.data.shape),
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ─── API: Scan Status ─────────────────────────────────────────────────

def _check_process_alive(pid):
    """Check if a process with given PID is alive (Windows)."""
    if not pid:
        return False
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        kernel32.CloseHandle(handle)
        return True
    except Exception:
        return False


def _recover_zombie_scan_state():
    """If scan_state says active but the process is dead, recover."""
    if not scan_state['active']:
        return False
    pid = scan_state.get('pid')
    if not pid or not _check_process_alive(pid):
        ts = datetime.now().strftime('%H:%M:%S')
        scan_state['log_lines'].append(
            f'[{ts}] WARN: Process PID {pid} is dead but scan_state was active. Auto-recovering.')
        scan_state['active'] = False
        scan_state['pid'] = None
        scan_state['active_scan_id'] = None
        return True
    return False


@app.route('/api/scan/status')
def api_scan_status():
    """Get current scan status with structured progress data."""
    # ── Zombie scan_state recovery ──
    _recover_zombie_scan_state()
    # Load scan_meta for additional context
    scan_meta = {}
    if scan_state.get('active_scan_id'):
        meta_dir = os.path.join(RESULTS_DIR, scan_state['active_scan_id'])
        scan_meta = _load_scan_meta(meta_dir) or {}
    
    # Check if the currently selected scan (from dropdown) has a checkpoint
    can_resume = False
    resume_scan_id = None
    if not scan_state['active']:
        # The frontend passes the selected scan_id as a query param
        selected = request.args.get('scan_id', '')
        if selected:
            cp_path = os.path.join(RESULTS_DIR, selected, 'checkpoint.json')
            if os.path.isfile(cp_path):
                can_resume = True
                resume_scan_id = selected

    return jsonify({
        'active': scan_state['active'],
        'scan_id': scan_state.get('active_scan_id'),
        'can_resume': can_resume,
        'resume_scan_id': resume_scan_id,
        'progress': scan_state['progress'],
        'log_tail': scan_state['log_lines'][-500:],
        # Structured progress
        'sub_bands_done': scan_state.get('sub_bands_done', 0),
        'sub_bands_total': scan_state.get('sub_bands_total', 0),
        'current_sub_band': scan_state.get('current_sub_band', 0),
        'current_freq_start': scan_state.get('current_freq_start', 0),
        'current_freq_stop': scan_state.get('current_freq_stop', 0),
        'current_freq': scan_state.get('current_freq', 0),
        'total_hits': scan_state.get('total_hits', 0),
        'subband_hits': scan_state.get('subband_hits', []),
        'recent_hits': scan_state.get('recent_hits', []),
        'target': scan_state.get('target', ''),
        'freq_start': scan_state.get('freq_start', 0),
        'freq_end': scan_state.get('freq_end', 0),
        # Fix 2/3/8: file tracking, ON/OFF hits, per-file hits
        'current_file': scan_state.get('current_file', ''),
        'current_file_index': scan_state.get('current_file_index', 0),
        'file_total': scan_state.get('file_total', 0),
        'on_hits': scan_state.get('on_hits', 0),
        'off_hits': scan_state.get('off_hits', 0),
        'file_hits': scan_state.get('file_hits', 0),
        # Scan meta from file
        'scan_meta': scan_meta,
    })


@app.route('/api/scan/spectrum')
def api_scan_spectrum():
    """Return real spectra from the last processed subband.
    
    The pipeline writes last_spectra.npz to the scan directory
    after extracting each subband. This endpoint reads the most
    recent one available.
    """
    scan_dir = scan_state.get('scan_dir', '')
    if not scan_dir:
        # Try to infer from active scan_id
        sid = scan_state.get('active_scan_id')
        if sid:
            scan_dir = os.path.join(RESULTS_DIR, sid)
    if not scan_dir:
        return jsonify({'error': 'No scan directory', 'spectra': []})
    
    npz_path = os.path.join(scan_dir, 'last_spectra.npz')
    if not os.path.isfile(npz_path):
        return jsonify({'error': 'No spectra snapshot available yet', 'spectra': []})
    
    try:
        data = np.load(npz_path, allow_pickle=True)
        spectra = data['spectra']  # shape: (n_times, n_freqs) already dB
        f_start = float(data['f_start'])
        f_stop = float(data['f_stop'])
        n_freqs = spectra.shape[-1]
        freqs = [f_start + (f_stop - f_start) * i / max(n_freqs - 1, 1) for i in range(n_freqs)]
        
        return jsonify({
            'spectra': spectra.tolist(),
            'freqs': freqs,
            'n_times': int(spectra.shape[0]),
            'n_freqs': int(spectra.shape[-1]),
            'f_start': f_start,
            'f_stop': f_stop,
            'subband_index': int(data['subband_index']) if 'subband_index' in data else 0,
        })
    except Exception as e:
        return jsonify({'error': str(e), 'spectra': []})


# ─── API: Scan Control ────────────────────────────────────────────────

@app.route('/api/scan/start', methods=['POST'])
def api_scan_start():
    """Start a pipeline scan. Runs in background thread."""
    if scan_state['active']:
        return jsonify({'error': 'Scan already running'}), 409
    
    params = request.json or {}
    target = params.get('target', 'PROXCEN')
    resolution = params.get('resolution', 'fine')
    sub_band_chans = params.get('sub_band_chans', 262144)
    overlap = params.get('overlap', 512)
    max_drift = params.get('max_drift', 5.0)
    snr = params.get('snr', 5.0)
    f_start = params.get('f_start', None)
    f_stop = params.get('f_stop', None)
    files_list = params.get('files', None)  # Optional: list of specific file paths
    
    # Create a scan result set first
    target = params.get('target', 'PROXCEN')
    now = datetime.now()
    date_time = now.strftime('%Y-%m-%d_%H%M')
    epoch = _epoch_label_from_files(files_list)
    if epoch:
        scan_id = f"{target.upper()}_{epoch}_{date_time}"
    else:
        scan_id = f"{target.upper()}_{date_time}"
    scan_dir = os.path.join(RESULTS_DIR, scan_id)
    # Ensure uniqueness
    counter = 1
    while os.path.isdir(scan_dir):
        suffix = f"_{counter}"
        scan_id = (f"{target.upper()}_{epoch}_{date_time}{suffix}" if epoch
                   else f"{target.upper()}_{date_time}{suffix}")
        scan_dir = os.path.join(RESULTS_DIR, scan_id)
        counter += 1
    os.makedirs(scan_dir)
    
    # Write scan_meta.json
    scan_meta = {
        'scan_id': scan_id,
        'target': target.upper(),
        'mjd_start': float(epoch) if epoch else None,
        'timestamp': now.isoformat(timespec='seconds'),
        'status': 'running',
        'parameters': {
            'sub_band_chans': sub_band_chans,
            'overlap': overlap,
            'max_drift': max_drift,
            'snr': snr,
            'f_start': f_start,
            'f_stop': f_stop,
            'files': files_list if files_list else [],
        },
        'stats': {},
    }
    with open(os.path.join(scan_dir, 'scan_meta.json'), 'w') as f:
        json.dump(scan_meta, f, indent=2)
    
    # Populate scan_state with meta fields
    scan_state['target'] = target.upper()
    scan_state['freq_start'] = f_start if f_start else 0
    scan_state['freq_end'] = f_stop if f_stop else 0
    scan_state['scan_dir'] = scan_dir
    
    # Build command - output to the scan directory
    py = sys.executable  # portability: interpreter running the dashboard
    script = os.path.join(SETI_ROOT, 'src', 'fine_res_pipeline.py')
    
    cmd = [py, script, '--out', scan_dir]
    
    if files_list and len(files_list) > 0:
        # Scan specific files instead of entire data-dir
        for fpath in files_list:
            resolved = _resolve_data_file(fpath)
            if resolved:
                cmd.extend(['--file', resolved])
    elif resolution == 'fine':
        cmd.extend(['--data-dir', FINE_DIR])
    else:
        cmd.extend(['--data-dir', MID_DIR])
    
    cmd.extend(['--sub-band-width', str(sub_band_chans)])
    cmd.extend(['--overlap', str(overlap)])
    cmd.extend(['--max-drift', str(max_drift)])
    cmd.extend(['--snr', str(snr)])
    
    def run_scan():
        scan_state['active'] = True
        scan_state['log_lines'] = []
        scan_state['active_scan_id'] = scan_id
        scan_state['scan_start_time'] = time.time()
        # Reset structured progress fields
        scan_state['sub_bands_done'] = 0
        scan_state['sub_bands_total'] = 0
        scan_state['current_sub_band'] = 0
        scan_state['current_freq_start'] = 0
        scan_state['current_freq_stop'] = 0
        scan_state['current_freq'] = 0
        scan_state['total_hits'] = 0
        scan_state['subband_hits'] = []
        scan_state['recent_hits'] = []
        scan_state['target'] = target.upper()
        scan_state['freq_start'] = f_start if f_start else 0
        scan_state['freq_end'] = f_stop if f_stop else 0
        scan_state['scan_dir'] = scan_dir
        # Fix 2/3/6/8: reset file tracking and ON/OFF counters
        scan_state['current_file'] = ''
        scan_state['current_file_index'] = 0
        scan_state['file_total'] = 0
        scan_state['on_hits'] = 0
        scan_state['off_hits'] = 0
        scan_state['file_hits'] = 0
        scan_state['processing_file_count'] = 0  # track for file_total
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                cwd=SETI_ROOT, text=True, bufsize=1,
            )
            scan_state['pid'] = proc.pid
            for line in proc.stdout:
                line_stripped = line.rstrip()
                scan_state['log_lines'].append(line_stripped)
                scan_state['progress']['last_line'] = line_stripped
                
                # Parse resume line: 'Resuming from file 4/6, sub-band 0'
                resume_match = re.search(r'Resuming from file (\d+)/(\d+)', line_stripped)
                if resume_match:
                    scan_state['processing_file_count'] = int(resume_match.group(1)) - 1
                    scan_state['file_total'] = int(resume_match.group(2))
                # Parse SKIP lines to count skipped files
                skip_match = re.match(r'\s*SKIP \(complete\):\s*(.+\.h5)', line_stripped)
                if skip_match:
                    scan_state['processing_file_count'] += 1
                # Fix 2: Parse current filename from 'Processing: filename.h5'
                proc_match = re.match(r'Processing:\s*(.+\.h5)', line_stripped)
                if proc_match:
                    new_file = proc_match.group(1).strip()
                    # Fix 6: On file transition, reset sub-band progress
                    if scan_state['current_file'] and scan_state['current_file'] != new_file:
                        scan_state['sub_bands_done'] = 0
                        scan_state['sub_bands_total'] = 0
                        scan_state['subband_hits'] = []
                        scan_state['file_hits'] = 0  # Fix 8: reset per-file hits
                    scan_state['current_file'] = new_file
                    scan_state['processing_file_count'] += 1
                    scan_state['current_file_index'] = scan_state['processing_file_count']
                    # Track total file count from 'Files: N' or infer from checkpoint
                    # The pipeline prints file count early on
                    files_total_match = re.search(r'Files:\s*(\d+)', line_stripped)
                    if files_total_match:
                        scan_state['file_total'] = int(files_total_match.group(1))
                # Also parse 'Files: N' from pipeline header
                files_match = re.search(r'Files:\s*(\d+)', line_stripped)
                if files_match:
                    scan_state['file_total'] = int(files_match.group(1))
                
                # Parse subband progress: [X/Y] FFFF.SSSS-FFFF.SSSS MHz
                sub_match = re.match(r'\s*\[(\d+)/(\d+)\]\s+([\d.]+)-([\d.]+)\s+MHz', line_stripped)
                if sub_match:
                    scan_state['sub_bands_done'] = int(sub_match.group(1))
                    scan_state['sub_bands_total'] = int(sub_match.group(2))
                    scan_state['current_sub_band'] = int(sub_match.group(1)) - 1
                    scan_state['current_freq_start'] = float(sub_match.group(3))
                    scan_state['current_freq_stop'] = float(sub_match.group(4))
                    scan_state['current_freq'] = (scan_state['current_freq_start'] + scan_state['current_freq_stop']) / 2
                
                # Parse hit count: -> N hits
                hit_match = re.match(r'\s*->\s*(\d+)\s*hits', line_stripped)
                if hit_match:
                    n_hits = int(hit_match.group(1))
                    # Fix 8: total_hits accumulates across ALL files (never reset on file transition)
                    scan_state['total_hits'] += n_hits
                    scan_state['file_hits'] += n_hits  # per-file hits
                    scan_state['subband_hits'].append(n_hits)
                    # Fix 3: Classify as ON or OFF based on filename
                    cur_file = scan_state.get('current_file', '')
                    if _file_is_on(cur_file, scan_state.get('target', '')):
                        scan_state['on_hits'] += n_hits
                    else:
                        scan_state['off_hits'] += n_hits
                
                # Parse top hit: find_doppler.N INFO     Top hit found! SNR X, Drift Rate Y, index Z
                top_match = re.search(r'Top hit found!.*SNR\s+([\d.]+).*Drift Rate\s+([-\d.]+).*index\s+(\d+)', line_stripped)
                if top_match:
                    hit = {
                        'snr': float(top_match.group(1)),
                        'drift_rate': float(top_match.group(2)),
                        'index': int(top_match.group(3)),
                        'coarse_chan': None,
                    }
                    cc_match = re.search(r'find_doppler\.(\d+)', line_stripped)
                    if cc_match:
                        hit['coarse_chan'] = int(cc_match.group(1))
                    scan_state['recent_hits'].append(hit)
                    if len(scan_state['recent_hits']) > 50:
                        scan_state['recent_hits'] = scan_state['recent_hits'][-50:]
            proc.wait()
        except Exception as e:
            scan_state['log_lines'].append(f'ERROR: {e}')
        finally:
            scan_state['active'] = False
            scan_state['pid'] = None
            # Complete the scan: update scan_meta.json
            elapsed = time.time() - scan_state.get('scan_start_time', time.time())
            try:
                _complete_scan_meta(scan_id, elapsed)
            except Exception as e:
                scan_state['log_lines'].append(f'WARN: Could not update scan_meta: {e}')
            # Auto-import completed scan into SQLite DB
            try:
                from db import import_scan_from_json
                _import_dir = os.path.join(RESULTS_DIR, scan_id)
                import_stats = import_scan_from_json(_import_dir)
                scan_state['log_lines'].append(
                    f'DB import: {import_stats.get("hits_imported", 0)} hits imported')
            except Exception as e:
                scan_state['log_lines'].append(f'WARN: DB import failed: {e}')
            scan_state['active_scan_id'] = None
    
    thread = threading.Thread(target=run_scan, daemon=True)
    thread.start()
    
    return jsonify({'status': 'started', 'scan_id': scan_id, 'pid': scan_state.get('pid')})


@app.route('/api/scan/stop', methods=['POST'])
def api_scan_stop():
    """Stop a running scan."""
    _recover_zombie_scan_state()
    if scan_state['pid']:
        try:
            import signal
            os.kill(scan_state['pid'], signal.SIGTERM)
            scan_state['active'] = False
            return jsonify({'status': 'stopped'})
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    return jsonify({'status': 'not running'})


@app.route('/api/scan/resume', methods=['POST'])
def api_scan_resume():
    """Resume the most recent (or specified) scan from checkpoint."""
    # Check for zombie state first
    _recover_zombie_scan_state()
    
    if scan_state['active']:
        params = request.json or {}
        if params.get('force'):
            ts = datetime.now().strftime('%H:%M:%S')
            scan_state['log_lines'].append(f'[{ts}] WARN: Force-resuming despite active=True')
            scan_state['active'] = False
            scan_state['pid'] = None
        else:
            return jsonify({'error': 'Scan already running, stop it first'}), 409

    params = request.json or {}
    scan_id = params.get('scan_id')

    # Find the scan to resume
    if not scan_id:
        # Find the most recent scan that has a checkpoint.
        # _discover_scans() returns metadata dicts sorted newest-first;
        # extract the scan_id string (passing the dict to _get_scan_dir
        # crashes its regex -> the historical 500 on body-less resume).
        for scan_meta_cp in _discover_scans():
            sid = scan_meta_cp.get('scan_id', '') if isinstance(scan_meta_cp, dict) else scan_meta_cp
            if not sid:
                continue
            cp_path = os.path.join(_get_scan_dir(sid), 'checkpoint.json')
            if os.path.isfile(cp_path):
                scan_id = sid
                break
        if not scan_id:
            return jsonify({'error': 'No scan with a checkpoint found to resume'}), 404

    scan_dir = _get_scan_dir(scan_id)
    if not scan_dir or not os.path.isdir(scan_dir):
        return jsonify({'error': f'Scan directory not found: {scan_id}'}), 404

    cp_path = os.path.join(scan_dir, 'checkpoint.json')
    if not os.path.isfile(cp_path):
        return jsonify({'error': 'No checkpoint found in scan directory'}), 404

    # Load checkpoint to report what we're resuming
    with open(cp_path) as f:
        checkpoint = json.load(f)

    # Load scan_meta to get original parameters
    scan_meta = _load_scan_meta(scan_dir) or {}
    orig_params = scan_meta.get('parameters', {})

    files_list = orig_params.get('files', [])
    sub_band_chans = orig_params.get('sub_band_chans', 262144)
    overlap = orig_params.get('overlap', 512)
    max_drift = orig_params.get('max_drift', 5.0)
    snr = orig_params.get('snr', 5.0)

    # Build command with --resume
    py = sys.executable  # portability: interpreter running the dashboard
    script = os.path.join(SETI_ROOT, 'src', 'fine_res_pipeline.py')

    cmd = [py, script, '--out', scan_dir, '--resume']

    if files_list:
        for fpath in files_list:
            resolved = _resolve_data_file(fpath)
            if resolved:
                cmd.extend(['--file', resolved])
    else:
        cmd.extend(['--data-dir', FINE_DIR])

    cmd.extend(['--sub-band-width', str(sub_band_chans)])
    cmd.extend(['--overlap', str(overlap)])
    cmd.extend(['--max-drift', str(max_drift)])
    cmd.extend(['--snr', str(snr)])

    target = scan_meta.get('target', 'UNKNOWN')

    def run_scan():
        scan_state['active'] = True
        scan_state['log_lines'] = []
        scan_state['active_scan_id'] = scan_id
        scan_state['scan_start_time'] = time.time()
        scan_state['sub_bands_done'] = 0
        scan_state['sub_bands_total'] = 0
        scan_state['current_sub_band'] = 0
        scan_state['current_freq_start'] = 0
        scan_state['current_freq_stop'] = 0
        scan_state['current_freq'] = 0
        scan_state['total_hits'] = 0
        scan_state['subband_hits'] = []
        scan_state['recent_hits'] = []
        scan_state['target'] = target
        scan_state['freq_start'] = orig_params.get('f_start', 0) or 0
        scan_state['freq_end'] = orig_params.get('f_stop', 0) or 0
        scan_state['scan_dir'] = scan_dir
        # Fix 2/3/6/8: reset file tracking and ON/OFF counters
        scan_state['current_file'] = ''
        scan_state['current_file_index'] = 0
        scan_state['file_total'] = 0
        scan_state['on_hits'] = 0
        scan_state['off_hits'] = 0
        scan_state['file_hits'] = 0
        scan_state['processing_file_count'] = 0
        _log_ts = lambda: datetime.now().strftime('%H:%M:%S')
        scan_state['log_lines'].append(f'[{_log_ts()}] RESUME: Starting pipeline...')
        scan_state['log_lines'].append(f'[{_log_ts()}] RESUME: cmd={" ".join(cmd)}')
        scan_state['log_lines'].append(f'[{_log_ts()}] RESUME: cwd={SETI_ROOT}')
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                cwd=SETI_ROOT, text=True, bufsize=1,
            )
            scan_state['pid'] = proc.pid
            scan_state['log_lines'].append(f'[{_log_ts()}] RESUME: Process started PID={proc.pid}')
            # Use readline with liveness check instead of blocking iterator
            # This prevents the thread from hanging forever if the process
            # dies but a child process (e.g. turboSETI workers) holds the pipe open
            _stall_counter = 0
            while True:
                line = proc.stdout.readline()
                if line:
                    _stall_counter = 0
                    line_stripped = line.rstrip()
                    scan_state['log_lines'].append(line_stripped)
                    scan_state['progress']['last_line'] = line_stripped

                    # Parse resume line: 'Resuming from file 4/6, sub-band 0'
                    resume_match = re.search(r'Resuming from file (\d+)/(\d+)', line_stripped)
                    if resume_match:
                        scan_state['processing_file_count'] = int(resume_match.group(1)) - 1
                        scan_state['file_total'] = int(resume_match.group(2))
                    # Parse SKIP lines to count skipped files
                    skip_match = re.match(r'\s*SKIP \(complete\):\s*(.+\.h5)', line_stripped)
                    if skip_match:
                        scan_state['processing_file_count'] += 1

                    # Fix 2: Parse current filename
                    proc_match = re.match(r'Processing:\s*(.+\.h5)', line_stripped)
                    if proc_match:
                        new_file = proc_match.group(1).strip()
                        # Fix 6: On file transition, reset sub-band progress
                        if scan_state['current_file'] and scan_state['current_file'] != new_file:
                            scan_state['sub_bands_done'] = 0
                            scan_state['sub_bands_total'] = 0
                            scan_state['subband_hits'] = []
                            scan_state['file_hits'] = 0
                        scan_state['current_file'] = new_file
                        scan_state['processing_file_count'] += 1
                        scan_state['current_file_index'] = scan_state['processing_file_count']
                    files_match = re.search(r'Files:\s*(\d+)', line_stripped)
                    if files_match:
                        scan_state['file_total'] = int(files_match.group(1))

                    sub_match = re.match(r'\s*\[(\d+)/(\d+)\]\s+([\d.]+)-([\d.]+)\s+MHz', line_stripped)
                    if sub_match:
                        scan_state['sub_bands_done'] = int(sub_match.group(1))
                        scan_state['sub_bands_total'] = int(sub_match.group(2))
                        scan_state['current_sub_band'] = int(sub_match.group(1)) - 1
                        scan_state['current_freq_start'] = float(sub_match.group(3))
                        scan_state['current_freq_stop'] = float(sub_match.group(4))
                        scan_state['current_freq'] = (scan_state['current_freq_start'] + scan_state['current_freq_stop']) / 2

                    hit_match = re.match(r'\s*->\s*(\d+)\s*hits', line_stripped)
                    if hit_match:
                        n_hits = int(hit_match.group(1))
                        scan_state['total_hits'] += n_hits
                        scan_state['file_hits'] += n_hits
                        scan_state['subband_hits'].append(n_hits)
                        # Fix 3: ON/OFF classification
                        cur_file = scan_state.get('current_file', '')
                        if _file_is_on(cur_file, scan_state.get('target', '')):
                            scan_state['on_hits'] += n_hits
                        else:
                            scan_state['off_hits'] += n_hits

                    top_match = re.search(r'Top hit found!.*SNR\s+([\d.]+).*Drift Rate\s+([-\d.]+).*index\s+(\d+)', line_stripped)
                    if top_match:
                        hit = {
                            'snr': float(top_match.group(1)),
                            'drift_rate': float(top_match.group(2)),
                            'index': int(top_match.group(3)),
                            'coarse_chan': None,
                        }
                        cc_match = re.search(r'find_doppler\.(\d+)', line_stripped)
                        if cc_match:
                            hit['coarse_chan'] = int(cc_match.group(1))
                        scan_state['recent_hits'].append(hit)
                        if len(scan_state['recent_hits']) > 50:
                            scan_state['recent_hits'] = scan_state['recent_hits'][-50:]
                else:
                    # No data on readline — check if process is done
                    rc = proc.poll()
                    if rc is not None:
                        scan_state['log_lines'].append(
                            f'[{_log_ts()}] RESUME: Process exited with code {rc}')
                        break
                    # Process still alive but no output — wait a bit
                    _stall_counter += 1
                    time.sleep(0.2)
                    # If we've been stalled for >60s with no output, log it
                    if _stall_counter % 300 == 0:  # every 60s
                        scan_state['log_lines'].append(
                            f'[{_log_ts()}] RESUME: Process PID={proc.pid} alive but no output for {_stall_counter * 0.2:.0f}s')
            proc.wait()
        except Exception as e:
            scan_state['log_lines'].append(f'ERROR: {e}')
        finally:
            scan_state['active'] = False
            scan_state['pid'] = None
            elapsed = time.time() - scan_state.get('scan_start_time', time.time())
            try:
                _complete_scan_meta(scan_id, elapsed)
            except Exception as e:
                scan_state['log_lines'].append(f'WARN: Could not update scan_meta: {e}')
            # Auto-import completed scan into SQLite DB
            try:
                from db import import_scan_from_json
                _import_dir = os.path.join(RESULTS_DIR, scan_id)
                import_stats = import_scan_from_json(_import_dir)
                scan_state['log_lines'].append(
                    f'DB import: {import_stats.get("hits_imported", 0)} hits imported')
            except Exception as e:
                scan_state['log_lines'].append(f'WARN: DB import failed: {e}')
            scan_state['active_scan_id'] = None

    thread = threading.Thread(target=run_scan, daemon=True)
    thread.start()

    return jsonify({
        'status': 'resumed',
        'scan_id': scan_id,
        'checkpoint': checkpoint,
    })


# ─── API: Delete File ─────────────────────────────────────────────────

@app.route('/api/delete', methods=['POST'])
def api_delete():
    """Delete a local data file."""
    params = request.json or {}
    filepath = params.get('path', '')
    if not filepath:
        return jsonify({'error': 'No path specified'}), 400
    
    # Resolve path safely (check primary + secondary data dirs)
    full_path = _resolve_data_file(filepath)
    if full_path:
        # Safety: make sure resolved path is under a known data directory
        real_path = os.path.realpath(full_path)
        safe_roots = [os.path.realpath(DATA_DIR)] + [os.path.realpath(d) for d in DATA_DIRS_SECONDARY]
        if not any(real_path.startswith(r) for r in safe_roots):
            full_path = None
    
    if not full_path:
        return jsonify({'error': 'File not found or outside data directory'}), 404
    
    try:
        import shutil
        # Move to trash instead of permanent delete
        trash_dir = os.path.join(DATA_DIR, '.trash')
        os.makedirs(trash_dir, exist_ok=True)
        filename = os.path.basename(full_path)
        trash_path = os.path.join(trash_dir, filename)
        # Handle name collisions in trash
        if os.path.exists(trash_path):
            import time as _t
            trash_path = os.path.join(trash_dir, filename + '.' + str(int(_t.time())))
        shutil.move(full_path, trash_path)
        return jsonify({'status': 'deleted', 'filename': filename})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ─── API: File Download ───────────────────────────────────────────────

# ---------------------------------------------------------------------------
# API: Epoch Archive (3B) - move staged h5 files to the D: per-target archive
# ---------------------------------------------------------------------------

archive_state = {'active': False, 'scan_id': '', 'stage': '',
                 'files_done': 0, 'files_total': 0, 'bytes_copied': 0,
                 'error': None, 'last': None}


@app.route('/api/archive/epoch', methods=['POST'])
def api_archive_epoch():
    """Move a completed scan's h5 files to the per-target archive on D:
    (D:\\seti_data\\{TARGET}\\fine), freeing G: SSD staging space.

    Copies each file to '<dst>.archiving', verifies byte size, renames
    atomically on D:, and only then removes the staging copy. A file
    already present in the archive at the same size just frees staging.
    """
    params = request.json or {}
    scan_id = (params.get('scan_id') or '').strip()
    if not scan_id:
        return jsonify({'error': 'scan_id required'}), 400
    if archive_state['active']:
        return jsonify({'error': 'archive already running'}), 409
    if not re.match(r'^[A-Za-z0-9_+\-]+$', scan_id):
        return jsonify({'error': 'Invalid scan_id'}), 400

    meta_path = os.path.join(RESULTS_DIR, scan_id, 'scan_meta.json')
    if not os.path.isfile(meta_path):
        return jsonify({'error': f'scan {scan_id} not found'}), 404
    try:
        with open(meta_path, encoding='utf-8-sig') as f:
            meta = json.load(f)
    except Exception as e:
        return jsonify({'error': f'cannot read scan_meta: {e}'}), 500

    target = (meta.get('target') or '').strip().upper()
    files = (meta.get('parameters') or {}).get('files') or []
    if not target or not files:
        return jsonify({'error': 'scan meta missing target/files'}), 400

    jobs = []
    for rel in files:
        fname = str(rel).replace('\\', '/').split('/')[-1]
        src = _resolve_data_file(os.path.join('fine', fname)) or \
            _resolve_data_file(fname)
        if not src:
            return jsonify({'error': f'file not found on any data root: {fname}'}), 404
        dst = os.path.join(ARCHIVE_ROOT, target, 'fine', fname)
        jobs.append((src, dst))

    thread = threading.Thread(target=_run_archive_epoch,
                              args=(scan_id, jobs), daemon=True)
    thread.start()
    return jsonify({'success': True, 'files': len(jobs)})


def _run_archive_epoch(scan_id, jobs):
    archive_state.update(active=True, scan_id=scan_id, stage='copying',
                         files_done=0, files_total=len(jobs),
                         bytes_copied=0, error=None)
    try:
        for src, dst in jobs:
            if os.path.realpath(src) == os.path.realpath(dst):
                archive_state['files_done'] += 1  # already archived
                continue
            if (os.path.isfile(dst)
                    and os.path.getsize(dst) == os.path.getsize(src)):
                try:
                    os.remove(src)  # archived by a previous run
                except OSError:
                    pass
                archive_state['files_done'] += 1
                continue
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            if os.path.splitdrive(os.path.abspath(src))[0] == \
                    os.path.splitdrive(os.path.abspath(dst))[0]:
                os.rename(src, dst)  # same volume (D:->D:): instant, atomic
            else:
                tmp = dst + '.archiving'
                shutil.copyfile(src, tmp)
                if os.path.getsize(tmp) != os.path.getsize(src):
                    os.remove(tmp)
                    raise RuntimeError(f'size mismatch after copy: {src}')
                os.replace(tmp, dst)   # atomic rename on D:
                os.remove(src)         # free SSD staging only after verified copy
            archive_state['files_done'] += 1
            archive_state['bytes_copied'] += os.path.getsize(dst)
        archive_state.update(active=False, stage='done', last=scan_id)
    except Exception as e:
        archive_state.update(active=False, stage='error', error=str(e))


@app.route('/api/archive/status')
def api_archive_status():
    return jsonify(archive_state)


# ---------------------------------------------------------------------------
# API: GBT epoch sessions (ABACAD cadence browser + bulk download)
# ---------------------------------------------------------------------------

@app.route('/api/gbt/sessions')
def api_gbt_sessions():
    """GBT session layout for a target: sessions grouped by MJD, in-band
    file counts for the chosen band, and ABACAD companions found by sky
    proximity (nearest catalog targets queried directly for the session
    MJDs; prefix-response neighbors are the fallback when the catalog is
    unavailable)."""
    from download_gbt import (api_query as gbt_api_query,
                              sessions_for as gbt_sessions_for,
                              find_companions as gbt_find_companions,
                              find_companions_proximity as gbt_find_companions_prox,
                              in_band as gbt_in_band,
                              BANDS as GBT_BANDS)

    target = request.args.get('target', '').strip()
    band = request.args.get('band', 'L')
    if not target:
        return jsonify({'error': 'No target specified'}), 400
    if band.upper() in GBT_BANDS:
        band_range = GBT_BANDS[band.upper()]
    else:
        try:
            lo, hi = band.split(',')
            band_range = (float(lo), float(hi))
        except Exception:
            return jsonify({'error': f'Bad band: {band}'}), 400
    try:
        raw = gbt_api_query(target)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    files = [f for f in raw
             if f['_parsed']['target'].upper() == target.upper()]
    sess = gbt_sessions_for(files)

    # Companion discovery: proximity (nearest catalog targets on sky,
    # queried for these session MJDs) with prefix-response fallback.
    prox = gbt_find_companions_prox(
        target, {m: sorted({f['_parsed']['seq'] for f in fs})
                 for m, fs in sess.items()})
    prox_source = 'proximity' if prox else None

    out = []
    for mjd, fs in sess.items():
        seqs = sorted({f['_parsed']['seq'] for f in fs})
        band_files = [f for f in fs if gbt_in_band(f, band_range)]

        def gb(lst):
            return round(sum(f['_parsed']['size'] for f in lst) / 1e9, 1)

        if mjd in prox:
            comps = prox[mjd]
        else:
            comps = gbt_find_companions(raw, mjd, target, seqs)
            prox_source = prox_source or 'prefix-fallback'
        comp_out = []
        for cname, cfs in sorted(comps.items()):
            cb = [f for f in cfs if gbt_in_band(f, band_range)]
            comp_out.append({'target': cname, 'n_fine': len(cfs),
                             'n_band': len(cb), 'gb_band': gb(cb)})
        out.append({'mjd': mjd, 'n_fine': len(fs), 'gb_fine': gb(fs),
                    'n_band': len(band_files), 'gb_band': gb(band_files),
                    'seq_first': seqs[0], 'seq_last': seqs[-1],
                    'companions': comp_out})
    out.sort(key=lambda s: int(s['mjd']))
    return jsonify({'target': target, 'band': band,
                    'n_exact_files': len(files),
                    'n_response_files': len(raw),
                    'companion_source': prox_source,
                    'sessions': out})


@app.route('/api/gbt/download', methods=['POST'])
def api_gbt_download():
    """Queue one GBT session's in-band fine files (optionally plus
    ABACAD companion scans) into the standard download pipeline."""
    from download_gbt import (api_query as gbt_api_query,
                              sessions_for as gbt_sessions_for,
                              find_companions as gbt_find_companions,
                              find_companions_proximity as gbt_find_companions_prox,
                              in_band as gbt_in_band,
                              BANDS as GBT_BANDS)

    params = request.json or {}
    target = (params.get('target') or '').strip()
    mjd = str(params.get('mjd') or '')
    band = params.get('band', 'L')
    want_comps = bool(params.get('companions'))
    if not target or not mjd:
        return jsonify({'error': 'target and mjd required'}), 400
    if band.upper() in GBT_BANDS:
        band_range = GBT_BANDS[band.upper()]
    else:
        try:
            lo, hi = band.split(',')
            band_range = (float(lo), float(hi))
        except Exception:
            return jsonify({'error': f'Bad band: {band}'}), 400
    try:
        raw = gbt_api_query(target)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    files = [f for f in raw
             if f['_parsed']['target'].upper() == target.upper()]
    sess = gbt_sessions_for(files)
    if mjd not in sess:
        return jsonify({'error': f'No fine session {mjd} for {target}'}), 404
    fs = sess[mjd]
    seqs = sorted({f['_parsed']['seq'] for f in fs})
    sel = [f for f in fs if gbt_in_band(f, band_range)]
    if want_comps:
        prox = gbt_find_companions_prox(
            target, {mjd: seqs})
        comps = prox.get(mjd) or gbt_find_companions(raw, mjd, target, seqs)
        for cfs in comps.values():
            sel += [f for f in cfs if gbt_in_band(f, band_range)]

    queued = skipped = 0
    total = 0
    for f in sel:
        fname = f['url'].rsplit('/', 1)[-1]
        res = _enqueue_download(f['url'], fname, FINE_DIR,
                                expected_size=f['_parsed']['size'] or None)
        if res.get('status') == 'queued':
            queued += 1
            total += f['_parsed']['size']
        else:
            skipped += 1
    return jsonify({'queued': queued, 'skipped': skipped,
                    'gb_queued': round(total / 1e9, 1)})


@app.route('/api/download', methods=['POST'])
def api_download():
    """Download a file from BL servers to local data directory.
    
    Streams the download in a background thread with progress tracking.
    Downloads to data/fine/ for fine-res files, data/ for others.
    """
    params = request.json or {}
    url = params.get('url', '')
    filename = params.get('filename', '')
    
    if not url or not filename:
        return jsonify({'error': 'Missing url or filename'}), 400
    
    # Determine target directory based on resolution and format in filename
    if '_fine.' in filename:
        target_dir = FINE_DIR
    elif '_mid.' in filename:
        target_dir = MID_DIR
    elif filename.endswith('.fil'):
        target_dir = FILT_DIR
    elif 'guppi_' in filename and '.0000.' in filename:
        # GBT fine-res (tier 0000): route beside Parkes fine data so the
        # Local Data scan and session-browser downloads share one home
        target_dir = FINE_DIR
    elif filename.endswith('.h5'):
        # HDF5 without resolution marker: route to fine if large, mid if small
        # BL fine-res files are ~15GB, mid-res are ~233MB
        # We can't know size yet, so put in a general h5 dir
        target_dir = os.path.join(DATA_DIR, 'h5')
    else:
        target_dir = DATA_DIR
    
    os.makedirs(target_dir, exist_ok=True)
    target_path = os.path.join(target_dir, filename)
    
    res = _enqueue_download(url, filename, target_dir)
    code = 409 if res.get('status') == 'already-downloading' else 200
    return jsonify(res), code


def _enqueue_download(url, filename, target_dir, expected_size=None):
    """Queue one file into the serialized download pipeline.

    Shared by /api/download (single file from the search UI) and
    /api/gbt/download (session bulk). expected_size lets partial files
    left by a killed download be detected (wrong size) and replaced
    instead of blocking re-downloads forever.
    """
    os.makedirs(target_dir, exist_ok=True)
    target_path = os.path.join(target_dir, filename)

    for item in download_state['queue']:
        if item['filename'] == filename and item['status'] in ('downloading', 'queued'):
            return {'error': 'Already downloading',
                    'status': 'already-downloading', 'filename': filename}
    if os.path.isfile(target_path):
        size = os.path.getsize(target_path)
        if expected_size and size != expected_size:
            try:
                os.remove(target_path)  # partial: killed mid-download earlier
            except OSError:
                pass
        else:
            return {'status': 'exists', 'filename': filename, 'size_bytes': size,
                    'path': os.path.relpath(target_path, SETI_ROOT)}

    item = {
        'url': url,
        'filename': filename,
        'target_path': target_path,
        'target_dir': target_dir,
        'status': 'queued',
        'progress': 0.0,
        'speed_mbs': 0.0,
        'eta_s': 0,
        'size_total': expected_size or 0,
        'size_done': 0,
        'error': None,
    }
    download_state['queue'].append(item)

    def do_download(dl_item):
        import urllib.request
        import time as _time

        # Wait if another download is active (serialize downloads)
        while download_state['active'] is not None and download_state['active'] is not dl_item:
            _time.sleep(1)
            if dl_item not in download_state['queue'] or dl_item.get('status') == 'cancelled':
                return  # Cancelled

        download_state['active'] = dl_item
        dl_item['status'] = 'downloading'

        try:
            req = urllib.request.Request(dl_item['url'],
                                         headers={'User-Agent': 'BackyardSETI/1.0'})
            with urllib.request.urlopen(req, timeout=60) as resp:
                if int(resp.headers.get('Content-Length', 0) or 0):
                    dl_item['size_total'] = int(resp.headers['Content-Length'])

                with open(dl_item['target_path'], 'wb') as f:
                    done = 0
                    chunk_size = 1024 * 1024  # 1 MB chunks
                    last_time = _time.time()
                    last_done = 0

                    while True:
                        if dl_item.get('status') == 'cancelled':
                            # cancel endpoint marked us; stop reading, clean
                            # up the partial after the file handle closes
                            # (its own os.remove fails while we hold the file)
                            break
                        chunk = resp.read(chunk_size)
                        if not chunk:
                            break
                        f.write(chunk)
                        done += len(chunk)
                        dl_item['size_done'] = done

                        if dl_item['size_total'] > 0:
                            dl_item['progress'] = round(done / dl_item['size_total'] * 100, 2)

                        # Calculate speed every 2 seconds
                        now = _time.time()
                        if now - last_time >= 2:
                            elapsed = now - last_time
                            bytes_diff = done - last_done
                            dl_item['speed_mbs'] = round(bytes_diff / elapsed / 1e6, 2)
                            if dl_item['speed_mbs'] > 0:
                                remaining = dl_item['size_total'] - done
                                dl_item['eta_s'] = int(remaining / (bytes_diff / elapsed))
                            last_time = now
                            last_done = done

            if dl_item.get('status') == 'cancelled':
                if os.path.isfile(dl_item['target_path']):
                    try:
                        os.remove(dl_item['target_path'])
                    except OSError:
                        pass
            else:
                dl_item['status'] = 'complete'
                dl_item['progress'] = 100.0

        except Exception as e:
            dl_item['status'] = 'error'
            dl_item['error'] = str(e)
            # Clean up partial file
            if os.path.isfile(dl_item['target_path']):
                try:
                    os.remove(dl_item['target_path'])
                except:
                    pass
        finally:
            if download_state['active'] is dl_item:
                download_state['active'] = None

    thread = threading.Thread(target=do_download, args=(item,), daemon=True)
    thread.start()
    return {'status': 'queued', 'filename': filename}


@app.route('/api/download/status')
def api_download_status():
    """Get status of all downloads."""
    # Clean up old completed downloads (keep last 10)
    completed = [i for i, q in enumerate(download_state['queue']) 
                 if q['status'] in ('complete', 'error')]
    if len(completed) > 10:
        for idx in reversed(completed[10:]):
            download_state['queue'].pop(idx)
    
    return jsonify({
        'active': download_state['active'] is not None,
        'queue': [{
            'filename': q['filename'],
            'status': q['status'],
            'progress': q['progress'],
            'speed_mbs': q['speed_mbs'],
            'eta_s': q['eta_s'],
            'size_total': q['size_total'],
            'size_done': q['size_done'],
            'error': q['error'],
        } for q in download_state['queue']],
    })


# ������ Epoch Audit (RFI zone scan) ����������������������������������
audit_state = {'active': False, 'epoch': None, 'stage': '', 'progress': 0,
               'total': 0, 'result': None, 'error': None}


@app.route('/api/audit/run', methods=['POST'])
def api_audit_run():
    """Run epoch_audit.py on one epoch of any Parkes target in a
    background thread. Target defaults to PROXCEN; the UI passes the
    name from the Target Search box."""
    params = request.json or {}
    epoch = str(params.get('epoch', '')).strip()
    target = str(params.get('target', 'PROXCEN')).strip().upper() or 'PROXCEN'
    if not epoch.isdigit():
        return jsonify({'error': 'Epoch must be numeric, e.g. 57910'}), 400
    if audit_state['active']:
        return jsonify({'error': f"Audit already running for {audit_state['epoch']}"}), 409

    def _cb(st):
        ph = st.get('phase')
        if ph == 'scanning':
            audit_state['progress'] = st.get('window', 0)
            audit_state['total'] = st.get('total', 0)
            audit_state['stage'] = (f"pair {st.get('pair', '?')}: window "
                                    f"{st.get('window', 0)}/{st.get('total', 0)}")
        elif ph == 'confirming':
            audit_state['stage'] = 'confirming flagged regions with 2nd pair'
        elif ph == 'done':
            audit_state['stage'] = 'done'

    def _run():
        try:
            _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            if _root not in sys.path:
                sys.path.insert(0, _root)
            import epoch_audit as _ea
            report = _ea.audit_epoch(epoch, progress_callback=_cb,
                                     target=target)
            if report is None:
                audit_state['error'] = (f"no report - epoch {epoch} of "
                                        f"{target} not found on disk or "
                                        f"files unreadable")
            else:
                audit_state['result'] = report
        except Exception as e:
            import traceback
            traceback.print_exc()
            audit_state['error'] = str(e) or type(e).__name__
        finally:
            audit_state['active'] = False

    audit_state.update({'active': True, 'epoch': epoch, 'target': target,
                        'stage': 'starting',
                        'progress': 0, 'total': 0, 'result': None, 'error': None})
    threading.Thread(target=_run, daemon=True).start()
    return jsonify({'status': 'queued', 'epoch': epoch, 'target': target})


@app.route('/api/audit/status')
def api_audit_status():
    return jsonify(audit_state)


@app.route('/api/download/clear', methods=['POST'])
def api_download_clear():
    """Clear all completed/errored/cancelled downloads from the queue."""
    download_state['queue'] = [q for q in download_state['queue']
                               if q['status'] == 'downloading' or q['status'] == 'queued']
    return jsonify({'success': True, 'remaining': len(download_state['queue'])})


@app.route('/api/download/cancel', methods=['POST'])
def api_download_cancel():
    """Cancel a queued or active download."""
    params = request.json or {}
    filename = params.get('filename', '')
    
    for item in download_state['queue']:
        if item['filename'] == filename:
            if item['status'] == 'downloading':
                item['status'] = 'cancelled'
                item['error'] = 'Cancelled by user'
                # The download thread will check and stop
                # Clean up partial file
                if os.path.isfile(item['target_path']):
                    try:
                        os.remove(item['target_path'])
                    except:
                        pass
                if download_state['active'] is item:
                    download_state['active'] = None
            elif item['status'] == 'queued':
                item['status'] = 'cancelled'
            return jsonify({'status': 'cancelled'})
    
    return jsonify({'error': 'Download not found'}), 404


# ─── API: Statistics ──────────────────────────────────────────────────

def _complete_scan_meta(scan_id, duration_s=0):
    """Update scan_meta.json with final stats after scan completes."""
    scan_dir = _get_scan_dir(scan_id)
    if not scan_dir:
        return
    
    meta = _load_scan_meta(scan_dir) or {
        'scan_id': scan_id,
        'target': 'unknown',
        'timestamp': datetime.now().isoformat(timespec='seconds'),
    }
    _scan_target = str(meta.get('target', '') or '')
    
    on_hits = 0
    off_hits = 0
    total_hits = 0
    
    for fpath in glob_module.glob(
        os.path.join(scan_dir, '**/*_hits.json'), recursive=True):
        try:
            with open(fpath) as f:
                data = json.load(f)
            hits = data.get('hits', [])
            fname = os.path.basename(fpath)
            is_on = _file_is_on(fname, _scan_target)
            count = len(hits)
            total_hits += count
            if is_on:
                on_hits += count
            else:
                off_hits += count
        except:
            pass
    
    meta['status'] = 'complete'
    meta['stats'] = {
        'total_hits': total_hits,
        'on_hits': on_hits,
        'off_hits': off_hits,
        'duration_s': round(duration_s, 1),
    }
    
    with open(os.path.join(scan_dir, 'scan_meta.json'), 'w') as f:
        json.dump(meta, f, indent=2)


@app.route('/api/stats')
def api_stats():
    """Aggregate statistics. If scan_id provided, scope to that scan only."""
    scan_id = request.args.get('scan_id', None)
    
    total_hits = 0
    on_hits = 0
    off_hits = 0
    top_snr = 0
    top_hit = None
    
    if scan_id:
        scan_dir = _get_scan_dir(scan_id)
        if not scan_dir:
            return jsonify({'error': f'Scan not found: {scan_id}'}), 404
        patterns = [os.path.join(scan_dir, '**/*_hits.json')]
    else:
        patterns = [
            os.path.join(RESULTS_DIR, 'validation_50mhz', '**/*_hits.json'),
            os.path.join(RESULTS_DIR, 'validation_50mhz', '*_summary.json'),
            os.path.join(RESULTS_DIR, 'fine_pipeline', '**/*_hits.json'),
        ]
        # Also include any scan directories with scan_meta.json
        for scan in _discover_scans():
            sid = scan.get('scan_id', '')
            if sid and sid != 'validation_50mhz':
                sd = _get_scan_dir(sid)
                if sd:
                    patterns.append(os.path.join(sd, '**/*_hits.json'))
    
    seen_files = set()
    for pattern in patterns:
        for fpath in glob_module.glob(pattern, recursive=True):
            if fpath in seen_files:
                continue
            seen_files.add(fpath)
            try:
                with open(fpath) as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    hits = data.get('hits', [])
                    total_hits += len(hits)
                    is_on = _file_is_on(fpath, scan_id.split('_')[0] if scan_id else '')
                    if is_on:
                        on_hits += len(hits)
                    else:
                        off_hits += len(hits)
                    for h in hits:
                        if h.get('snr', 0) > top_snr:
                            top_snr = h['snr']
                            top_hit = h
            except:
                pass
    
    return jsonify({
        'total_hits': total_hits,
        'on_hits': on_hits,
        'off_hits': off_hits,
        'top_snr': round(top_snr, 2),
        'top_hit': top_hit,
    })


# ─── API: ON/OFF Rejection ────────────────────────────────────────────

@app.route('/api/reject', methods=['POST'])
def api_reject():
    """Perform ON/OFF cadence rejection on existing hit results.
    
    Compares ON-source hits against OFF-source hits by frequency proximity.
    Any ON hit that has a matching OFF hit within the frequency tolerance
    is classified as RFI and rejected. Remaining ON-only hits are candidates.
    
    Parameters (JSON body):
      - tolerance_mhz: frequency match tolerance in MHz (default 0.001 = 1 kHz)
      - drift_tolerance: drift rate match tolerance in Hz/s (default 0.5)
      - source: which result set to use ('validation_50mhz', 'fine_pipeline', etc.)
    """
    params = request.json or {}
    tolerance_mhz = params.get('tolerance_mhz', 0.001)
    drift_tolerance = params.get('drift_tolerance', 0.5)
    source = params.get('source', params.get('scan_id', 'validation_50mhz'))
    
    # Load all hits from the specified result set
    source_dir = os.path.join(RESULTS_DIR, source)
    if not os.path.isdir(source_dir):
        return jsonify({'error': f'Source directory not found: {source}'}), 404
    
    on_hits = []
    off_hits = []
    
    for fpath in glob_module.glob(os.path.join(source_dir, '**/*_hits.json'), recursive=True):
        try:
            with open(fpath) as f:
                data = json.load(f)
            hits = data.get('hits', [])
            filename = data.get('file', os.path.basename(fpath))
            is_on = _file_is_on(filename, source.split('_')[0]) or data.get('on_off') == 'ON'
            for h in hits:
                h['source_file'] = filename
                if is_on:
                    on_hits.append(h)
                else:
                    off_hits.append(h)
        except Exception:
            pass
    
    if not on_hits:
        return jsonify({'error': 'No ON-source hits found'}), 400
    if not off_hits:
        return jsonify({'error': 'No OFF-source hits found'}), 400
    
    # Build OFF frequency index for fast lookup
    # Round frequencies to tolerance grid for O(1) matching
    freq_grid = int(1.0 / tolerance_mhz)  # buckets per MHz
    off_index = {}
    for h in off_hits:
        freq = h.get('freq', 0)
        bucket = int(round(freq * freq_grid))
        if bucket not in off_index:
            off_index[bucket] = []
        off_index[bucket].append(h)
    
    # Match each ON hit against OFF index
    candidates = []
    rejected = []
    
    for h in on_hits:
        freq = h.get('freq', 0)
        drift = h.get('drift_rate', 0)
        bucket = int(round(freq * freq_grid))
        
        matched = False
        # Check current bucket and neighbors
        for dbucket in range(-1, 2):
            key = bucket + dbucket
            if key not in off_index:
                continue
            for off_h in off_index[key]:
                freq_diff = abs(freq - off_h.get('freq', 0))
                drift_diff = abs(drift - off_h.get('drift_rate', 0))
                if freq_diff <= tolerance_mhz and drift_diff <= drift_tolerance:
                    matched = True
                    break
            if matched:
                break
        
        if matched:
            h['status'] = 'RFI'
            h['on_off'] = 'OFF'
            rejected.append(h)
        else:
            h['status'] = 'CANDIDATE'
            h['on_off'] = 'ON'
            candidates.append(h)
    
    # Sort candidates by SNR descending
    candidates.sort(key=lambda x: x.get('snr', 0), reverse=True)
    
    # Save rejection results
    reject_dir = os.path.join(RESULTS_DIR, source, 'rejection')
    os.makedirs(reject_dir, exist_ok=True)
    reject_path = os.path.join(reject_dir, 'rejection_results.json')
    with open(reject_path, 'w') as f:
        json.dump({
            'parameters': {
                'tolerance_mhz': tolerance_mhz,
                'drift_tolerance': drift_tolerance,
                'source': source,
            },
            'summary': {
                'total_on': len(on_hits),
                'total_off': len(off_hits),
                'rejected_rfi': len(rejected),
                'candidates': len(candidates),
                'rejection_rate': round(len(rejected) / max(len(on_hits), 1) * 100, 2),
            },
            'candidates': candidates,  # All candidates sorted by SNR
            'rejected_count': len(rejected),
        }, f, indent=2)
    
    return jsonify({
        'parameters': {
            'tolerance_mhz': tolerance_mhz,
            'drift_tolerance': drift_tolerance,
            'source': source,
        },
        'summary': {
            'total_on': len(on_hits),
            'total_off': len(off_hits),
            'rejected_rfi': len(rejected),
            'candidates': len(candidates),
            'rejection_rate': round(len(rejected) / max(len(on_hits), 1) * 100, 2),
        },
        'candidates': candidates,  # All candidates
    })


@app.route('/api/rejection/results')
def api_rejection_results():
    """Get saved rejection results. If scan_id provided, scope to that scan."""
    scan_id = request.args.get('scan_id', None)
    
    if scan_id:
        scan_dir = _get_scan_dir(scan_id)
        if scan_dir:
            reject_path = os.path.join(scan_dir, 'rejection', 'rejection_results.json')
            if os.path.isfile(reject_path):
                with open(reject_path) as f:
                    return jsonify(json.load(f))
        return jsonify({'error': 'No rejection results for this scan'}), 404
    
    # Legacy: search known directories
    for source in ['validation_50mhz', 'fine_pipeline', 'dashboard_scan']:
        reject_path = os.path.join(RESULTS_DIR, source, 'rejection', 'rejection_results.json')
        if os.path.isfile(reject_path):
            with open(reject_path) as f:
                return jsonify(json.load(f))
    return jsonify({'error': 'No rejection results found'}), 404


# ─── API: Barycentric Correction ─────────────────────────────────────

# === Barycentric busy guard: one operation per scan at a time ===
# Without this, delete during an active correction hits WinError 32 on
# Windows (files held open by the correction worker) and dies partway
# through, leaving a half-deleted barycentric/ directory (2026-08-18).
_bary_busy_lock = threading.Lock()
_bary_busy = {}  # scan_id -> operation description

def _bary_mark_busy(scan_id, op):
    with _bary_busy_lock:
        if scan_id in _bary_busy:
            return False
        _bary_busy[scan_id] = op
        return True

def _bary_clear_busy(scan_id):
    with _bary_busy_lock:
        _bary_busy.pop(scan_id, None)


@app.route('/api/barycentric/correct', methods=['POST'])
def api_barycentric_correct():
    """Run barycentric correction on a scan directory.
    
    Body: {scan_id, ra_hours?, dec_deg?, telescope?}
    Auto-detects coordinates from target name and HDF5 headers if not provided.
    """
    params = request.json or {}
    scan_id = params.get('scan_id', '')
    ra_hours = params.get('ra_hours', None)
    dec_deg = params.get('dec_deg', None)
    telescope = params.get('telescope', 'parkes')
    
    if not scan_id:
        return jsonify({'error': 'No scan_id specified'}), 400
    
    scan_dir = _get_scan_dir(scan_id)
    if not scan_dir:
        return jsonify({'error': f'Scan not found: {scan_id}'}), 404
    
    if not _bary_mark_busy(scan_id, 'correction'):
        return jsonify({'error': 'A barycentric operation is already running for this scan. Wait for it to finish.'}), 409
    
    try:
        from barycentric_correct import correct_scan, resolve_target_coords
        
        # Auto-detect target from scan meta
        meta = _load_scan_meta(scan_dir) or {}
        target_name = meta.get('target', params.get('target', ''))
        
        # Auto-detect coords if not provided
        if ra_hours is not None:
            ra_hours = float(ra_hours)
        if dec_deg is not None:
            dec_deg = float(dec_deg)
        if ra_hours is None or dec_deg is None:
            db_ra, db_dec, src = resolve_target_coords(target_name, ra_hours, dec_deg)
            if db_ra is not None:
                ra_hours = db_ra
                dec_deg = db_dec
        
        result = correct_scan(
            scan_dir, 
            ra_hours=ra_hours, 
            dec_deg=dec_deg,
            telescope=telescope, 
            target_name=target_name,
        )
        
        # Sync correction results into the database so hits views show bary freqs
        bary_stats = {}
        try:
            from db import (update_barycentric_freqs, update_scan_barycentric,
                            count_hits)
            bary_updates = []
            vel = mjd_val = None
            for fname in result['files_corrected']:
                bp = os.path.join(result['barycentric_dir'],
                                  os.path.basename(fname) + '_bary_hits.json')
                if not os.path.isfile(bp):
                    continue
                with open(bp) as f:
                    bdata = json.load(f)
                sf = bdata.get('file', os.path.basename(fname))
                if sf and not sf.endswith('.h5'):
                    sf += '.h5'
                for h in bdata.get('hits', []):
                    if 'barycentric_freq' in h:
                        if vel is None:
                            vel = h.get('barycentric_velocity_mps', 0) or 0
                        if mjd_val is None:
                            mjd_val = h.get('mjd', 0) or 0
                        bary_updates.append({
                            'freq': h.get('freq'),
                            'barycentric_freq': h['barycentric_freq'],
                            'source_file': sf,
                        })
            if bary_updates:
                update_barycentric_freqs(scan_id, bary_updates)
                bary_stats = {'bary_updated': len(bary_updates)}
                if vel is not None:
                    update_scan_barycentric(
                        scan_id, vel, mjd_val, ra_hours, dec_deg, telescope)
        except Exception as e:
            import traceback
            bary_stats = {'db_sync_error': str(e),
                          'traceback': traceback.format_exc()[-300:]}
        
        return jsonify({
            'status': 'complete',
            'scan_id': scan_id,
            'files_corrected': len(result['files_corrected']),
            'total_hits': result['total_hits'],
            'corrections': result['corrections'],
            'barycentric_dir': result['barycentric_dir'],
            'db_sync': bary_stats,
        })
    except Exception as e:
        import traceback
        return jsonify({'error': str(e), 'traceback': traceback.format_exc()[-500:]}), 500
    finally:
        _bary_clear_busy(scan_id)


@app.route('/api/barycentric/results/<scan_id>')
def api_barycentric_results(scan_id):
    """Get corrected hits for a scan."""
    scan_dir = _get_scan_dir(scan_id)
    if not scan_dir:
        return jsonify({'error': f'Scan not found: {scan_id}'}), 404
    
    bary_dir = os.path.join(scan_dir, 'barycentric')
    combined_path = os.path.join(bary_dir, 'combined_corrected.json')
    
    if not os.path.isfile(combined_path):
        return jsonify({'error': 'No barycentric correction results found. Run correction first.'}), 404
    
    with open(combined_path) as f:
        data = json.load(f)
    
    # Paginate hits for display (the combined file can be huge)
    page = request.args.get('page', default=1, type=int)
    per_page = request.args.get('per_page', default=200, type=int)
    snr_min = request.args.get('snr_min', default=0, type=float)
    
    hits = data.get('hits', [])
    # Filter by SNR
    if snr_min > 0:
        hits = [h for h in hits if h.get('snr', 0) >= snr_min]
    
    total = len(hits)
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))
    start = (page - 1) * per_page
    page_hits = hits[start:start + per_page]
    
    return jsonify({
        'scan_id': scan_id,
        'target': data.get('target', ''),
        'ra_hours': data.get('ra_hours', 0),
        'dec_deg': data.get('dec_deg', 0),
        'telescope': data.get('telescope', ''),
        'total_hits': data.get('total_hits', 0),
        'files_corrected': data.get('files_corrected', 0),
        'corrections': data.get('corrections', {}),
        'hits': page_hits,
        'page': page,
        'per_page': per_page,
        'total_filtered': total,
        'total_pages': total_pages,
    })


# ─── Cross-Epoch Cache Helpers ──────────────────────────────────────

def _cross_epoch_cache_dir():
    """Return the cross-epoch cache directory, creating it if needed."""
    cache_dir = os.path.join(SETI_ROOT, 'results', 'cross_epoch_cache')
    os.makedirs(cache_dir, exist_ok=True)
    return cache_dir

def _cross_epoch_cache_filename(scan_ids, min_snr, tol_hz, min_epochs):
    """Build a deterministic cache filename for given parameters."""
    ids_str = '_'.join(scan_ids)
    # Sanitize: keep alphanumerics, underscores, hyphens
    ids_safe = re.sub(r'[^A-Za-z0-9_-]', '', ids_str)
    return f'cross_epoch_{ids_safe}_snr{min_snr}_tol{tol_hz}_ep{min_epochs}.json'


@app.route('/api/barycentric/cross-epoch', methods=['POST'])
def api_barycentric_cross_epoch():
    """Run cross-epoch comparison across multiple scans.
    
    Body: {scan_ids, freq_tolerance_hz?, min_epochs?, min_snr?, force_rerun?}
    """
    params = request.json or {}
    scan_ids = params.get('scan_ids', [])
    freq_tolerance_hz = params.get('freq_tolerance_hz', 10)
    min_epochs = params.get('min_epochs', 2)
    min_snr = params.get('min_snr', 0)
    force_rerun = params.get('force_rerun', False)
    
    if not scan_ids or len(scan_ids) < 2:
        return jsonify({'error': 'Need at least 2 scan_ids for cross-epoch comparison'}), 400
    
    scan_dirs = []
    for sid in scan_ids:
        sd = _get_scan_dir(sid)
        if not sd:
            return jsonify({'error': f'Scan not found: {sid}'}), 404
        scan_dirs.append(sd)
    
    # Check cache first (unless force_rerun)
    cache_dir = _cross_epoch_cache_dir()
    cache_file = _cross_epoch_cache_filename(scan_ids, min_snr, freq_tolerance_hz, min_epochs)
    cache_path = os.path.join(cache_dir, cache_file)
    
    if not force_rerun and os.path.isfile(cache_path):
        try:
            with open(cache_path) as f:
                result = json.load(f)
            if 'freqs_meeting_min_epochs' not in result.get('summary', {}):
                raise ValueError('stale cache format, recomputing')
            result['summary']['from_cache'] = True
            result['summary']['cache_file'] = cache_file
            return jsonify(result)
        except Exception:
            pass  # Cache read failed, fall through to recompute
    
    try:
        from barycentric_correct import cross_epoch_match
        
        result = cross_epoch_match(
            scan_dirs,
            freq_tolerance_hz=float(freq_tolerance_hz),
            min_epochs=int(min_epochs),
            min_snr=float(min_snr),
        )
        result['summary']['from_cache'] = False
        result['summary']['cache_file'] = cache_file
        
        # Save to cache
        try:
            with open(cache_path, 'w') as f:
                json.dump(result, f)
        except Exception as e:
            print(f'[WARNING] Failed to cache cross-epoch result: {e}')
        
        return jsonify(result)
    except Exception as e:
        import traceback
        return jsonify({'error': str(e), 'traceback': traceback.format_exc()[-500:]}), 500


@app.route('/api/barycentric/cross-epoch/history')
def api_barycentric_cross_epoch_history():
    """List all cached cross-epoch runs, newest first."""
    cache_dir = _cross_epoch_cache_dir()
    runs = []
    
    for entry in os.listdir(cache_dir):
        if not entry.endswith('.json'):
            continue
        if not re.match(r'^[A-Za-z0-9_-]+\.json$', entry):
            continue
        full_path = os.path.join(cache_dir, entry)
        try:
            mtime = os.path.getmtime(full_path)
            # Try to load summary from the cached file
            with open(full_path) as f:
                data = json.load(f)
            summary = data.get('summary', {})
            runs.append({
                'filename': entry,
                'timestamp': datetime.fromtimestamp(mtime).isoformat(),
                'scan_ids': summary.get('scan_ids', []),
                'min_snr': summary.get('min_snr', 0),
                'tolerance_hz': summary.get('freq_tolerance_hz', 10),
                'min_epochs': summary.get('min_epochs', 2),
                'candidate_count': summary.get('total_candidates', 0),
                'total_scans': summary.get('total_scans', 0),
            })
        except Exception:
            continue
    
    runs.sort(key=lambda r: r.get('timestamp', ''), reverse=True)
    return jsonify({'runs': runs})


@app.route('/api/barycentric/cross-epoch/load')
def api_barycentric_cross_epoch_load():
    """Load a specific cached cross-epoch result by filename."""
    filename = request.args.get('file', '')
    if not filename or not re.match(r'^[A-Za-z0-9_-]+\.json$', filename):
        return jsonify({'error': 'Invalid filename'}), 400
    
    cache_path = os.path.join(_cross_epoch_cache_dir(), filename)
    if not os.path.isfile(cache_path):
        return jsonify({'error': 'Cached result not found'}), 404
    
    try:
        with open(cache_path) as f:
            result = json.load(f)
        result['summary']['from_cache'] = True
        result['summary']['cache_file'] = filename
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/barycentric/cross-epoch/delete', methods=['DELETE'])
def api_barycentric_cross_epoch_delete():
    """Delete a legacy file-cached cross-epoch result by filename."""
    filename = request.args.get('file', '')
    if not filename or not re.match(r'^[A-Za-z0-9_-]+\.json$', filename):
        return jsonify({'error': 'Invalid filename'}), 400
    
    cache_path = os.path.join(_cross_epoch_cache_dir(), filename)
    if os.path.isfile(cache_path):
        os.remove(cache_path)
        return jsonify({'success': True, 'deleted': filename})
    return jsonify({'error': 'File not found'}), 404


@app.route('/api/barycentric/corrected/<scan_id>')
def api_barycentric_corrected_load(scan_id):
    """Load pre-computed barycentric correction results for a scan."""
    if not re.match(r'^[A-Za-z0-9_-]+$', scan_id):
        return jsonify({'error': 'Invalid scan_id'}), 400
    
    scan_dir = _get_scan_dir(scan_id)
    if not scan_dir:
        return jsonify({'error': 'Scan not found'}), 404
    
    combined_path = os.path.join(scan_dir, 'barycentric', 'combined_corrected.json')
    if not os.path.isfile(combined_path):
        return jsonify({'error': 'No barycentric correction found for this scan'}), 404
    
    # Use existing paginated results endpoint logic
    page = request.args.get('page', default=1, type=int)
    per_page = request.args.get('per_page', default=500, type=int)
    snr_min = request.args.get('snr_min', default=0, type=float)
    
    try:
        with open(combined_path) as f:
            data = json.load(f)
        
        hits = data.get('hits', [])
        # Apply SNR filter
        if snr_min > 0:
            hits = [h for h in hits if h.get('snr', 0) >= snr_min]
        
        total_filtered = len(hits)
        total_hits = len(data.get('hits', []))
        total_pages = max(1, (total_filtered + per_page - 1) // per_page)
        start = (page - 1) * per_page
        page_hits = hits[start:start + per_page]
        
        # Count corrected files
        files_set = set()
        for h in data.get('hits', []):
            sf = h.get('source_file', '')
            if sf:
                files_set.add(sf)
        
        return jsonify({
            'hits': page_hits,
            'total_hits': total_hits,
            'total_filtered': total_filtered,
            'page': page,
            'per_page': per_page,
            'total_pages': total_pages,
            'target': data.get('target', ''),
            'mjd': data.get('mjd', 0),
            'files_corrected': len(files_set),
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/barycentric/delete/<scan_id>', methods=['DELETE'])
def api_barycentric_delete(scan_id):
    """Delete barycentric correction data for a scan.
    
    Removes the entire barycentric/ subdirectory and all corrected data.
    The scan itself is not affected — correction can be re-run later.
    """
    if not re.match(r'^[A-Za-z0-9_-]+$', scan_id):
        return jsonify({'error': 'Invalid scan_id'}), 400
    
    scan_dir = _get_scan_dir(scan_id)
    if not scan_dir:
        return jsonify({'error': f'Scan not found: {scan_id}'}), 404
    
    bary_dir = os.path.join(scan_dir, 'barycentric')
    if not os.path.isdir(bary_dir):
        return jsonify({'error': 'No barycentric correction found for this scan'}), 404
    
    if not _bary_mark_busy(scan_id, 'delete'):
        return jsonify({'error': 'A barycentric operation is already running for this scan. Wait for it to finish, then delete again.'}), 409
    
    def _rmtree_retry(path, retries=6, delay_s=1.0):
        """rmtree with brief retries for transient Windows file locks
        (AV scanners, h5py handles closing)."""
        import time as _time
        last_err = None
        for attempt in range(retries):
            try:
                shutil.rmtree(path)
                return None
            except Exception as e:
                last_err = e
                _time.sleep(delay_s)
        return last_err
    
    try:
        err = _rmtree_retry(bary_dir)
        if err:
            raise err
        return jsonify({'success': True, 'deleted': scan_id})
    except Exception as e:
        return jsonify({'error': f'Failed to delete barycentric data: {e}'}), 500
    finally:
        _bary_clear_busy(scan_id)


# ============================================================================
# API: Target Registry (Phase 3A)
# ============================================================================
@app.route('/api/registry')
def api_targets_list():
    """List all registry targets."""
    try:
        from target_registry import list_targets
        return jsonify({'targets': list_targets()})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/registry', methods=['POST'])
def api_targets_add():
    """Add a target. Manual coords win, else SIMBAD resolve, then BL check."""
    params = request.json or {}
    name = (params.get('name') or '').strip()
    if not name:
        return jsonify({'error': 'name required'}), 400
    try:
        from target_registry import add_target
        t = add_target(
            name, ra_hours=params.get('ra_hours'), dec_deg=params.get('dec_deg'),
            display_name=params.get('display_name'),
            aliases=params.get('aliases') or [],
            notes=params.get('notes'),
            priority=params.get('priority', 0) or 0,
            check_bl=bool(params.get('check_bl', True)))
        return jsonify({'success': True, 'target': t})
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/registry/simbad')
def api_targets_simbad():
    """SIMBAD identifier search preview (no DB writes)."""
    name = request.args.get('name', '').strip()
    if not name:
        return jsonify({'error': 'name required'}), 400
    from target_registry import simbad_search
    return jsonify({'results': simbad_search(name)})


@app.route('/api/registry/<name>/blcheck', methods=['POST'])
def api_targets_blcheck(name):
    """Re-run BL fine-res availability check for a target."""
    if not re.match(r'^[A-Za-z0-9_+\-]+$', name):
        return jsonify({'error': 'Invalid name'}), 400
    try:
        from target_registry import refresh_bl
        return jsonify({'success': True, 'availability': refresh_bl(name)})
    except ValueError as e:
        return jsonify({'error': str(e)}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/registry/<name>', methods=['DELETE'])
def api_targets_delete(name):
    if not re.match(r'^[A-Za-z0-9_+\-]+$', name):
        return jsonify({'error': 'Invalid name'}), 400
    try:
        from db import get_db
        conn = get_db()
        cur = conn.execute('DELETE FROM targets WHERE UPPER(name) = UPPER(?)', (name,))
        n = cur.rowcount
        conn.commit()
        conn.close()
        if n == 0:
            return jsonify({'error': 'not found'}), 404
        return jsonify({'success': True, 'deleted': name})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# Cache for barycentric scan status: sid -> (combined mtime, entry).
# Raw/combined hit JSONs hold 100k+ records per scan; parsing them on
# every dropdown load measured ~55s. Entries recompute only when a
# scan's combined_corrected.json mtime changes.
_bary_status_cache = {}
_BARY_CACHE_PATH = os.path.join(DATA_DIR, 'bary_status_cache.json')


def _load_bary_cache():
    """Load the disk-persisted scan-status cache (survives restarts;
    entries whose combined_corrected.json mtime changed re-compute)."""
    if _bary_status_cache:
        return
    try:
        with open(_BARY_CACHE_PATH) as f:
            raw = json.load(f)
        for sid, rec in raw.items():
            _bary_status_cache[sid] = (rec['mtime'], rec['entry'])
    except Exception:
        pass


def _save_bary_cache():
    try:
        os.makedirs(os.path.dirname(_BARY_CACHE_PATH), exist_ok=True)
        with open(_BARY_CACHE_PATH, 'w') as f:
            json.dump({sid: {'mtime': rec[0], 'entry': rec[1]}
                       for sid, rec in _bary_status_cache.items()}, f)
    except Exception:
        pass


def _bary_scan_status(sid, scan_dir):
    _load_bary_cache()
    combined_path = os.path.join(scan_dir, 'barycentric',
                                 'combined_corrected.json')
    mtime = os.path.getmtime(combined_path)
    cached = _bary_status_cache.get(sid)
    if cached and cached[0] == mtime:
        return cached[1]
    status_entry = {'scan_id': sid, 'complete': True,
                    'bary_hits': 0, 'raw_hits': 0}
    try:
        with open(combined_path) as f:
            bary_data = json.load(f)
        status_entry['bary_hits'] = bary_data.get(
            'total_hits', len(bary_data.get('hits', [])))
        hit_files = glob_module.glob(
            os.path.join(scan_dir, '**/*_hits.json'), recursive=True)
        raw_count = 0
        for hf in hit_files:
            if 'barycentric' in hf:
                continue
            try:
                with open(hf) as f:
                    raw_data = json.load(f)
                raw_count += len(raw_data.get('hits', raw_data)
                                 if isinstance(raw_data, dict) else raw_data)
            except Exception:
                pass
        status_entry['raw_hits'] = raw_count
        if raw_count > 0:
            status_entry['complete'] = (status_entry['bary_hits'] /
                                        raw_count) >= 0.95
    except Exception:
        pass
    _bary_status_cache[sid] = (mtime, status_entry)
    _save_bary_cache()
    return status_entry


@app.route('/api/barycentric/targets')
def api_barycentric_targets():
    """Return known target coordinates for the dropdown.
    Registry-only: the SQLite target registry is the single source of
    truth for target coordinates (legacy dict retired 2026-08-15)."""
    from barycentric_correct import TELESCOPE_LOCATIONS
    
    targets = []
    try:
        from target_registry import list_targets
        for t in list_targets():
            if t.get('ra_hours') is None:
                continue
            targets.append({'name': t['name'], 'ra_hours': t['ra_hours'],
                            'dec_deg': t['dec_deg']})
    except Exception as e:
        print(f'[registry] list failed: {e}')
    
    telescopes = []
    for name, info in sorted(TELESCOPE_LOCATIONS.items()):
        telescopes.append({
            'name': name,
            'display_name': info['name'],
            'lat': info['lat'],
            'lon': info['lon'],
            'elev': info['elev'],
        })
    
    # Also return which scans have barycentric correction completed
    # (mtime-cached; see _bary_scan_status)
    corrected_scans = []
    corrected_scans_status = []
    for sm in _discover_scans():
        sid = sm.get('scan_id') or sm.get('_dir', '')
        if not sid:
            continue
        scan_dir = os.path.join(RESULTS_DIR, sid)
        combined_path = os.path.join(scan_dir, 'barycentric',
                                     'combined_corrected.json')
        if os.path.isfile(combined_path):
            corrected_scans.append(sid)
            corrected_scans_status.append(_bary_scan_status(sid, scan_dir))
    
    return jsonify({'targets': targets, 'telescopes': telescopes, 
                     'corrected_scans': corrected_scans,
                     'corrected_scans_status': corrected_scans_status})


# ─── API: Waterfall Data ─────────────────────────────────────────────

@app.route('/api/waterfall')
def api_waterfall():
    """Return a narrow-band waterfall (spectrogram) around a hit frequency.

    Query params:
      - file: path to HDF5 file (relative to SETI_ROOT or data/)
      - freq_mhz: center frequency in MHz
      - width_chans: channels per side (default 200, total = 2*width_chans)
      - max_tints: max time integrations (default 20)
    """
    filepath = request.args.get('file', '')
    freq_mhz = request.args.get('freq_mhz', type=float)
    width_chans = request.args.get('width_chans', default=200, type=int)
    max_tints = request.args.get('max_tints', default=20, type=int)

    if not filepath or freq_mhz is None:
        return jsonify({'error': 'Missing required params: file, freq_mhz'}), 400

    full_path = _resolve_data_file(filepath)

    if not full_path:
        return jsonify({'error': f'File not found: {filepath}'}), 404

    try:
        # Header via h5py directly; bitshuffle is registered by importing
        # hdf5plugin (same plugin blimpy loads internally). Avoids blimpy's
        # full-row decompression for these ~12 GB fine files.
        import h5py
        import hdf5plugin  # noqa: F401

        with h5py.File(full_path, 'r') as _f:
            _attrs = _f['data'].attrs
            fch1 = float(_attrs['fch1'])
            nchans = int(_attrs['nchans'])
            foff = float(_attrs['foff'])  # MHz per channel
            tsamp = float(_attrs.get('tsamp', 18.25))  # seconds

        # df in Hz (abs because foff can be negative)
        df_hz = abs(foff) * 1e6

        # Compute sub-band frequency range
        half_width_mhz = width_chans * abs(foff)
        f_start = freq_mhz - half_width_mhz
        f_stop = freq_mhz + half_width_mhz

        # Clamp to file frequency range
        f_min_file = min(fch1, fch1 + nchans * foff)
        f_max_file = max(fch1, fch1 + nchans * foff)
        f_start = max(f_start, f_min_file)
        f_stop = min(f_stop, f_max_file)

        # Load the sub-band data (direct h5py block reads, no full-file load)
        from incoherent_stack import load_spectrum_window_2d
        freqs, data = load_spectrum_window_2d(full_path, f_start, f_stop)
        if freqs is None:
            return jsonify({'error': f'Failed to load window {f_start:.6f}-{f_stop:.6f} MHz'}), 500

        n_tints, n_chans = data.shape

        # Limit time integrations
        if n_tints > max_tints:
            # Evenly sample across time
            indices = np.linspace(0, n_tints - 1, max_tints, dtype=int)
            data = data[indices]
            n_tints = max_tints

        # Pad data to requested width (2 * width_chans) to avoid blank edges
        target_chans = 2 * width_chans
        if n_chans < target_chans:
            # Pad with NaN on the right side (will render as dark/empty in heatmap)
            padding = np.full((n_tints, target_chans - n_chans), np.nan, dtype=np.float32)
            data = np.hstack([data, padding])
            n_chans = target_chans
            # Extend freqs axis to match
            if len(freqs) < target_chans:
                freqs = np.linspace(f_start, f_stop, target_chans)
        elif n_chans > target_chans:
            # Trim to requested width, centered on hit
            center_idx = np.argmin(np.abs(freqs - freq_mhz))
            half = target_chans // 2
            start_idx = max(0, center_idx - half)
            end_idx = start_idx + target_chans
            if end_idx > n_chans:
                end_idx = n_chans
                start_idx = end_idx - target_chans
            data = data[:, start_idx:end_idx]
            freqs = freqs[start_idx:end_idx]
            n_chans = target_chans

        # freqs is the exact channel grid from the loader; rebuild only if a
        # code path above changed n_chans without slicing freqs to match
        if len(freqs) != n_chans:
            freqs = np.linspace(f_start, f_stop, n_chans)

        # Build time axis (seconds)
        times = np.arange(n_tints, dtype=np.float64) * tsamp

        # Replace NaN/Inf with 0 for JSON safety
        data = np.nan_to_num(data, nan=0.0, posinf=0.0, neginf=0.0)
        
        # Convert to dB above median for better dynamic range visualization
        # This compresses the huge value range (40M to 88B) into a readable scale
        median_val = np.median(data[data > 0]) if np.any(data > 0) else 1.0
        if median_val > 0:
            data = 10.0 * np.log10(np.maximum(data, 1.0) / median_val)

        # Convert to nested lists (time x freq)
        data_list = data.tolist()
        freqs_list = freqs.tolist()
        times_list = times.tolist()

        return jsonify({
            'data': data_list,
            'freqs': freqs_list,
            'times': times_list,
            'n_chans': n_chans,
            'n_tints': n_tints,
            'df_hz': round(df_hz, 4),
            'dt_s': round(tsamp, 4),
        })

    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        return jsonify({'error': str(e), 'traceback': tb[-500:]}), 500


# ─── API: SQLite Database Endpoints ─────────────────────────────────

@app.route('/api/db/stats')
def api_db_stats():
    """Database statistics."""
    try:
        from db import db_stats
        return jsonify(db_stats())
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/db/scans')
def api_db_scans():
    """List all scans from the database (fast, replaces slow JSON discovery)."""
    try:
        from db import get_all_scans
        scans = get_all_scans()
        return jsonify(scans)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/db/scans/<scan_id>', methods=['DELETE'])
def api_db_delete_scan(scan_id):
    """Delete a scan: DB rows AND the on-disk results directory.

    The results dir is moved to results/.trash (recoverable) because the
    scan dropdown merges DB scans with disk discovery - leaving the dir in
    place makes the scan reappear in the list after deletion.
    """
    if not re.match(r'^[A-Za-z0-9_-]+$', scan_id):
        return jsonify({'error': 'Invalid scan_id'}), 400
    try:
        from db import delete_scan
        result = delete_scan(scan_id)
        # Also remove the results directory (to trash, not permanent)
        scan_dir = _get_scan_dir(scan_id)
        if scan_dir and os.path.isdir(scan_dir):
            import shutil
            trash_dir = os.path.join(RESULTS_DIR, '.trash')
            os.makedirs(trash_dir, exist_ok=True)
            dst = os.path.join(trash_dir, scan_id)
            if os.path.exists(dst):
                import time as _t
                dst = os.path.join(trash_dir, scan_id + '.' + str(int(_t.time())))
            shutil.move(scan_dir, dst)
            result['results_dir_removed'] = True
        else:
            result['results_dir_removed'] = False
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/db/scans/<scan_id>/hits')
def api_db_hits(scan_id):
    """Paginated hits from the database."""
    if not re.match(r'^[A-Za-z0-9_-]+$', scan_id):
        return jsonify({'error': 'Invalid scan_id'}), 400
    try:
        from db import get_hits, count_hits
        min_snr = request.args.get('min_snr', default=0, type=float)
        on_off = request.args.get('on_off', default=None, type=str)
        if on_off and on_off not in ('ON', 'OFF'):
            on_off = None
        limit = request.args.get('limit', default=100, type=int)
        offset = request.args.get('offset', default=0, type=int)
        order = request.args.get('order', default='snr DESC', type=str)

        hits = get_hits(scan_id, min_snr=min_snr, on_off=on_off,
                        limit=limit, offset=offset, order_by=order)
        total = count_hits(scan_id, min_snr=min_snr, on_off=on_off)

        return jsonify({
            'scan_id': scan_id,
            'hits': hits,
            'total': total,
            'limit': limit,
            'offset': offset,
            'count': len(hits),
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/db/scans/<scan_id>/stats')
def api_db_scan_stats(scan_id):
    """Hit statistics for a scan from the database."""
    if not re.match(r'^[A-Za-z0-9_-]+$', scan_id):
        return jsonify({'error': 'Invalid scan_id'}), 400
    try:
        from db import get_hit_stats
        stats = get_hit_stats(scan_id)
        return jsonify(stats)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/db/scans/<scan_id>/corrected')
def api_db_corrected(scan_id):
    """Paginated barycentric-corrected hits from the database."""
    if not re.match(r'^[A-Za-z0-9_-]+$', scan_id):
        return jsonify({'error': 'Invalid scan_id'}), 400
    try:
        from db import get_hits, count_hits, get_scan
        min_snr = request.args.get('min_snr', default=0, type=float)
        limit = request.args.get('limit', default=100, type=int)
        offset = request.args.get('offset', default=0, type=int)

        hits = get_hits(scan_id, min_snr=min_snr, limit=limit, offset=offset,
                        order_by='snr DESC')
        total = count_hits(scan_id, min_snr=min_snr)
        scan = get_scan(scan_id)

        return jsonify({
            'scan_id': scan_id,
            'hits': hits,
            'total_filtered': total,
            'total_hits': scan.get('total_hits', 0) if scan else 0,
            'limit': limit,
            'offset': offset,
            'count': len(hits),
            'target': scan.get('target', '') if scan else '',
            'bary_corrected': scan.get('bary_corrected', 0) if scan else 0,
            'bary_velocity': scan.get('bary_velocity') if scan else None,
            'telescope': scan.get('telescope', '') if scan else '',
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/db/cross-epoch', methods=['POST'])
def api_db_cross_epoch():
    """Run SQL-based cross-epoch search, cache result to DB."""
    params = request.json or {}
    scan_ids = params.get('scan_ids', [])
    freq_tolerance_hz = params.get('freq_tolerance_hz', 10)
    min_epochs = params.get('min_epochs', 2)
    min_snr = params.get('min_snr', 0)
    force_rerun = params.get('force_rerun', False)

    if not scan_ids or len(scan_ids) < 2:
        return jsonify({'error': 'Need at least 2 scan_ids'}), 400

    try:
        from db import cross_epoch_search_sql, save_cross_epoch_result, get_cross_epoch_history, load_cross_epoch_result

        # Check for cached result
        if not force_rerun:
            history = get_cross_epoch_history()
            for h in history:
                if (h['min_snr'] == min_snr and
                    h['tolerance_hz'] == freq_tolerance_hz and
                    h['min_epochs'] == min_epochs):
                    cached = load_cross_epoch_result(h['id'])
                    if cached and cached.get('result', {}).get('summary', {}).get(
                            'freqs_meeting_min_epochs') is not None:
                        result = cached['result']
                        result['summary']['from_cache'] = True
                        result['summary']['cache_id'] = h['id']
                        return jsonify(result)

        # Run the search
        result = cross_epoch_search_sql(
            scan_ids,
            min_snr=float(min_snr),
            tolerance_hz=float(freq_tolerance_hz),
            min_epochs=int(min_epochs),
        )

        # Record which scans went into this run so history entries can
        # display their epochs
        result.setdefault('summary', {})['scan_ids'] = scan_ids

        # Save to DB cache
        try:
            result_id = save_cross_epoch_result(result)
            result['summary']['cache_id'] = result_id
            result['summary']['from_cache'] = False
        except Exception as e:
            print(f'[WARNING] Failed to cache cross-epoch result: {e}')

        return jsonify(result)
    except Exception as e:
        import traceback
        return jsonify({'error': str(e), 'traceback': traceback.format_exc()[-500:]}), 500


@app.route('/api/db/cross-epoch/history')
def api_db_cross_epoch_history():
    """List cached cross-epoch results from the database."""
    try:
        from db import get_cross_epoch_history
        runs = get_cross_epoch_history()
        return jsonify({'runs': runs})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/db/cross-epoch/<int:result_id>')
def api_db_cross_epoch_load(result_id):
    """Load a specific cached cross-epoch result by id."""
    try:
        from db import load_cross_epoch_result
        cached = load_cross_epoch_result(result_id)
        if not cached:
            return jsonify({'error': 'Result not found'}), 404
        result = cached['result']
        result['summary']['from_cache'] = True
        result['summary']['cache_id'] = result_id
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/db/cross-epoch/<int:result_id>', methods=['DELETE'])
def api_db_cross_epoch_delete(result_id):
    """Delete a cached cross-epoch result by id."""
    try:
        from db import get_db
        conn = get_db()
        conn.execute('DELETE FROM cross_epoch_results WHERE id = ?', (result_id,))
        conn.commit()
        conn.close()
        return jsonify({'success': True, 'deleted': result_id})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ─── API: Incoherent Stack ────────────────────────────────────────────

import uuid as _uuid
import threading as _threading

# In-memory job tracker for stack runs
_stack_jobs = {}  # job_id -> {thread, status, progress, msg, result}

# Ensure stack output directory exists
STACK_OUTPUT_DIR = os.path.join(SETI_ROOT, 'data', 'stack_results')
os.makedirs(STACK_OUTPUT_DIR, exist_ok=True)


@app.route('/stack')
def stack_page():
    """Serve the Incoherent Stack page."""
    return render_template('stack.html')


@app.route('/api/stack/epochs')
def api_stack_epochs():
    """List available epochs for stacking.

    3C: cadence validation (Parkes: 3 ON + 3 OFF) and per-epoch scan
    status cross-referenced from the scans table via mjd_start.
    """
    target = request.args.get('target', 'PROXCEN').upper()
    try:
        from incoherent_stack import get_available_epochs
        epochs = get_available_epochs(target)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    scan_by_mjd = {}
    try:
        from db import get_db
        conn = get_db()
        rows = conn.execute(
            'SELECT scan_id, status, mjd_start FROM scans '
            'WHERE UPPER(target) = ? AND mjd_start IS NOT NULL '
            'ORDER BY timestamp', (target,)).fetchall()
        conn.close()
        for scan_id, status, mjd_start in rows:
            scan_by_mjd[int(mjd_start)] = {'scan_id': scan_id,
                                           'status': status}
    except Exception:
        pass  # DB unavailable: scan status stays null

    result = []
    for label, info in sorted(epochs.items()):
        scan = scan_by_mjd.get(info['mjd_int'])
        seqs = info.get('seqs') or []
        first_on = (f"Parkes_{label}_{seqs[0][0]}_{target}_S_fine.h5"
                    if seqs else None)
        result.append({
            'label': label,
            'mjd_int': info['mjd_int'],
            'n_pairs': len(seqs),
            'n_on': info.get('n_on', 0),
            'n_off': info.get('n_off', 0),
            'cadence_ok': info.get('cadence_ok'),  # None = unknown (GBT)
            'telescope': info.get('telescope', 'Parkes'),
            'scan_status': scan['status'] if scan else None,
            'scan_id': scan['scan_id'] if scan else None,
            'first_on_file': first_on,
        })
    return jsonify({'epochs': result})


@app.route('/api/stack/run', methods=['POST'])
def api_stack_run():
    """Start an incoherent stack job in the background.

    POST body JSON:
        target:       str   (e.g. 'PROXCEN')
        freq_center:  float (MHz)
        width:        float (MHz, 0 = full band)
        epochs:       list of str
        n_sigma:      float (default 5.0)
        telescope:    str   (default 'parkes')
    """
    params = request.json or {}
    target = params.get('target', 'PROXCEN')
    freq_center = params.get('freq_center', 3000.0)
    width = params.get('width', 10.0)
    epochs = params.get('epochs', [])
    n_sigma = params.get('n_sigma', 5.0)
    telescope = params.get('telescope', 'parkes')

    if not epochs:
        try:
            from incoherent_stack import get_available_epochs
            epochs = list(get_available_epochs().keys())
        except Exception:
            epochs = []

    if len(epochs) < 2:
        return jsonify({'error': 'Need at least 2 epochs for stacking'}), 400

    # Full band: use the overlap range across epochs
    if width == 0 or width is None:
        freq_center = 3034.0
        width = 580.0

    job_id = str(_uuid.uuid4())[:8]

    # Output paths
    plot_path = os.path.join(STACK_OUTPUT_DIR, f'stack_{job_id}.png')
    json_path = os.path.join(STACK_OUTPUT_DIR, f'stack_{job_id}.json')

    # Job state
    job_state = {
        'job_id': job_id,
        'status': 'running',
        'progress': 0,
        'progress_msg': 'Initializing...',
        'result': None,
        'target': target,
        'freq_center': freq_center,
        'width': width,
        'epochs': epochs,
        'n_sigma': n_sigma,
    }
    _stack_jobs[job_id] = job_state

    # Persist to DB immediately so the job survives dashboard restarts
    try:
        from db import get_db
        conn = get_db()
        conn.execute('''
            INSERT INTO stack_jobs
            (job_id, target, freq_center, width_mhz, epochs, n_epochs,
             n_sigma, status, progress, progress_msg)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'running', 0, 'Initializing...')
        ''', (
            job_id, target, freq_center, width,
            json.dumps(epochs), len(epochs), n_sigma,
        ))
        conn.commit()
        conn.close()
    except Exception as db_err:
        print(f"  Stack DB insert error: {db_err}")

    # Progress callback
    def progress_cb(status):
        phase = status.get('phase', '')
        if phase == 'start':
            job_state['progress'] = 5
            job_state['progress_msg'] = 'Starting stack job...'
        elif phase == 'chunk_start':
            ci = status.get('chunk_index', 0)
            tc = status.get('total_chunks', 1)
            cc = status.get('chunk_center', 0)
            cw = status.get('chunk_width', 0)
            job_state['progress'] = 5 + int(85 * ci / tc)
            job_state['progress_msg'] = f"Chunk {ci+1}/{tc} ({cc:.0f} MHz, {cw:.0f} MHz wide)..."
        elif phase == 'chunk_skipped':
            ci = status.get('chunk_index', 0)
            tc = status.get('total_chunks', 1)
            job_state['progress'] = 5 + int(85 * (ci + 1) / tc)
            job_state['progress_msg'] = f"Chunk {ci+1}/{tc} already done (resuming)..."
        elif phase == 'chunk_done':
            ci = status.get('chunk_index', 0)
            tc = status.get('total_chunks', 1)
            np = status.get('n_peaks', 0)
            job_state['progress'] = 5 + int(85 * (ci + 1) / tc)
            job_state['progress_msg'] = f"Chunk {ci+1}/{tc} done ({np} peaks)."
        elif phase == 'epoch_start':
            idx = status.get('epoch_index', 0)
            total = status.get('total_epochs', 1)
            label = status.get('epoch_label', '?')
            ci = status.get('chunk_index', 0)
            tc = status.get('total_chunks', 1)
            # Within a chunk, epochs take up the chunk's progress slice
            chunk_base = 5 + int(85 * ci / tc)
            chunk_span = int(85 / tc)
            job_state['progress'] = chunk_base + int(chunk_span * idx / total)
            job_state['progress_msg'] = f"Chunk {ci+1}/{tc}, Epoch {label} ({idx+1}/{total}): loading..."
        elif phase == 'file_load':
            fname = status.get('file', status.get('filename', '?'))
            ftype = status.get('type', status.get('file_type', ''))
            ci = status.get('chunk_index', 0)
            tc = status.get('total_chunks', 1)
            job_state['progress_msg'] = f"Chunk {ci+1}/{tc}: Loading {ftype} {fname}..."
        elif phase == 'epoch_done':
            idx = status.get('epoch_index', 0)
            total = status.get('total_epochs', 1)
            label = status.get('epoch_label', '?')
            ci = status.get('chunk_index', 0)
            tc = status.get('total_chunks', 1)
            chunk_base = 5 + int(85 * ci / tc)
            chunk_span = int(85 / tc)
            job_state['progress'] = chunk_base + int(chunk_span * (idx + 1) / total)
            job_state['progress_msg'] = f"Chunk {ci+1}/{tc}, Epoch {label} done."
        elif phase == 'stacking':
            job_state['progress'] = 90
            job_state['progress_msg'] = 'Stacking epochs...'
        elif phase == 'peak_finding':
            job_state['progress'] = 93
            job_state['progress_msg'] = 'Finding peaks...'
        elif phase == 'plotting':
            job_state['progress'] = 97
            job_state['progress_msg'] = 'Generating plot...'
        elif phase == 'complete':
            job_state['progress'] = 100
            job_state['progress_msg'] = 'Complete.'
        elif phase == 'error':
            job_state['status'] = 'error'
            job_state['progress_msg'] = status.get('message') or 'Unknown error'

    # Run in background thread
    def run_thread():
        try:
            # Use chunked processing for wide windows (>50 MHz), simple for narrow
            use_chunked = width > 50
            if use_chunked:
                from incoherent_stack import run_stack_job_chunked
                chunk_dir = os.path.join(STACK_OUTPUT_DIR, f'chunks_{job_id}')
                os.makedirs(chunk_dir, exist_ok=True)
                result = run_stack_job_chunked({
                    'target': target,
                    'freq_center': freq_center,
                    'width': width,
                    'epochs': epochs,
                    'n_sigma': n_sigma,
                    'telescope': telescope,
                    'output_png': plot_path,
                    'output_json': json_path,
                    'output_dir': chunk_dir,
                    'chunk_size_mhz': 50.0,
                }, progress_callback=progress_cb)
            else:
                from incoherent_stack import run_stack_job
                result = run_stack_job({
                    'target': target,
                    'freq_center': freq_center,
                    'width': width,
                    'epochs': epochs,
                    'n_sigma': n_sigma,
                    'telescope': telescope,
                    'output_png': plot_path,
                    'output_json': json_path,
                }, progress_callback=progress_cb)

            job_state['result'] = result
            if result.get('success'):
                job_state['status'] = 'complete'
            else:
                job_state['status'] = 'error'
                job_state['progress_msg'] = result.get('error') or 'Unknown error'

            # Spectrum artifacts (_grid.npy/_power.npy + meta) are now
            # saved by incoherent_stack itself for both narrow and chunked
            # runs; no dashboard-side npz write needed anymore.

            # Update DB row with final results
            try:
                from db import get_db
                conn = get_db()
                conn.execute('''
                    UPDATE stack_jobs SET
                    status = ?, progress = ?, progress_msg = ?,
                    peaks_json = ?, plot_path = ?,
                    stack_median = ?, stack_sigma = ?,
                    snr_improvement = ?, epoch_info_json = ?,
                    completed_at = datetime('now')
                    WHERE job_id = ?
                ''', (
                    job_state['status'], job_state['progress'],
                    job_state['progress_msg'],
                    json.dumps(result.get('peaks', [])[:200]),
                    plot_path if os.path.isfile(plot_path) else None,
                    result.get('stack_median'), result.get('stack_sigma'),
                    result.get('snr_improvement'),
                    json.dumps(result.get('epoch_info', [])),
                    job_id,
                ))
                conn.commit()
                conn.close()
            except Exception as db_err:
                print(f"  Stack DB persist error: {db_err}")

        except Exception as e:
            job_state['status'] = 'error'
            # str(MemoryError()) is '' - keep the UI informative
            job_state['progress_msg'] = str(e) or type(e).__name__
            import traceback
            traceback.print_exc()
            # Persist the failure so History reflects it instead of
            # showing the job as 'running' forever
            try:
                from db import get_db
                conn = get_db()
                conn.execute(
                    "UPDATE stack_jobs SET status = 'error', progress = ?, "
                    "progress_msg = ? WHERE job_id = ?",
                    (job_state['progress'], job_state['progress_msg'], job_id))
                conn.commit()
                conn.close()
            except Exception:
                pass

    thread = _threading.Thread(target=run_thread, daemon=True)
    job_state['thread'] = thread
    thread.start()

    return jsonify({
        'job_id': job_id,
        'status': 'running',
        'target': target,
        'freq_center': freq_center,
        'width': width,
        'epochs': epochs,
        'n_sigma': n_sigma,
    })


@app.route('/api/stack/resume/<job_id>', methods=['POST'])
def api_stack_resume(job_id):
    """Resume an interrupted chunked stack job.

    Checks for existing chunk files and continues from where it left off.
    """
    # Load original job params from DB
    try:
        from db import get_db
        conn = get_db()
        row = conn.execute(
            'SELECT * FROM stack_jobs WHERE job_id = ?', (job_id,)).fetchone()
        conn.close()
        if not row:
            return jsonify({'error': 'Job not found'}), 404
        if row['status'] == 'complete':
            return jsonify({'error': 'Job already complete'}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    target = row['target']
    freq_center = row['freq_center']
    width = row['width_mhz']
    epochs = json.loads(row['epochs'] or '[]')
    n_sigma = row['n_sigma']

    # New job state
    new_job_id = str(_uuid.uuid4())[:8]
    plot_path = os.path.join(STACK_OUTPUT_DIR, f'stack_{new_job_id}.png')
    json_path = os.path.join(STACK_OUTPUT_DIR, f'stack_{new_job_id}.json')
    # Reuse the same chunk dir so existing chunks are found
    chunk_dir = os.path.join(STACK_OUTPUT_DIR, f'chunks_{job_id}')

    # Mark the old job as superseded so the Resume button disappears
    try:
        from db import get_db
        conn = get_db()
        conn.execute(
            "UPDATE stack_jobs SET status = 'superseded', progress_msg = ? WHERE job_id = ?",
            ('Resumed as ' + new_job_id, job_id,))
        conn.commit()
        conn.close()
    except Exception:
        pass

    job_state = {
        'job_id': new_job_id,
        'status': 'running',
        'progress': 0,
        'progress_msg': 'Resuming stack job...',
        'result': None,
        'target': target,
        'freq_center': freq_center,
        'width': width,
        'epochs': epochs,
        'n_sigma': n_sigma,
    }
    _stack_jobs[new_job_id] = job_state

    # Reuse the same progress callback logic
    def progress_cb(status):
        phase = status.get('phase', '')
        if phase == 'start':
            job_state['progress'] = 5
            job_state['progress_msg'] = 'Resuming stack job...'
        elif phase == 'chunk_start':
            ci = status.get('chunk_index', 0)
            tc = status.get('total_chunks', 1)
            cc = status.get('chunk_center', 0)
            cw = status.get('chunk_width', 0)
            job_state['progress'] = 5 + int(85 * ci / tc)
            job_state['progress_msg'] = f"Chunk {ci+1}/{tc} ({cc:.0f} MHz, {cw:.0f} MHz wide)..."
        elif phase == 'chunk_skipped':
            ci = status.get('chunk_index', 0)
            tc = status.get('total_chunks', 1)
            job_state['progress'] = 5 + int(85 * (ci + 1) / tc)
            job_state['progress_msg'] = f"Chunk {ci+1}/{tc} already done (resuming)..."
        elif phase == 'chunk_done':
            ci = status.get('chunk_index', 0)
            tc = status.get('total_chunks', 1)
            np_ = status.get('n_peaks', 0)
            job_state['progress'] = 5 + int(85 * (ci + 1) / tc)
            job_state['progress_msg'] = f"Chunk {ci+1}/{tc} done ({np_} peaks)."
        elif phase == 'epoch_start':
            idx = status.get('epoch_index', 0)
            total = status.get('total_epochs', 1)
            label = status.get('epoch_label', '?')
            ci = status.get('chunk_index', 0)
            tc = status.get('total_chunks', 1)
            chunk_base = 5 + int(85 * ci / tc)
            chunk_span = int(85 / tc)
            job_state['progress'] = chunk_base + int(chunk_span * idx / total)
            job_state['progress_msg'] = f"Chunk {ci+1}/{tc}, Epoch {label} ({idx+1}/{total}): loading..."
        elif phase == 'file_load':
            fname = status.get('file', status.get('filename', '?'))
            ftype = status.get('type', status.get('file_type', ''))
            ci = status.get('chunk_index', 0)
            tc = status.get('total_chunks', 1)
            job_state['progress_msg'] = f"Chunk {ci+1}/{tc}: Loading {ftype} {fname}..."
        elif phase == 'epoch_done':
            idx = status.get('epoch_index', 0)
            total = status.get('total_epochs', 1)
            label = status.get('epoch_label', '?')
            ci = status.get('chunk_index', 0)
            tc = status.get('total_chunks', 1)
            chunk_base = 5 + int(85 * ci / tc)
            chunk_span = int(85 / tc)
            job_state['progress'] = chunk_base + int(chunk_span * (idx + 1) / total)
            job_state['progress_msg'] = f"Chunk {ci+1}/{tc}, Epoch {label} done."
        elif phase == 'stacking':
            job_state['progress'] = 90
            job_state['progress_msg'] = 'Stacking epochs...'
        elif phase == 'peak_finding':
            job_state['progress'] = 93
            job_state['progress_msg'] = 'Finding peaks...'
        elif phase == 'plotting':
            job_state['progress'] = 97
            job_state['progress_msg'] = 'Generating plot...'
        elif phase == 'complete':
            job_state['progress'] = 100
            job_state['progress_msg'] = 'Complete.'
        elif phase == 'error':
            job_state['status'] = 'error'
            job_state['progress_msg'] = status.get('message') or 'Unknown error'

    def run_thread():
        try:
            from incoherent_stack import run_stack_job_chunked
            result = run_stack_job_chunked({
                'target': target,
                'freq_center': freq_center,
                'width': width,
                'epochs': epochs,
                'n_sigma': n_sigma,
                'telescope': 'parkes',
                'output_png': plot_path,
                'output_json': json_path,
                'output_dir': chunk_dir,
                'chunk_size_mhz': 50.0,
            }, progress_callback=progress_cb)

            job_state['result'] = result
            if result.get('success'):
                job_state['status'] = 'complete'
            else:
                job_state['status'] = 'error'
                job_state['progress_msg'] = result.get('error') or 'Unknown error'

            try:
                from db import get_db
                conn = get_db()
                conn.execute('''
                    INSERT INTO stack_jobs
                    (job_id, target, freq_center, width_mhz, epochs, n_epochs,
                     n_sigma, status, progress, progress_msg, peaks_json,
                     plot_path, stack_median, stack_sigma, snr_improvement,
                     epoch_info_json, completed_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                ''', (
                    new_job_id, target, freq_center, width,
                    json.dumps(epochs), len(epochs), n_sigma,
                    job_state['status'], job_state['progress'],
                    job_state['progress_msg'],
                    json.dumps(result.get('peaks', [])[:200]),
                    plot_path if os.path.isfile(plot_path) else None,
                    result.get('stack_median'), result.get('stack_sigma'),
                    result.get('snr_improvement'),
                    json.dumps(result.get('epoch_info', [])),
                ))
                conn.commit()

                # Mark the original interrupted job as superseded
                if result.get('success'):
                    try:
                        conn.execute(
                            "UPDATE stack_jobs SET status = 'superseded', "
                            "progress_msg = 'Resumed as ' || ? WHERE job_id = ?",
                            (new_job_id, job_id))
                        conn.commit()
                    except Exception:
                        pass

                conn.close()
            except Exception as db_err:
                print(f"  Stack DB persist error: {db_err}")

        except Exception as e:
            job_state['status'] = 'error'
            # str(MemoryError()) is '' - keep the UI informative
            job_state['progress_msg'] = str(e) or type(e).__name__
            import traceback
            traceback.print_exc()
            # Persist the failure. The new job row is only inserted on
            # success, so if that never ran, restore the ORIGINAL job to
            # 'error' so its Resume button survives (it owns the chunk dir).
            try:
                from db import get_db
                conn = get_db()
                cur = conn.execute(
                    "UPDATE stack_jobs SET status = 'error', progress = ?, "
                    "progress_msg = ? WHERE job_id = ?",
                    (job_state['progress'], job_state['progress_msg'],
                     new_job_id))
                if cur.rowcount == 0:
                    conn.execute(
                        "UPDATE stack_jobs SET status = 'error', "
                        "progress_msg = ? WHERE job_id = ?",
                        ('Resume failed: ' + job_state['progress_msg'], job_id))
                conn.commit()
                conn.close()
            except Exception:
                pass

    thread = _threading.Thread(target=run_thread, daemon=True)
    job_state['thread'] = thread
    thread.start()

    return jsonify({
        'job_id': new_job_id,
        'original_job_id': job_id,
        'status': 'running',
        'target': target,
        'freq_center': freq_center,
        'width': width,
        'epochs': epochs,
        'n_sigma': n_sigma,
    })


@app.route('/api/stack/status/<job_id>')
def api_stack_status(job_id):
    """Poll stack job progress."""
    job = _stack_jobs.get(job_id)
    if not job:
        # Try loading from DB
        try:
            from db import get_db
            conn = get_db()
            row = conn.execute(
                'SELECT * FROM stack_jobs WHERE job_id = ?', (job_id,)).fetchone()
            conn.close()
            if row:
                return jsonify({
                    'job_id': job_id,
                    'status': row['status'],
                    'progress': row['progress'],
                    'progress_msg': row['progress_msg'],
                    'target': row['target'],
                    'freq_center': row['freq_center'],
                    'width': row['width_mhz'],
                    'n_epochs': row['n_epochs'],
                    'n_peaks': len(json.loads(row['peaks_json'] or '[]')),
                })
        except Exception:
            pass
        return jsonify({'error': 'Job not found'}), 404

    resp = {
        'job_id': job_id,
        'status': job['status'],
        'progress': job['progress'],
        'progress_msg': job['progress_msg'],
        'target': job.get('target'),
        'freq_center': job.get('freq_center'),
        'width': job.get('width'),
        'epochs': job.get('epochs'),
    }

    if job['status'] == 'complete' and job.get('result'):
        r = job['result']
        resp.update({
            'n_peaks': len(r.get('peaks', [])),
            'n_epochs': r.get('n_epochs'),
            'snr_improvement': r.get('snr_improvement'),
            'stack_median': r.get('stack_median'),
            'stack_sigma': r.get('stack_sigma'),
            'peaks': r.get('peaks', [])[:50],
            'epoch_info': r.get('epoch_info', []),
        })
    elif job['status'] == 'error':
        resp['error'] = job.get('progress_msg', 'Unknown error')

    return jsonify(resp)


@app.route('/api/stack/results/<job_id>')
def api_stack_results(job_id):
    """Get full results for a completed stack job.

    Checks in-memory job tracker first, then falls back to SQLite DB
    so results survive dashboard restarts.
    """
    # Try in-memory first (for freshly completed jobs)
    job = _stack_jobs.get(job_id)
    if job and job['status'] == 'complete' and job.get('result'):
        r = job['result']
        # Chunked runs return empty grid/power lists by design (spectra live
        # on disk as .npy). Emitting "[]" here makes the frontend treat them
        # as renderable data (empty arrays are truthy in JS), producing a blank
        # Plotly chart with only the sigma-threshold line visible. Only include
        # the arrays when they actually carry data.
        grid_freqs = r.get('grid_freqs') or []
        stack_power = r.get('stack_power') or []
        resp = {
            'job_id': job_id,
            'success': True,
            'target': r.get('target'),
            'freq_center_mhz': r.get('freq_center_mhz'),
            'width_mhz': r.get('width_mhz'),
            'n_epochs': r.get('n_epochs'),
            'used_epochs': r.get('used_epochs'),
            'snr_improvement': r.get('snr_improvement'),
            'stack_median': r.get('stack_median'),
            'stack_sigma': r.get('stack_sigma'),
            'peaks': r.get('peaks', []),
            'epoch_info': r.get('epoch_info', []),
            'grid_n_bins': r.get('grid_n_bins'),
            'has_spectrum': True,
        }
        if grid_freqs and stack_power:
            resp['grid_freqs'] = grid_freqs
            resp['stack_power'] = stack_power
        return jsonify(resp)

    # Fall back to SQLite DB (for jobs from previous dashboard runs)
    try:
        from db import get_db
        conn = get_db()
        row = conn.execute(
            'SELECT * FROM stack_jobs WHERE job_id = ?', (job_id,)).fetchone()
        conn.close()
        if row and row['status'] == 'complete':
            peaks = json.loads(row['peaks_json'] or '[]')
            epochs = json.loads(row['epochs'] or '[]')
            # Load epoch_info from DB if available, otherwise reconstruct labels only
            epoch_info = []
            try:
                epoch_info = json.loads(row['epoch_info_json'] or '[]')
            except (KeyError, TypeError, json.JSONDecodeError):
                pass
            if not epoch_info:
                epoch_info = [{'label': e} for e in epochs]
            return jsonify({
                'job_id': job_id,
                'success': True,
                'target': row['target'],
                'freq_center_mhz': row['freq_center'],
                'width_mhz': row['width_mhz'],
                'n_epochs': row['n_epochs'],
                'used_epochs': epochs,
                'snr_improvement': row['snr_improvement'],
                'stack_median': row['stack_median'],
                'stack_sigma': row['stack_sigma'],
                'peaks': peaks,
                'epoch_info': epoch_info,
                'grid_n_bins': None,
                'has_spectrum': os.path.isfile(os.path.join(STACK_OUTPUT_DIR, f'stack_{job_id}_grid.npy'))
                                or os.path.isfile(os.path.join(STACK_OUTPUT_DIR, f'stack_{job_id}.npz')),
            })
    except Exception as e:
        pass

    return jsonify({'error': 'Job not found'}), 404


@app.route('/api/stack/plot/<job_id>')
def api_stack_plot(job_id):
    """Serve the plot PNG for a stack job."""
    # Try in-memory job first
    job = _stack_jobs.get(job_id)
    if job:
        plot_path = os.path.join(STACK_OUTPUT_DIR, f'stack_{job_id}.png')
    else:
        # Try DB lookup
        try:
            from db import get_db
            conn = get_db()
            row = conn.execute(
                'SELECT plot_path FROM stack_jobs WHERE job_id = ?', (job_id,)).fetchone()
            conn.close()
            if row and row['plot_path']:
                plot_path = row['plot_path']
            else:
                plot_path = os.path.join(STACK_OUTPUT_DIR, f'stack_{job_id}.png')
        except Exception:
            plot_path = os.path.join(STACK_OUTPUT_DIR, f'stack_{job_id}.png')

    if os.path.isfile(plot_path):
        return send_from_directory(os.path.dirname(plot_path), os.path.basename(plot_path))
    return jsonify({'error': 'Plot not found'}), 404


@app.route('/api/stack/spectrum/<job_id>')
def api_stack_spectrum(job_id):
    """Serve spectrum data (freqs + power) as JSON for Plotly rendering.
    Loads from .npz file on disk so it survives dashboard restarts.
    """
    import numpy as np
    # Try in-memory first
    job = _stack_jobs.get(job_id)
    if job and job.get('result') and job['result'].get('grid_freqs'):
        r = job['result']
        raw_freqs = r.get('grid_freqs', [])
        raw_power = r.get('stack_power', [])
        n_total = len(raw_freqs)
        # Downsample for JSON transport if huge
        max_points = 50000
        if n_total > max_points:
            step = int(np.ceil(n_total / max_points))
            out_freqs = raw_freqs[::step]
            out_power = raw_power[::step]
        else:
            out_freqs = raw_freqs
            out_power = raw_power
        return jsonify({
            'job_id': job_id,
            'grid_freqs': out_freqs,
            'stack_power': out_power,
            'n_bins': n_total,
            'n_rendered': len(out_freqs),
        })

    # Newer runs: mmap-friendly .npy pair written by incoherent_stack
    # (peak RAM during a view = only the ~50k downsampled points)
    grid_npy = os.path.join(STACK_OUTPUT_DIR, f'stack_{job_id}_grid.npy')
    power_npy = os.path.join(STACK_OUTPUT_DIR, f'stack_{job_id}_power.npy')
    if os.path.isfile(grid_npy) and os.path.isfile(power_npy):
        try:
            max_points = 50000
            freqs_mm = np.load(grid_npy, mmap_mode='r')
            power_mm = np.load(power_npy, mmap_mode='r')
            n_total = len(freqs_mm)
            step = int(np.ceil(n_total / max_points))
            out_freqs = freqs_mm[::step]
            out_power = power_mm[::step]
            return jsonify({
                'job_id': job_id,
                'grid_freqs': out_freqs.tolist(),
                # None for NaN bins (RFI-zoned) keeps JSON browser-parseable
                'stack_power': [None if v != v else v for v in out_power.tolist()],
                'n_bins': n_total,
                'n_rendered': len(out_freqs),
            })
        except Exception as e:
            return jsonify({'error': f'Failed to load spectrum: {e}'}), 500

    # Legacy fallback: .npz file on disk (full decompress-load per view)
    npz_path = os.path.join(STACK_OUTPUT_DIR, f'stack_{job_id}.npz')
    if os.path.isfile(npz_path):
        try:
            data = np.load(npz_path)
            freqs = data['grid_freqs']
            power = data['stack_power']
            n_total = len(freqs)
            # Downsample to max 50k points for JSON transport
            # (frontend Plotly downsamples anyway, no reason to ship millions of points)
            max_points = 50000
            if n_total > max_points:
                step = int(np.ceil(n_total / max_points))
                freqs = freqs[::step]
                power = power[::step]
            return jsonify({
                'job_id': job_id,
                'grid_freqs': freqs.tolist(),
                # None for NaN bins (RFI-zoned) keeps JSON browser-parseable
                'stack_power': [None if v != v else v for v in power.tolist()],
                'n_bins': n_total,
                'n_rendered': len(freqs),
            })
        except Exception as e:
            return jsonify({'error': f'Failed to load spectrum: {e}'}), 500

    return jsonify({'error': 'Spectrum data not found'}), 404


@app.route('/api/stack/delete/<job_id>', methods=['DELETE'])
def api_stack_delete(job_id):
    """Delete a stack job from DB and disk."""
    import shutil
    try:
        from db import get_db
        conn = get_db()
        conn.execute('DELETE FROM stack_jobs WHERE job_id = ?', (job_id,))
        conn.commit()
        conn.close()
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    # Remove from in-memory tracker
    if job_id in _stack_jobs:
        del _stack_jobs[job_id]

    # Delete output files
    for ext in ['.png', '.json', '.npz', '_grid.npy', '_power.npy', '_meta.json']:
        p = os.path.join(STACK_OUTPUT_DIR, f'stack_{job_id}{ext}')
        if os.path.isfile(p):
            os.remove(p)

    # Delete chunk directory if it exists
    chunk_dir = os.path.join(STACK_OUTPUT_DIR, f'chunks_{job_id}')
    if os.path.isdir(chunk_dir):
        shutil.rmtree(chunk_dir, ignore_errors=True)

    return jsonify({'success': True, 'job_id': job_id})


@app.route('/api/stack/history')
def api_stack_history():
    """List past stack jobs from SQLite."""
    try:
        from db import get_db
        conn = get_db()
        rows = conn.execute('''
            SELECT job_id, target, freq_center, width_mhz, epochs, n_epochs,
                   n_sigma, status,
                   stack_sigma, snr_improvement, created_at, completed_at
            FROM stack_jobs
            ORDER BY created_at DESC
            LIMIT 50
        ''').fetchall()
        conn.close()
        results = []
        for r in rows:
            results.append({
                'job_id': r['job_id'],
                'target': r['target'],
                'freq_center': r['freq_center'],
                'width_mhz': r['width_mhz'],
                'epochs': json.loads(r['epochs'] or '[]'),
                'n_epochs': r['n_epochs'],
                'n_sigma': r['n_sigma'],
                'status': r['status'],
                'stack_sigma': r['stack_sigma'],
                'snr_improvement': r['snr_improvement'],
                'created_at': r['created_at'],
                'completed_at': r['completed_at'],
            })
        return jsonify({'jobs': results})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ─── API: Stack Peak Classification & Cross-Reference ────────────────

@app.route('/api/stack/peaks/<job_id>/classify')
def api_stack_classify(job_id):
    """Classify stack peaks as Candidate/Possible/RFI.

    Uses peak width, OFF-frame contamination, and multi-epoch presence
    to assign a classification badge.
    """
    try:
        from db import get_db
        conn = get_db()

        # Load the stack job's peaks
        row = conn.execute(
            'SELECT peaks_json, target FROM stack_jobs WHERE job_id = ?', (job_id,)
        ).fetchone()
        if not row:
            conn.close()
            return jsonify({'error': 'Job not found'}), 404

        peaks = json.loads(row['peaks_json'] or '[]')
        if not peaks:
            conn.close()
            return jsonify({'classifications': [], 'summary': {'candidate': 0, 'possible': 0, 'rfi': 0}})

        target = row['target'] or ''

        # Determine scan_ids associated with this target
        scan_rows = conn.execute(
            'SELECT scan_id FROM scans WHERE target = ?', (target,)
        ).fetchall()
        scan_ids = [r['scan_id'] for r in scan_rows]
        n_scans = len(scan_ids)

        # Tolerance for frequency matching: 0.001 MHz (1 kHz) for cross-ref,
        # but wider (0.01 MHz / 10 kHz) for OFF contamination check since
        # we're looking at whether the frequency region is polluted
        off_tol_mhz = 0.01
        on_tol_mhz = 0.005

        # Classify each peak
        classifications = []
        for p in peaks:
            freq = p.get('freq_mhz', 0)
            width = p.get('width_chans', 0)

            # Check OFF contamination: count OFF hits within ±off_tol
            off_count = 0
            off_max_snr = 0
            if scan_ids:
                placeholders = ','.join('?' * len(scan_ids))
                off_row = conn.execute(
                    f"SELECT COUNT(*) as cnt, MAX(snr) as max_snr FROM hits "
                    f"WHERE scan_id IN ({placeholders}) AND on_off = 'OFF' "
                    f"AND barycentric_freq BETWEEN ? AND ?",
                    scan_ids + [freq - off_tol_mhz, freq + off_tol_mhz]
                ).fetchone()
                off_count = off_row['cnt'] if off_row else 0
                off_max_snr = off_row['max_snr'] if off_row and off_row['max_snr'] else 0

            # Check multi-epoch presence: how many distinct scans have ON hits near this freq?
            epoch_presence = 0
            if scan_ids:
                placeholders = ','.join('?' * len(scan_ids))
                presence_row = conn.execute(
                    f"SELECT COUNT(DISTINCT scan_id) as cnt FROM hits "
                    f"WHERE scan_id IN ({placeholders}) AND on_off = 'ON' "
                    f"AND barycentric_freq BETWEEN ? AND ?",
                    scan_ids + [freq - on_tol_mhz, freq + on_tol_mhz]
                ).fetchone()
                epoch_presence = presence_row['cnt'] if presence_row else 0

            # Classification logic
            score = 0
            reasons = []

            # Width scoring
            if 3 <= width <= 10:
                score += 2
                reasons.append('good width')
            elif width == 1 or width == 2:
                score += 1
                reasons.append('narrow (noise spike?)')
            elif width > 10:
                score -= 2
                reasons.append(f'wide ({width} chans)')
            elif width > 20:
                score -= 3
                reasons.append(f'very wide ({width} chans)')

            # OFF contamination scoring
            if off_count > 10 or off_max_snr > 100:
                score -= 3
                reasons.append(f'OFF contamination ({off_count} hits, SNR {off_max_snr:.0f})')
            elif off_count > 0:
                score -= 1
                reasons.append(f'weak OFF presence ({off_count} hits)')
            else:
                score += 2
                reasons.append('no OFF contamination')

            # Multi-epoch presence scoring
            if n_scans > 0:
                presence_ratio = epoch_presence / n_scans
                if presence_ratio >= 0.5:
                    score += 2
                    reasons.append(f'in {epoch_presence}/{n_scans} scans')
                elif epoch_presence >= 1:
                    score += 0
                    reasons.append(f'in {epoch_presence}/{n_scans} scans')
                else:
                    # Only in the stack, not in individual scan hits
                    # Could be below threshold individually
                    score += 1
                    reasons.append('below individual threshold')

            # Assign class
            if score >= 4:
                cls = 'candidate'
            elif score >= 1:
                cls = 'possible'
            else:
                cls = 'rfi'

            classifications.append({
                'freq_mhz': freq,
                'class': cls,
                'score': score,
                'reasons': reasons,
                'off_count': off_count,
                'off_max_snr': round(off_max_snr, 2) if off_max_snr else 0,
                'epoch_presence': epoch_presence,
                'n_scans': n_scans,
            })

        conn.close()

        summary = {
            'candidate': sum(1 for c in classifications if c['class'] == 'candidate'),
            'possible': sum(1 for c in classifications if c['class'] == 'possible'),
            'rfi': sum(1 for c in classifications if c['class'] == 'rfi'),
        }

        return jsonify({'classifications': classifications, 'summary': summary})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/stack/peaks/<job_id>/crossref')
def api_stack_crossref(job_id):
    """Cross-reference stack peaks against cross_epoch_candidates.

    Matches peak frequencies within ±tolerance (default 1 kHz = 0.001 MHz)
    against known cross-epoch candidates from the database.
    """
    try:
        from db import get_db
        conn = get_db()

        # Load the stack job's peaks
        row = conn.execute(
            'SELECT peaks_json FROM stack_jobs WHERE job_id = ?', (job_id,)
        ).fetchone()
        if not row:
            conn.close()
            return jsonify({'error': 'Job not found'}), 404

        peaks = json.loads(row['peaks_json'] or '[]')
        if not peaks:
            conn.close()
            return jsonify({'matches': [], 'n_matched': 0})

        # Load all cross-epoch candidates
        ce_rows = conn.execute(
            'SELECT id, result_json, tolerance_hz, min_epochs, candidate_count, created_at FROM cross_epoch_results'
        ).fetchall()

        # Parse all candidates from all cross-epoch results
        all_candidates = []
        for ce_row in ce_rows:
            try:
                result = json.loads(ce_row['result_json'] or '{}')
                for cand in result.get('candidates', []):
                    all_candidates.append({
                        'freq_mhz': cand.get('barycentric_freq_mhz', 0),
                        'epoch_count': cand.get('epoch_count', 0),
                        'max_snr': cand.get('max_snr', 0),
                        'mean_drift': cand.get('mean_drift_rate', 0),
                        'ce_result_id': ce_row['id'],
                        'tolerance_hz': ce_row['tolerance_hz'],
                        'min_epochs': ce_row['min_epochs'],
                        'created_at': ce_row['created_at'],
                    })
            except (json.JSONDecodeError, TypeError):
                continue

        # Match peaks to candidates
        tolerance_mhz = 0.001  # 1 kHz default tolerance
        matches = []

        for p in peaks:
            freq = p.get('freq_mhz', 0)
            matched_cands = []
            for cand in all_candidates:
                if abs(cand['freq_mhz'] - freq) <= tolerance_mhz:
                    matched_cands.append(cand)

            if matched_cands:
                matches.append({
                    'freq_mhz': freq,
                    'matched': True,
                    'candidates': matched_cands,
                })
            else:
                matches.append({
                    'freq_mhz': freq,
                    'matched': False,
                    'candidates': [],
                })

        conn.close()

        n_matched = sum(1 for m in matches if m['matched'])
        return jsonify({
            'matches': matches,
            'n_matched': n_matched,
            'n_total_candidates': len(all_candidates),
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ─── API: Stacked Peak Waterfall (single epoch vs stacked comparison) ──

@app.route('/api/stack/peaks/<job_id>/stacked_waterfall')
def api_stack_stacked_waterfall(job_id):
    """Return side-by-side waterfalls for a stack peak: single epoch (raw)
    vs stacked average (SNR-boosted).

    Uses hdf5_reader.read_channel_slice() for memory-efficient reads
    (~16 KB per file instead of loading 12 GB via blimpy).

    Query params:
      - freq_mhz: peak frequency in MHz (barycentric)
      - width_chans: half-width in channels (default 200)
      - max_tints: max time integrations per epoch (default 20)
    """
    import numpy as np
    import gc

    freq_mhz = request.args.get('freq_mhz', type=float)
    width_chans = request.args.get('width_chans', default=200, type=int)
    max_tints = request.args.get('max_tints', default=20, type=int)

    if freq_mhz is None:
        return jsonify({'error': 'Missing freq_mhz'}), 400

    # Load the stack job to get target and epochs
    try:
        from db import get_db
        conn = get_db()
        row = conn.execute(
            'SELECT target, epochs FROM stack_jobs WHERE job_id = ?', (job_id,)
        ).fetchone()
        conn.close()
    except Exception:
        row = None
    if not row:
        return jsonify({'error': 'Job not found'}), 404

    target = (row['target'] or 'PROXCEN').upper()
    epochs = json.loads(row['epochs'] or '[]')

    if not epochs:
        return jsonify({'error': 'No epochs in stack job'}), 400

    # Get epoch definitions and coordinates
    sys.path.insert(0, SETI_ROOT)
    sys.path.insert(0, os.path.join(SETI_ROOT, 'src'))
    from incoherent_stack import EPOCHS, find_h5
    from barycentric_correct import (compute_barycentric_velocity,
                                     extract_mjd_from_filename,
                                     resolve_target_coords)
    from hdf5_reader import read_channel_slice, freq_to_chan, get_header

    target_ra, target_dec, _src = resolve_target_coords(target)
    if target_ra is None:
        return jsonify({'error':
                        f'No coordinates for target "{target}"'}), 400

    chan_width_mhz = 2.7939677e-6  # Parkes fine-res channel width

    # Build common barycentric grid for the narrow window
    target_chans = 2 * width_chans
    f_min_bary = freq_mhz - width_chans * chan_width_mhz
    f_max_bary = freq_mhz + width_chans * chan_width_mhz
    common_grid = np.linspace(f_min_bary, f_max_bary, target_chans)

    epoch_spectra_2d = []
    epoch_labels = []
    tsamp = 18.25

    for ep_label in epochs:
        ep_def = EPOCHS.get(ep_label)
        if not ep_def:
            continue

        mjd_int = ep_def['mjd_int']
        seqs = ep_def['seqs']

        # Barycentric correction for this epoch
        first_on = f"Parkes_{mjd_int}_{seqs[0][0]}_{target}_S_fine.h5"
        mjd = extract_mjd_from_filename(first_on)
        v_bary = compute_barycentric_velocity(mjd, target_ra, target_dec, 'parkes')
        c = 299792458.0
        corr = 1.0 - v_bary / c

        # Load ON and OFF using the efficient hdf5_reader
        on_seq, off_seq = seqs[0]
        on_file = f"Parkes_{mjd_int}_{on_seq}_{target}_S_fine.h5"
        off_file = f"Parkes_{mjd_int}_{off_seq}_{target}_R_fine.h5"
        on_path = find_h5(on_file)
        off_path = find_h5(off_file)
        if not on_path or not off_path:
            continue

        try:
            # Read header for tsamp and channel mapping
            hdr = get_header(on_path)
            tsamp = float(hdr.get('tsamp', 18.25))

            # Convert barycentric freq to observed freq for channel lookup
            # f_obs = f_bary / corr
            obs_freq = freq_mhz / corr
            center_chan = freq_to_chan(obs_freq, h5_path=on_path)

            # Read ON slice: (n_tints, 2*width_chans)
            on_data = read_channel_slice(on_path, center_chan, half_width=width_chans)
            # Read OFF slice
            off_data = read_channel_slice(off_path, center_chan, half_width=width_chans)
        except Exception as e:
            print(f"  Error loading {on_file}: {e}")
            continue

        # Align time dimensions
        n_t = min(on_data.shape[0], off_data.shape[0])
        on_data = on_data[:n_t]
        off_data = off_data[:n_t]

        # Subtract OFF from ON (kills steady RFI)
        residual = on_data - off_data
        del on_data, off_data

        # Build observed frequency axis for the loaded channels
        fch1 = float(hdr.get('fch1', 0))
        foff = float(hdr.get('foff', chan_width_mhz))
        chan_start = center_chan - width_chans
        obs_freqs = np.array([fch1 + (chan_start + i) * foff for i in range(2 * width_chans)])

        # Apply barycentric correction
        bary_freqs = obs_freqs * corr

        # Interpolate each time row onto common barycentric grid
        sort_idx = np.argsort(bary_freqs)
        bary_sorted = bary_freqs[sort_idx]

        interp_2d = np.zeros((n_t, target_chans), dtype=np.float32)
        for t_idx in range(n_t):
            row_sorted = residual[t_idx, sort_idx]
            interp_2d[t_idx] = np.interp(common_grid, bary_sorted, row_sorted)

        del residual
        gc.collect()

        # Limit time integrations
        if interp_2d.shape[0] > max_tints:
            indices = np.linspace(0, interp_2d.shape[0] - 1, max_tints, dtype=int)
            interp_2d = interp_2d[indices]

        epoch_spectra_2d.append(interp_2d)
        epoch_labels.append(ep_label)

    if not epoch_spectra_2d:
        return jsonify({'error': 'No epoch data could be loaded'}), 500

    # Align all epochs to the same number of time bins
    min_tints = min(s.shape[0] for s in epoch_spectra_2d)
    for i in range(len(epoch_spectra_2d)):
        if epoch_spectra_2d[i].shape[0] > min_tints:
            indices = np.linspace(0, epoch_spectra_2d[i].shape[0] - 1, min_tints, dtype=int)
            epoch_spectra_2d[i] = epoch_spectra_2d[i][indices]

    # Build single-epoch view (first epoch, raw)
    single = epoch_spectra_2d[0]

    # Build stacked average (the SNR-boosted view)
    stacked = np.mean(epoch_spectra_2d, axis=0)

    # Convert both to dB above median for visualization
    # ON-OFF residuals can be negative, so we shift to positive before log
    def to_db(arr):
        med = np.median(arr)
        if med == 0:
            med = np.median(np.abs(arr)) or 1.0
        shifted = arr - med  # center on zero
        # Use symmetric dynamic range: map to dB relative to median
        # Positive values = signal above noise floor
        # Negative values = noise dips
        abs_shifted = np.abs(shifted)
        db = np.sign(shifted) * 10.0 * np.log10(np.maximum(abs_shifted, 1e-6) / (np.median(abs_shifted) or 1.0))
        return db

    single_db = to_db(single)
    stacked_db = to_db(stacked)

    times = (np.arange(min_tints, dtype=np.float64) * tsamp).tolist()
    freqs = common_grid.tolist()

    return jsonify({
        'single_epoch': {
            'label': epoch_labels[0],
            'data': np.nan_to_num(single_db, nan=0.0).tolist(),
        },
        'stacked': {
            'label': f'Stacked ({len(epoch_spectra_2d)} epochs)',
            'data': np.nan_to_num(stacked_db, nan=0.0).tolist(),
        },
        'all_epochs': [
            {'label': epoch_labels[i], 'data': np.nan_to_num(to_db(epoch_spectra_2d[i]), nan=0.0).tolist()}
            for i in range(len(epoch_spectra_2d))
        ],
        'freqs': freqs,
        'times': times,
        'n_chans': target_chans,
        'n_tints': min_tints,
        'center_freq_mhz': freq_mhz,
    })


# ─── API: Two-Layer Barycentric Filter Pipeline ─────────────────────

_two_layer_jobs = {}  # job_id -> {thread, status, progress, msg, result, ...}
TWO_LAYER_OUTPUT_DIR = os.path.join(SETI_ROOT, 'results', 'two_layer')
os.makedirs(TWO_LAYER_OUTPUT_DIR, exist_ok=True)


@app.route('/api/stack/two-layer', methods=['POST'])
def api_two_layer_run():
    """Start a two-layer SETI filter pipeline job.

    Layer 1: Cross-epoch barycentric filter (find hits at the same
             barycentric-corrected frequency across multiple epochs).
    Layer 2: Targeted incoherent stack on surviving candidates.

    POST body JSON:
        target:       str   (e.g. 'PROXCEN')
        tolerance_hz: float (default 10)
        min_epochs:   int   (default 3)
        min_snr:      float (default 8)
        stack_width:  float (MHz, default 0.05)
        n_sigma:      float (default 5.0)
        epochs:       list of str (optional, default = all available)
    """
    params = request.json or {}
    target = params.get('target', 'PROXCEN')
    tolerance_hz = params.get('tolerance_hz', 10)
    min_epochs = params.get('min_epochs', 3)
    min_snr = params.get('min_snr', 8)
    stack_width = params.get('stack_width', 0.05)
    n_sigma = params.get('n_sigma', 5.0)
    epochs_param = params.get('epochs', None)

    job_id = str(_uuid.uuid4())[:8]

    job_state = {
        'job_id': job_id,
        'status': 'running',
        'progress': 0,
        'progress_msg': 'Initializing...',
        'phase': 'init',
        'result': None,
        'target': target,
        'tolerance_hz': tolerance_hz,
        'min_epochs': min_epochs,
        'min_snr': min_snr,
        'stack_width': stack_width,
        'n_sigma': n_sigma,
        'candidates_found': 0,
    }
    _two_layer_jobs[job_id] = job_state

    def run_thread():
        try:
            # ─── Imports ────────────────────────────────────────────
            sys.path.insert(0, SETI_ROOT)
            sys.path.insert(0, os.path.join(SETI_ROOT, 'src'))
            sys.path.insert(0, os.path.join(SETI_ROOT, 'ml'))

            from barycentric_correct import cross_epoch_match
            from incoherent_stack import EPOCHS
            from ml.two_layer_pipeline import get_scan_dirs, targeted_stack

            # ─── Determine epochs ──────────────────────────────────
            # Only use epochs that have barycentrically-corrected scan data
            # Map scan dirs to epoch labels by reading the combined_corrected.json
            scan_dirs = get_scan_dirs(target)
            available_epoch_labels = []
            for sd in scan_dirs:
                combined_path = os.path.join(sd, 'barycentric', 'combined_corrected.json')
                if os.path.isfile(combined_path):
                    try:
                        with open(combined_path) as _f:
                            _combined = json.load(_f)
                        for _hit in (_combined.get('hits', []) or [])[:50]:
                            _sf = _hit.get('source_file', '')
                            _parts = _sf.split('_')
                            if len(_parts) >= 2:
                                _mjd = _parts[1]
                                if _mjd in EPOCHS and _mjd not in available_epoch_labels:
                                    available_epoch_labels.append(_mjd)
                    except Exception:
                        pass
            available_epoch_labels.sort()
            
            if epochs_param:
                # User specified epochs, intersect with available
                epoch_labels = [e for e in epochs_param if e in available_epoch_labels]
            else:
                epoch_labels = available_epoch_labels

            if len(epoch_labels) < 2:
                job_state['status'] = 'error'
                job_state['progress_msg'] = (
                    f'Need at least 2 barycentrically-corrected epochs, found {len(epoch_labels)}. '
                    f'Available: {available_epoch_labels}'
                )
                return

            # ─── Layer 1: Cross-Epoch Barycentric Filter ───────────
            job_state['phase'] = 'filter_start'
            job_state['progress'] = 5
            job_state['progress_msg'] = f'Layer 1: Filtering {len(scan_dirs)} scans ({len(epoch_labels)} epochs)...'

            t0 = time.time()
            xepoch_result = cross_epoch_match(
                scan_dirs,
                freq_tolerance_hz=tolerance_hz,
                min_epochs=min_epochs,
                min_snr=min_snr,
            )
            elapsed = time.time() - t0

            candidates = xepoch_result.get('candidates', [])
            summary = xepoch_result.get('summary', {})

            job_state['phase'] = 'filter_done'
            job_state['progress'] = 50
            job_state['progress_msg'] = (
                f'Layer 1 complete ({elapsed:.1f}s): '
                f'{len(candidates)} candidates from '
                f'{summary.get("total_on_frequencies", "?")} ON freqs'
            )
            job_state['candidates_found'] = len(candidates)

            # ─── Layer 2: Targeted Stack (if candidates exist) ─────
            if len(candidates) == 0:
                job_state['phase'] = 'complete'
                job_state['progress'] = 100
                job_state['progress_msg'] = 'No candidates survived cross-epoch filter.'
                job_state['status'] = 'complete'

                # Save summary
                output_path = os.path.join(
                    TWO_LAYER_OUTPUT_DIR,
                    f'{target}_tol{tolerance_hz}_ep{min_epochs}_snr{min_snr}.json',
                )
                result_data = {
                    'target': target,
                    'timestamp': time.strftime('%Y-%m-%dT%H:%M:%S'),
                    'layer1': xepoch_result,
                    'layer2': {'results': [], 'n_candidates_stacked': 0},
                    'summary': {
                        'total_on_freqs': summary.get('total_on_frequencies', 0),
                        'candidates_after_layer1': 0,
                        'candidates_after_layer2': 0,
                        'verdict': 'NO_CANDIDATES',
                    },
                }
                with open(output_path, 'w') as f:
                    json.dump(result_data, f, indent=2, default=str)

                # Normalize numpy types for in-memory store
                job_state['result'] = json.loads(json.dumps(result_data, default=str))

                # Persist to database so results survive restarts
                try:
                    from db import get_db
                    conn = get_db()
                    slim_for_db = json.loads(json.dumps(result_data, default=str))
                    conn.execute('''
                        INSERT INTO two_layer_jobs
                        (job_id, target, tolerance_hz, min_epochs, min_snr,
                         stack_width, n_sigma, status, progress, progress_msg,
                         n_candidates, n_stacked, n_with_peaks, verdict,
                         result_json, completed_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, 'complete', 100, ?, ?, ?, ?, ?, ?, datetime('now'))
                    ''', (
                        job_id, target, tolerance_hz, min_epochs, min_snr,
                        stack_width, n_sigma, job_state['progress_msg'],
                        0, 0, 0, 'NO_CANDIDATES',
                        json.dumps(slim_for_db),
                    ))
                    conn.commit()
                    conn.close()
                except Exception as e:
                    print(f'  DB persist error (no-candidate path): {e}')

                return

            # Run targeted stacks on each candidate
            job_state['phase'] = 'stack_start'
            job_state['progress'] = 55
            job_state['progress_msg'] = (
                f'Layer 2: Stacking {len(candidates)} candidates...'
            )

            stack_results = []
            # Inject total_epochs_available into each candidate for Layer 2.5
            total_available = len(epoch_labels)
            for cand in candidates:
                cand['total_epochs_available'] = total_available
            
            for i, cand in enumerate(candidates):
                freq = cand['barycentric_freq_mhz']
                job_state['progress_msg'] = (
                    f'Layer 2: Stacking candidate {i+1}/{len(candidates)} '
                    f'at {freq:.6f} MHz...'
                )
                job_state['progress'] = 55 + int(40 * (i + 1) / len(candidates))

                try:
                    sr = targeted_stack(
                        freq, stack_width, epoch_labels, target=target,
                    )

                    if sr is None:
                        stack_results.append({
                            'candidate': cand,
                            'stack_success': False,
                            'error': 'Insufficient epoch data',
                        })
                        continue

                    n_peaks = len(sr['peaks'])
                    stack_results.append({
                        'candidate': cand,
                        'stack_success': True,
                        'n_epochs': sr['n_epochs'],
                        'median': sr['median'],
                        'sigma': sr['sigma'],
                        'peaks': sr['peaks'],
                        'used_epochs': sr['used_epochs'],
                        'snr_improvement': sr['snr_improvement'],
                        'epoch_info': sr.get('epoch_info', []),
                        'power_concentration': sr.get('power_concentration'),
                        'pulse_periodicity': sr.get('pulse_periodicity'),
                    })
                except Exception as e:
                    stack_results.append({
                        'candidate': cand,
                        'stack_success': False,
                        'error': str(e),
                    })

            job_state['phase'] = 'stack_done'
            job_state['progress'] = 95

            n_stacked = sum(1 for r in stack_results if r.get('stack_success'))
            n_with_peaks = sum(
                1 for r in stack_results
                if r.get('peaks')
            )

            # ─── Layer 2.5: Automated RFI Scorecard ─────────────────
            job_state['phase'] = 'scorecard'
            job_state['progress'] = 97
            job_state['progress_msg'] = 'Running RFI scorecard analysis...'

            layer25_result = None
            try:
                from ml.layer25_analysis import analyze_all_candidates
                layer25_result = analyze_all_candidates(candidates, stack_results)
            except Exception as e:
                print(f'  Layer 2.5 error: {e}')

            verdict = 'CANDIDATES_FOUND' if n_with_peaks > 0 else 'NO_PEAKS_IN_STACK'

            job_state['phase'] = 'complete'
            job_state['progress'] = 100
            job_state['progress_msg'] = (
                f'Pipeline complete: {len(candidates)} layer-1 candidates, '
                f'{n_stacked} stacked, {n_with_peaks} with peaks.'
            )
            job_state['status'] = 'complete'

            # Save results
            output_path = os.path.join(
                TWO_LAYER_OUTPUT_DIR,
                f'{target}_tol{tolerance_hz}_ep{min_epochs}_snr{min_snr}.json',
            )
            result_data = {
                'target': target,
                'timestamp': time.strftime('%Y-%m-%dT%H:%M:%S'),
                'layer1': {
                    'summary': summary,
                    'candidates': candidates,
                },
                'layer2': {
                    'results': stack_results,
                    'n_candidates_stacked': n_stacked,
                },
                'layer25': layer25_result,
                'summary': {
                    'total_on_freqs': summary.get('total_on_frequencies', 0),
                    'candidates_after_layer1': len(candidates),
                    'candidates_after_layer2': n_with_peaks,
                    'verdict': verdict,
                },
            }
            with open(output_path, 'w') as f:
                json.dump(result_data, f, indent=2, default=str)

            # Normalize through JSON round-trip to strip numpy types that
            # break Flask's jsonify() on the results endpoint.
            job_state['result'] = json.loads(json.dumps(result_data, default=str))

            # Persist to database so results survive restarts
            try:
                from db import get_db
                conn = get_db()
                # Strip large arrays from saved JSON
                slim_for_db = json.loads(json.dumps(result_data, default=str))
                conn.execute('''
                    INSERT INTO two_layer_jobs
                    (job_id, target, tolerance_hz, min_epochs, min_snr,
                     stack_width, n_sigma, status, progress, progress_msg,
                     n_candidates, n_stacked, n_with_peaks, verdict,
                     result_json, completed_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'complete', 100, ?, ?, ?, ?, ?, ?, datetime('now'))
                ''', (
                    job_id, target, tolerance_hz, min_epochs, min_snr,
                    stack_width, n_sigma, job_state['progress_msg'],
                    len(candidates), n_stacked, n_with_peaks, verdict,
                    json.dumps(slim_for_db),
                ))
                conn.commit()
                conn.close()
                print(f'  Two-layer job {job_id} saved to DB')
            except Exception as db_err:
                print(f'  Two-layer DB persist error: {db_err}')

        except Exception as e:
            job_state['status'] = 'error'
            # str(MemoryError()) is '' - keep the UI informative
            job_state['progress_msg'] = str(e) or type(e).__name__
            import traceback
            traceback.print_exc()
            # Persist the failure so History reflects it after restarts
            try:
                from db import get_db
                conn = get_db()
                conn.execute(
                    "UPDATE two_layer_jobs SET status = 'error', "
                    "progress = ?, progress_msg = ? WHERE job_id = ?",
                    (job_state['progress'], job_state['progress_msg'], job_id))
                conn.commit()
                conn.close()
            except Exception:
                pass

    thread = _threading.Thread(target=run_thread, daemon=True)
    job_state['thread'] = thread
    thread.start()

    return jsonify({
        'job_id': job_id,
        'status': 'running',
        'target': target,
    })


@app.route('/api/stack/two-layer/active')
def api_two_layer_active():
    """Check for any running two-layer jobs (for page refresh auto-reconnect)."""
    for job_id, job in _two_layer_jobs.items():
        if job.get('status') in ('running', 'pending'):
            return jsonify({
                'job_id': job_id,
                'status': job['status'],
                'progress': job.get('progress', 0),
                'progress_msg': job.get('progress_msg', ''),
                'phase': job.get('phase', ''),
                'target': job.get('target', ''),
                'candidates_found': job.get('candidates_found', 0),
            })
    return jsonify({'job_id': None, 'status': 'none'})


@app.route('/api/stack/two-layer/history')
def api_two_layer_history():
    """List past two-layer pipeline jobs from DB."""
    try:
        from db import get_db
        conn = get_db()
        rows = conn.execute('''
            SELECT job_id, target, tolerance_hz, min_epochs, min_snr,
                   stack_width, n_sigma, status, n_candidates, n_stacked,
                   n_with_peaks, verdict, created_at, completed_at
            FROM two_layer_jobs
            ORDER BY created_at DESC
            LIMIT 50
        ''').fetchall()
        conn.close()
        jobs = []
        for r in rows:
            jobs.append({
                'job_id': r['job_id'],
                'target': r['target'],
                'tolerance_hz': r['tolerance_hz'],
                'min_epochs': r['min_epochs'],
                'min_snr': r['min_snr'],
                'stack_width': r['stack_width'],
                'n_sigma': r['n_sigma'],
                'status': r['status'],
                'n_candidates': r['n_candidates'],
                'n_stacked': r['n_stacked'],
                'n_with_peaks': r['n_with_peaks'],
                'verdict': r['verdict'],
                'created_at': r['created_at'],
                'completed_at': r['completed_at'],
            })
        return jsonify({'jobs': jobs})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/stack/two-layer/delete/<job_id>', methods=['DELETE'])
def api_two_layer_delete(job_id):
    """Delete a two-layer pipeline job from DB."""
    if not re.match(r'^[A-Za-z0-9_-]+$', job_id):
        return jsonify({'error': 'Invalid job_id'}), 400
    try:
        from db import get_db
        conn = get_db()
        conn.execute('DELETE FROM two_layer_jobs WHERE job_id = ?', (job_id,))
        conn.commit()
        conn.close()
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    if job_id in _two_layer_jobs:
        del _two_layer_jobs[job_id]
    return jsonify({'success': True, 'deleted': job_id})


@app.route('/api/stack/two-layer/<job_id>')
def api_two_layer_status(job_id):
    """Poll two-layer pipeline job status."""
    job = _two_layer_jobs.get(job_id)
    if not job:
        return jsonify({'error': 'Job not found'}), 404

    resp = {
        'job_id': job_id,
        'status': job['status'],
        'progress': job['progress'],
        'progress_msg': job['progress_msg'],
        'phase': job.get('phase', ''),
        'target': job.get('target'),
        'candidates_found': job.get('candidates_found', 0),
    }

    if job['status'] == 'error':
        resp['error'] = job.get('progress_msg', 'Unknown error')

    return jsonify(resp)


@app.route('/api/stack/two-layer/<job_id>/results')
def api_two_layer_results(job_id):
    """Get full results for a completed two-layer pipeline job."""
    # Try in-memory first (for freshly completed jobs)
    job = _two_layer_jobs.get(job_id)
    if job:
        if job['status'] != 'complete':
            return jsonify({
                'error': 'Job not complete',
                'status': job['status'],
                'progress': job['progress'],
            }), 400

        full_result = job.get('result', {})
    else:
        # Fall back to DB (for jobs from previous dashboard runs)
        try:
            from db import get_db
            conn = get_db()
            row = conn.execute(
                'SELECT * FROM two_layer_jobs WHERE job_id = ?', (job_id,)).fetchone()
            conn.close()
            if row and row['status'] == 'complete' and row['result_json']:
                full_result = json.loads(row['result_json'])
            else:
                return jsonify({'error': 'Job not found'}), 404
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    # Build slim result (strip large arrays like stack spectra)
    slim_result = {
        'target': full_result.get('target'),
        'timestamp': full_result.get('timestamp'),
        'summary': full_result.get('summary'),
        'layer1': full_result.get('layer1'),
        'layer25': full_result.get('layer25'),
        'layer2': {
            'n_candidates_stacked': full_result.get('layer2', {}).get('n_candidates_stacked', 0),
            'results': [],
        },
    }
    for sr in full_result.get('layer2', {}).get('results', []):
        slim_sr = {k: v for k, v in sr.items() if k != 'stack_result'}
        if 'stack_result' in sr:
            slim_sr['stack_result'] = {
                'freq_center': sr['stack_result'].get('freq_center'),
                'n_epochs': sr['stack_result'].get('n_epochs'),
                'median': sr['stack_result'].get('median'),
                'sigma': sr['stack_result'].get('sigma'),
                'peaks': sr['stack_result'].get('peaks', []),
                'used_epochs': sr['stack_result'].get('used_epochs', []),
                'snr_improvement': sr['stack_result'].get('snr_improvement'),
                'epoch_info': sr['stack_result'].get('epoch_info', []),
                'stack_width_mhz': sr['stack_result'].get('stack_width_mhz'),
            }
        slim_result['layer2']['results'].append(slim_sr)

    return jsonify(slim_result)


# ─── Main ─────────────────────────────────────────────────────────────

# Ensure DB schema is up to date (creates stack_jobs table if missing)
try:
    from db import init_db
    init_db()
    # Phase 3A: ensure the target registry table exists (seeded on first run)
    from target_registry import ensure_table as _ensure_targets
    _ensure_targets()
except Exception as _db_err:
    print(f"  WARNING: DB init error: {_db_err}")

# Orphan recovery moved to __main__ block for reliable execution

if __name__ == '__main__':
    import numpy as np  # needed by header endpoint

    # Orphan recovery: mark any running stack jobs as interrupted
    # (scan subprocess may still be running, but the dashboard lost its handle)
    try:
        from db import get_db
        conn = get_db()
        orphans = conn.execute(
            "SELECT job_id, target FROM stack_jobs WHERE status = 'running'"
        ).fetchall()
        for o in orphans:
            conn.execute(
                "UPDATE stack_jobs SET status = 'interrupted', progress_msg = 'Interrupted by dashboard restart' WHERE job_id = ?",
                (o['job_id'],))
            print(f"  Orphan recovery: stack job {o['job_id']} ({o['target']}) marked as interrupted (auto-resume on next page load)")
        conn.commit()
        conn.close()
    except Exception as _orphan_err:
        print(f"  Orphan recovery error: {_orphan_err}")

    # Orphan recovery: mark any running two-layer jobs as interrupted
    try:
        from db import get_db
        conn = get_db()
        tl_orphans = conn.execute(
            "SELECT job_id FROM two_layer_jobs WHERE status = 'running'"
        ).fetchall()
        for o in tl_orphans:
            conn.execute(
                "UPDATE two_layer_jobs SET status = 'interrupted', progress_msg = 'Interrupted by dashboard restart' WHERE job_id = ?",
                (o['job_id'],))
            print(f"  Orphan recovery: two-layer job {o['job_id']} marked as interrupted")
        conn.commit()
        conn.close()
    except Exception as _tl_orphan_err:
        print(f"  Two-layer orphan recovery error: {_tl_orphan_err}")

    print(f"SETI Dashboard starting on http://localhost:8070")
    print(f"  Data dir: {DATA_DIR}")
    print(f"  Results dir: {RESULTS_DIR}")
    app.run(host='0.0.0.0', port=8070, debug=False, threaded=True)
