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
from datetime import datetime, timedelta
from pathlib import Path
from flask import Flask, render_template, jsonify, request, send_from_directory, make_response
import numpy as np
from dotenv import load_dotenv

# Add src to path for imports
SETI_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(SETI_ROOT, 'src'))

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


@app.route('/api/targets')
def api_targets():
    """List local data files grouped by target."""
    targets = {}
    
    # Scan fine-res
    if os.path.isdir(FINE_DIR):
        for f in os.listdir(FINE_DIR):
            if f.endswith('.h5'):
                parts = f.split('_')
                if len(parts) >= 4:
                    target = parts[3]
                    if target not in targets:
                        targets[target] = {'fine': [], 'mid': [], 'filterbank': [], 'h5': []}
                    targets[target]['fine'].append({
                        'name': f,
                        'size_gb': round(os.path.getsize(os.path.join(FINE_DIR, f)) / 1e9, 2),
                        'path': f'fine/{f}',
                        'date': mjd_to_date(parts[1]) if len(parts) >= 2 else '',
                    })
    
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
                parts = f.split('_')
                target = parts[3] if len(parts) >= 4 else 'unknown'
                if target not in targets:
                    targets[target] = {'fine': [], 'mid': [], 'filterbank': [], 'h5': []}
                targets[target]['filterbank'].append({
                    'name': f,
                    'size_gb': round(os.path.getsize(os.path.join(FILT_DIR, f)) / 1e9, 2),
                    'path': f'filterbank/{f}',
                    'date': mjd_to_date(parts[1]) if len(parts) >= 2 else '',
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
    
    # Also scan old PROXCEN dir for backwards compat
    if os.path.isdir(PROXCEN_DIR):
        for f in os.listdir(PROXCEN_DIR):
            if f.endswith('.h5'):
                parts = f.split('_')
                if len(parts) >= 4:
                    target = parts[3]
                    if target not in targets:
                        targets[target] = {'fine': [], 'mid': [], 'filterbank': [], 'h5': []}
                    if target in targets and len(targets[target]['mid']) == 0:
                        targets[target]['mid'].append({
                            'name': f,
                            'size_gb': round(os.path.getsize(os.path.join(PROXCEN_DIR, f)) / 1e6, 1),
                            'path': f'PROXCEN/{f}',
                            'date': mjd_to_date(parts[1]) if len(parts) >= 2 else '',
                        })
    
    return jsonify(targets)


@app.route('/api/blsearch')
def api_blsearch():
    """Proxy search to Berkeley SETI open data API."""
    import urllib.request
    import urllib.parse
    
    target = request.args.get('target', '')
    if not target:
        return jsonify({'error': 'No target specified'}), 400
    
    api_url = f'https://seti.berkeley.edu/opendata/api/query-files?target={urllib.parse.quote(target)}'
    
    try:
        req = urllib.request.Request(api_url, headers={'User-Agent': 'BackyardSETI/1.0'})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


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
        elif entry == 'validation_50mhz':
            # Legacy: create a virtual meta if none exists
            scans.append({
                'scan_id': 'validation_50mhz',
                'target': 'PROXCEN',
                'timestamp': '2026-08-06T20:00:00',
                'status': 'complete',
                'parameters': {},
                'stats': {},
                '_dir': entry,
            })
    
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
                    is_on = '_S_' in fname
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


@app.route('/api/scans/create', methods=['POST'])
def api_scans_create():
    """Create a new scan result set. Returns scan_id."""
    params = request.json or {}
    target = params.get('target', 'PROXCEN').upper()
    
    # Generate scan_id: TARGET_YYYY-MM-DD_HHMM
    now = datetime.now()
    scan_id = f"{target}_{now.strftime('%Y-%m-%d_%H%M')}"
    
    # Ensure uniqueness
    scan_dir = os.path.join(RESULTS_DIR, scan_id)
    counter = 1
    while os.path.isdir(scan_dir):
        scan_id = f"{target}_{now.strftime('%Y-%m-%d_%H%M')}_{counter}"
        scan_dir = os.path.join(RESULTS_DIR, scan_id)
        counter += 1
    
    os.makedirs(scan_dir)
    
    meta = {
        'scan_id': scan_id,
        'target': target,
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
            is_on = '_S_' in fname
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

@app.route('/api/scan/status')
def api_scan_status():
    """Get current scan status with structured progress data."""
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
    scan_id = f"{target.upper()}_{now.strftime('%Y-%m-%d_%H%M')}"
    scan_dir = os.path.join(RESULTS_DIR, scan_id)
    # Ensure uniqueness
    counter = 1
    while os.path.isdir(scan_dir):
        scan_id = f"{target.upper()}_{now.strftime('%Y-%m-%d_%H%M')}_{counter}"
        scan_dir = os.path.join(RESULTS_DIR, scan_id)
        counter += 1
    os.makedirs(scan_dir)
    
    # Write scan_meta.json
    scan_meta = {
        'scan_id': scan_id,
        'target': target.upper(),
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
    py = r'C:\Users\w4gon\AppData\Local\Programs\Python\Python311\python.exe'
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
                    if '_S_' in cur_file:
                        scan_state['on_hits'] += n_hits
                    elif '_R_' in cur_file:
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
            scan_state['active_scan_id'] = None
    
    thread = threading.Thread(target=run_scan, daemon=True)
    thread.start()
    
    return jsonify({'status': 'started', 'scan_id': scan_id, 'pid': scan_state.get('pid')})


@app.route('/api/scan/stop', methods=['POST'])
def api_scan_stop():
    """Stop a running scan."""
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
    if scan_state['active']:
        return jsonify({'error': 'Scan already running, stop it first'}), 409

    params = request.json or {}
    scan_id = params.get('scan_id')

    # Find the scan to resume
    if not scan_id:
        # Find the most recent scan that has a checkpoint
        scan_dirs = _discover_scans()
        for sid in reversed(scan_dirs):
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
    py = r'C:\Users\w4gon\AppData\Local\Programs\Python\Python311\python.exe'
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
                    if '_S_' in cur_file:
                        scan_state['on_hits'] += n_hits
                    elif '_R_' in cur_file:
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
    elif filename.endswith('.h5'):
        # HDF5 without resolution marker: route to fine if large, mid if small
        # BL fine-res files are ~15GB, mid-res are ~233MB
        # We can't know size yet, so put in a general h5 dir
        target_dir = os.path.join(DATA_DIR, 'h5')
    else:
        target_dir = DATA_DIR
    
    os.makedirs(target_dir, exist_ok=True)
    target_path = os.path.join(target_dir, filename)
    
    # Check if already downloading
    for item in download_state['queue']:
        if item['filename'] == filename and item['status'] == 'downloading':
            return jsonify({'error': 'Already downloading'}), 409
    
    # Check if file already exists
    if os.path.isfile(target_path):
        size = os.path.getsize(target_path)
        return jsonify({'status': 'exists', 'filename': filename, 'size_bytes': size,
                       'path': os.path.relpath(target_path, SETI_ROOT)})
    
    # Create download tracker
    item = {
        'url': url,
        'filename': filename,
        'target_path': target_path,
        'target_dir': target_dir,
        'status': 'queued',
        'progress': 0.0,
        'speed_mbs': 0.0,
        'eta_s': 0,
        'size_total': 0,
        'size_done': 0,
        'error': None,
    }
    download_state['queue'].append(item)
    
    # Start download in background thread
    def do_download(dl_item):
        import urllib.request
        import time as _time
        
        # Wait if another download is active (serialize downloads)
        while download_state['active'] is not None and download_state['active'] is not dl_item:
            _time.sleep(1)
            if dl_item not in download_state['queue']:
                return  # Cancelled
        
        download_state['active'] = dl_item
        dl_item['status'] = 'downloading'
        
        try:
            req = urllib.request.Request(dl_item['url'], 
                                         headers={'User-Agent': 'BackyardSETI/1.0'})
            with urllib.request.urlopen(req, timeout=60) as resp:
                dl_item['size_total'] = int(resp.headers.get('Content-Length', 0))
                
                with open(dl_item['target_path'], 'wb') as f:
                    done = 0
                    chunk_size = 1024 * 1024  # 1 MB chunks
                    last_time = _time.time()
                    last_done = 0
                    
                    while True:
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
    
    return jsonify({'status': 'queued', 'filename': filename})


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
            is_on = '_S_' in fname
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
                    is_on = '_S_' in os.path.basename(fpath)
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
            is_on = '_S_' in filename or data.get('on_off') == 'ON'
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
            rejected.append(h)
        else:
            h['status'] = 'CANDIDATE'
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
        
        return jsonify({
            'status': 'complete',
            'scan_id': scan_id,
            'files_corrected': len(result['files_corrected']),
            'total_hits': result['total_hits'],
            'corrections': result['corrections'],
            'barycentric_dir': result['barycentric_dir'],
        })
    except Exception as e:
        import traceback
        return jsonify({'error': str(e), 'traceback': traceback.format_exc()[-500:]}), 500


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


@app.route('/api/barycentric/targets')
def api_barycentric_targets():
    """Return known target coordinates for the dropdown."""
    from barycentric_correct import TARGET_COORDS, TELESCOPE_LOCATIONS
    
    targets = []
    for name, (ra, dec) in sorted(TARGET_COORDS.items()):
        targets.append({'name': name, 'ra_hours': ra, 'dec_deg': dec})
    
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
    corrected_scans = []
    for sm in _discover_scans():
        sid = sm.get('scan_id') or sm.get('_dir', '')
        if not sid:
            continue
        scan_dir = os.path.join(RESULTS_DIR, sid)
        combined_path = os.path.join(scan_dir, 'barycentric', 'combined_corrected.json')
        if os.path.isfile(combined_path):
            corrected_scans.append(sid)
    
    return jsonify({'targets': targets, 'telescopes': telescopes, 'corrected_scans': corrected_scans})


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
        from blimpy import Waterfall

        # Read header first to get channel bandwidth
        wf_header = Waterfall(full_path, load_data=False)
        header = wf_header.header

        # Get frequency info
        fch1 = float(header.get('fch1', 0))
        nchans = int(header.get('nchans', 1))
        foff = float(header.get('foff', 0))  # MHz per channel
        tsamp = float(header.get('tsamp', 18.25))  # seconds

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

        # Load the sub-band data
        wf = Waterfall(full_path, load_data=True,
                       f_start=f_start, f_stop=f_stop)
        data = np.array(wf.data, dtype=np.float32)  # shape: (n_tints, 1, n_chans)

        if data.ndim == 3:
            data = data[:, 0, :]  # squeeze IF dimension
        elif data.ndim == 2:
            pass  # already 2D
        elif data.ndim == 1:
            data = data.reshape(1, -1)

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

        # Build frequency axis (MHz) for the loaded sub-band
        # Waterfall object should have freqs available
        try:
            freqs = wf.container.sf_freqs  # MHz
            freqs = np.array(freqs, dtype=np.float64)
        except Exception:
            # Fallback: compute from f_start/f_stop
            freqs = np.linspace(f_start, f_stop, n_chans)

        # Ensure freqs length matches data
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
                    if cached:
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


# ─── Main ─────────────────────────────────────────────────────────────

if __name__ == '__main__':
    import numpy as np  # needed by header endpoint
    print(f"SETI Dashboard starting on http://localhost:8070")
    print(f"  Data dir: {DATA_DIR}")
    print(f"  Results dir: {RESULTS_DIR}")
    app.run(host='0.0.0.0', port=8070, debug=False, threaded=True)
