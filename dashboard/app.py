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
MID_DIR = os.path.join(DATA_DIR, 'PROXCEN')

# Track running scans
scan_state = {
    'active': False,
    'pid': None,
    'progress': {},
    'log_lines': [],
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
                    target = parts[3]  # e.g. PROXCEN
                    if target not in targets:
                        targets[target] = {'fine': [], 'mid': []}
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
                        targets[target] = {'fine': [], 'mid': []}
                    if target in targets:
                        targets[target]['mid'].append({
                            'name': f,
                            'size_gb': round(os.path.getsize(os.path.join(MID_DIR, f)) / 1e6, 1),
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


# ─── API: Scan Results ────────────────────────────────────────────────

@app.route('/api/results')
def api_results():
    """Return hit results from pipeline output."""
    results = []
    
    # Scan validation results
    val_dir = os.path.join(RESULTS_DIR, 'validation_50mhz')
    if os.path.isdir(val_dir):
        for f in os.listdir(val_dir):
            if f.endswith('.json'):
                with open(os.path.join(val_dir, f)) as fh:
                    data = json.load(fh)
                    if isinstance(data, dict) and 'files' in data:
                        # Combined summary
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
    
    # Build command
    py = r'C:\Users\w4gon\AppData\Local\Programs\Python\Python311\python.exe'
    script = os.path.join(SETI_ROOT, 'src', 'fine_res_pipeline.py')
    
    cmd = [py, script, '--out', os.path.join(RESULTS_DIR, 'dashboard_scan')]
    
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
    
    thread = threading.Thread(target=run_scan, daemon=True)
    thread.start()
    
    return jsonify({'status': 'started', 'pid': scan_state.get('pid')})


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


# ─── API: Statistics ──────────────────────────────────────────────────

@app.route('/api/stats')
def api_stats():
    """Aggregate statistics across all results."""
    total_hits = 0
    on_hits = 0
    off_hits = 0
    top_snr = 0
    top_hit = None
    
    # Parse all result JSONs
    for pattern in ['validation_50mhz/**/*_hits.json', 
                    'validation_50mhz/*_summary.json',
                    'fine_pipeline/**/*_hits.json',
                    'dashboard_scan/**/*_hits.json']:
        for fpath in glob_module.glob(os.path.join(RESULTS_DIR, pattern), recursive=True):
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
    source = params.get('source', 'validation_50mhz')
    
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
    """Get saved rejection results."""
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
