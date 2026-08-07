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
from datetime import datetime
from pathlib import Path
from flask import Flask, render_template, jsonify, request, send_from_directory
import numpy as np

# Add src to path for imports
SETI_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(SETI_ROOT, 'src'))

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


# ─── API: Target Search ───────────────────────────────────────────────

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
    
    # Try multiple path resolutions
    candidates = [
        os.path.join(SETI_ROOT, filepath),
        os.path.join(SETI_ROOT, 'data', filepath),
        os.path.join(DATA_DIR, filepath),
        filepath,  # absolute path
    ]
    full_path = None
    for c in candidates:
        if os.path.isfile(c):
            full_path = c
            break
    
    if not full_path:
        return jsonify({'error': f'File not found: tried {candidates}'}), 404
    
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
    """Get current scan status."""
    return jsonify({
        'active': scan_state['active'],
        'scan_id': scan_state.get('active_scan_id'),
        'progress': scan_state['progress'],
        'log_tail': scan_state['log_lines'][-50:],
    })


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
    
    # Build command - output to the scan directory
    py = r'C:\Users\w4gon\AppData\Local\Programs\Python\Python311\python.exe'
    script = os.path.join(SETI_ROOT, 'src', 'fine_res_pipeline.py')
    
    cmd = [py, script, '--out', scan_dir]
    
    if files_list and len(files_list) > 0:
        # Scan specific files instead of entire data-dir
        for fpath in files_list:
            # Resolve each file path
            candidates = [
                os.path.join(SETI_ROOT, fpath),
                os.path.join(SETI_ROOT, 'data', fpath),
                os.path.join(DATA_DIR, fpath),
            ]
            resolved = None
            for c in candidates:
                if os.path.isfile(c):
                    resolved = c
                    break
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
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                cwd=SETI_ROOT, text=True, bufsize=1,
            )
            scan_state['pid'] = proc.pid
            for line in proc.stdout:
                scan_state['log_lines'].append(line.rstrip())
                # Parse progress
                if '->' in line and 'hits' in line:
                    scan_state['progress']['last_line'] = line.strip()
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


# ─── API: Delete File ─────────────────────────────────────────────────

@app.route('/api/delete', methods=['POST'])
def api_delete():
    """Delete a local data file."""
    params = request.json or {}
    filepath = params.get('path', '')
    if not filepath:
        return jsonify({'error': 'No path specified'}), 400
    
    # Resolve path safely
    candidates = [
        os.path.join(SETI_ROOT, filepath),
        os.path.join(SETI_ROOT, 'data', filepath),
        os.path.join(DATA_DIR, filepath),
    ]
    full_path = None
    for c in candidates:
        # Make sure resolved path is under DATA_DIR for safety
        real_c = os.path.realpath(c)
        real_data = os.path.realpath(DATA_DIR)
        if os.path.isfile(real_c) and real_c.startswith(real_data):
            full_path = real_c
            break
    
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
            'candidates': candidates[:1000],  # Top 1000 by SNR
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
        'candidates': candidates[:200],  # Top 200 for dashboard display
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


# ─── Main ─────────────────────────────────────────────────────────────

if __name__ == '__main__':
    import numpy as np  # needed by header endpoint
    print(f"SETI Dashboard starting on http://localhost:8070")
    print(f"  Data dir: {DATA_DIR}")
    print(f"  Results dir: {RESULTS_DIR}")
    app.run(host='0.0.0.0', port=8070, debug=False, threaded=True)
