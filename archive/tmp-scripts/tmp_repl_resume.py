"""Replicate the resume handler's request path to surface the 500's
traceback (dashboard stdout is lost in the hidden window)."""
import json
import os
import sys
import traceback

sys.path.insert(0, r'G:\seti\dashboard')
sys.path.insert(0, r'G:\seti\src')

import app as dash  # noqa: E402  (module-level import runs config only)

try:
    scan_dirs = dash._discover_scans()
    print('discover ok:', len(scan_dirs), 'scans')
    scan_id = None
    for sid in reversed(scan_dirs):
        cp = os.path.join(dash._get_scan_dir(sid), 'checkpoint.json')
        if os.path.isfile(cp):
            scan_id = sid
            break
    print('resume target:', scan_id)
    scan_dir = dash._get_scan_dir(scan_id)
    print('scan_dir:', scan_dir)
    with open(os.path.join(scan_dir, 'checkpoint.json')) as f:
        cp = json.load(f)
    print('checkpoint ok:', cp['file_name'], cp['sub_band_index'])
    meta = dash._load_scan_meta(scan_dir) or {}
    print('meta ok:', meta.get('scan_id'), meta.get('status'))
    orig = meta.get('parameters', {})
    print('params keys:', sorted(orig.keys()))
    for fpath in orig.get('files', []):
        r = dash._resolve_data_file(fpath)
        print('  resolve', fpath, '->', r)
    cmd = [sys.executable,
           os.path.join(dash.SETI_ROOT, 'src', 'fine_res_pipeline.py'),
           '--out', scan_dir, '--resume']
    for fpath in orig.get('files', []):
        resolved = dash._resolve_data_file(fpath)
        if resolved:
            cmd.extend(['--file', resolved])
    cmd.extend(['--sub-band-width', str(orig.get('sub_band_chans', 262144))])
    cmd.extend(['--overlap', str(orig.get('overlap', 512))])
    cmd.extend(['--max-drift', str(orig.get('max_drift', 5.0))])
    cmd.extend(['--snr', str(orig.get('snr', 5.0))])
    print('cmd ok:', ' '.join(cmd[:6]), '...')
    print('NO EXCEPTION in handler request path')
except Exception:
    print('=== EXCEPTION ===')
    traceback.print_exc()
