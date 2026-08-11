"""
db.py - SQLite database layer for SETI hit storage.

Replaces large JSON files with indexed SQLite tables for fast querying,
filtering, and cross-epoch analysis.

Database: G:\\seti\\data\\seti_hits.db (WAL mode for concurrent reads)
"""

import os
import json
import sqlite3
import time
import numpy as np
from datetime import datetime

# Database path
SETI_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(SETI_ROOT, 'data', 'seti_hits.db')

# Ensure data dir exists
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)


def get_db(db_path=None):
    """Get a SQLite connection with WAL mode and sensible defaults."""
    path = db_path or DB_PATH
    conn = sqlite3.connect(path, timeout=30)
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA synchronous=NORMAL')
    conn.execute('PRAGMA cache_size=-64000')  # 64MB cache
    conn.execute('PRAGMA temp_store=MEMORY')
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path=None):
    """Create the schema if it doesn't exist."""
    conn = get_db(db_path)
    try:
        conn.executescript('''
            CREATE TABLE IF NOT EXISTS scans (
                scan_id         TEXT PRIMARY KEY,
                target          TEXT,
                timestamp       TEXT,
                status          TEXT,
                mjd_start       REAL,
                sub_band_chans  INTEGER,
                overlap         INTEGER,
                max_drift       REAL,
                snr_threshold   REAL,
                f_start         REAL,
                f_stop          REAL,
                total_hits      INTEGER,
                on_hits         INTEGER,
                off_hits        INTEGER,
                duration_s      REAL,
                bary_corrected  INTEGER DEFAULT 0,
                bary_velocity   REAL,
                bary_mjd        REAL,
                ra_hours        REAL,
                dec_deg         REAL,
                telescope       TEXT,
                created_at      TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS hits (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                scan_id         TEXT NOT NULL,
                source_file     TEXT,
                on_off          TEXT,
                freq            REAL,
                barycentric_freq REAL,
                drift_rate      REAL,
                snr             REAL,
                channel         INTEGER,
                sub_band        INTEGER,
                mjd             REAL,
                FOREIGN KEY (scan_id) REFERENCES scans(scan_id)
            );

            CREATE INDEX IF NOT EXISTS idx_hits_scan ON hits(scan_id);
            CREATE INDEX IF NOT EXISTS idx_hits_snr ON hits(snr);
            CREATE INDEX IF NOT EXISTS idx_hits_on_off ON hits(on_off);
            CREATE INDEX IF NOT EXISTS idx_hits_scan_snr ON hits(scan_id, snr);
            CREATE INDEX IF NOT EXISTS idx_hits_scan_onoff_snr ON hits(scan_id, on_off, snr);
            CREATE INDEX IF NOT EXISTS idx_hits_bary ON hits(barycentric_freq);
            CREATE INDEX IF NOT EXISTS idx_hits_scan_bary ON hits(scan_id, barycentric_freq);

            CREATE TABLE IF NOT EXISTS cross_epoch_results (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                scan_ids        TEXT,
                min_snr         REAL,
                tolerance_hz    REAL,
                min_epochs      INTEGER,
                candidate_count INTEGER,
                result_json     TEXT,
                created_at      TEXT DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_cross_epoch_lookup ON cross_epoch_results(scan_ids, min_snr, tolerance_hz, min_epochs);

            CREATE TABLE IF NOT EXISTS stack_jobs (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id          TEXT UNIQUE,
                target          TEXT,
                freq_center     REAL,
                width_mhz       REAL,
                epochs          TEXT,
                n_epochs        INTEGER,
                n_sigma         REAL,
                status          TEXT DEFAULT 'pending',
                progress        INTEGER DEFAULT 0,
                progress_msg    TEXT,
                peaks_json      TEXT,
                plot_path       TEXT,
                stack_median    REAL,
                stack_sigma     REAL,
                snr_improvement REAL,
                epoch_info_json TEXT,
                created_at      TEXT DEFAULT (datetime('now')),
                completed_at    TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_stack_jobs_status ON stack_jobs(status);
        ''')
        # Migration: add epoch_info_json column if missing
        try:
            cols = [r[1] for r in conn.execute('PRAGMA table_info(stack_jobs)').fetchall()]
            if 'epoch_info_json' not in cols:
                conn.execute('ALTER TABLE stack_jobs ADD COLUMN epoch_info_json TEXT')
        except Exception:
            pass
        conn.commit()
    finally:
        conn.close()


# ─── Scan Operations ──────────────────────────────────────────────────

def upsert_scan(scan_meta, db_path=None):
    """Insert or update a scan record from scan_meta dict."""
    conn = get_db(db_path)
    try:
        params = scan_meta.get('parameters', {})
        stats = scan_meta.get('stats', {})
        conn.execute('''
            INSERT OR REPLACE INTO scans 
            (scan_id, target, timestamp, status, mjd_start,
             sub_band_chans, overlap, max_drift, snr_threshold,
             f_start, f_stop, total_hits, on_hits, off_hits, duration_s)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            scan_meta.get('scan_id', ''),
            scan_meta.get('target', ''),
            scan_meta.get('timestamp', ''),
            scan_meta.get('status', ''),
            scan_meta.get('mjd_start'),
            params.get('sub_band_chans'),
            params.get('overlap'),
            params.get('max_drift'),
            params.get('snr'),
            params.get('f_start'),
            params.get('f_stop'),
            stats.get('total_hits'),
            stats.get('on_hits'),
            stats.get('off_hits'),
            stats.get('duration_s'),
        ))
        conn.commit()
    finally:
        conn.close()


def get_scan(scan_id, db_path=None):
    """Get scan metadata as dict."""
    conn = get_db(db_path)
    try:
        row = conn.execute('SELECT * FROM scans WHERE scan_id = ?', (scan_id,)).fetchone()
        if row:
            return dict(row)
        return None
    finally:
        conn.close()


def get_all_scans(db_path=None):
    """List all scans, newest first."""
    conn = get_db(db_path)
    try:
        rows = conn.execute('SELECT * FROM scans ORDER BY timestamp DESC').fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def delete_scan(scan_id, db_path=None):
    """Delete a scan and all its hits from the database.

    Also removes any cross-epoch results that reference this scan.
    Returns dict with counts of deleted rows.
    """
    conn = get_db(db_path)
    try:
        # Count hits before deleting
        hit_count = conn.execute('SELECT COUNT(*) FROM hits WHERE scan_id = ?', (scan_id,)).fetchone()[0]

        # Delete hits
        conn.execute('DELETE FROM hits WHERE scan_id = ?', (scan_id,))

        # Delete the scan record
        conn.execute('DELETE FROM scans WHERE scan_id = ?', (scan_id,))

        # Delete cross-epoch results that include this scan_id
        ce_rows = conn.execute('SELECT id, scan_ids FROM cross_epoch_results').fetchall()
        ce_deleted = 0
        for row in ce_rows:
            scan_ids_str = row['scan_ids'] or ''
            scan_ids = [s.strip() for s in scan_ids_str.split(',')]
            if scan_id in scan_ids:
                conn.execute('DELETE FROM cross_epoch_results WHERE id = ?', (row['id'],))
                ce_deleted += 1

        conn.commit()
        return {'scan_id': scan_id, 'hits_deleted': hit_count, 'cross_epoch_deleted': ce_deleted}
    finally:
        conn.close()


def update_scan_barycentric(scan_id, velocity, mjd, ra_hours, dec_deg, telescope, db_path=None):
    """Mark a scan as barycentrically corrected."""
    conn = get_db(db_path)
    try:
        conn.execute('''
            UPDATE scans SET bary_corrected = 1, bary_velocity = ?, bary_mjd = ?,
                           ra_hours = ?, dec_deg = ?, telescope = ?
            WHERE scan_id = ?
        ''', (velocity, mjd, ra_hours, dec_deg, telescope, scan_id))
        conn.commit()
    finally:
        conn.close()


# ─── Hit Operations ──────────────────────────────────────────────────

def bulk_insert_hits(scan_id, hits, db_path=None):
    """Insert hits in bulk with transaction batching.
    
    hits: list of dicts with keys: freq, snr, drift_rate, channel, sub_band, 
          source_file, on_off, mjd
    """
    conn = get_db(db_path)
    try:
        BATCH = 10000
        for i in range(0, len(hits), BATCH):
            batch = hits[i:i + BATCH]
            conn.executemany('''
                INSERT INTO hits (scan_id, source_file, on_off, freq, barycentric_freq,
                                  drift_rate, snr, channel, sub_band, mjd)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', [
                (scan_id,
                 h.get('source_file', h.get('file', '')),
                 h.get('on_off', ''),
                 h.get('freq', 0),
                 h.get('barycentric_freq'),
                 h.get('drift_rate', 0),
                 h.get('snr', 0),
                 h.get('channel'),
                 h.get('sub_band'),
                 h.get('mjd'))
                for h in batch
            ])
            conn.commit()
    finally:
        conn.close()


def update_barycentric_freqs(scan_id, hit_updates, db_path=None):
    """Batch update barycentric frequencies for hits.
    
    hit_updates: list of dicts with: freq (observed), barycentric_freq, source_file
    Uses freq + source_file as a composite key to find the right hit row.
    """
    conn = get_db(db_path)
    try:
        BATCH = 10000
        for i in range(0, len(hit_updates), BATCH):
            batch = hit_updates[i:i + BATCH]
            conn.executemany('''
                UPDATE hits SET barycentric_freq = ?
                WHERE scan_id = ? AND source_file = ? AND freq = ?
            ''', [
                (h['barycentric_freq'], scan_id,
                 h.get('source_file', h.get('file', '')),
                 h['freq'])
                for h in batch
            ])
            conn.commit()
    finally:
        conn.close()


def get_hits(scan_id, min_snr=0, on_off=None, limit=100, offset=0,
             order_by='snr DESC', db_path=None):
    """Query hits with optional filters. Returns list of dicts."""
    conn = get_db(db_path)
    try:
        query = 'SELECT * FROM hits WHERE scan_id = ?'
        params = [scan_id]
        if min_snr > 0:
            query += ' AND snr >= ?'
            params.append(min_snr)
        if on_off:
            query += ' AND on_off = ?'
            params.append(on_off)
        # Sanitize order_by (only allow column names + ASC/DESC)
        allowed_cols = {'snr', 'freq', 'barycentric_freq', 'drift_rate', 'channel', 'id'}
        parts = order_by.split()
        if parts and parts[0].lower() in allowed_cols:
            direction = 'DESC' if len(parts) > 1 and parts[1].upper() == 'DESC' else 'ASC'
            query += f' ORDER BY {parts[0].lower()} {direction}'
        else:
            query += ' ORDER BY snr DESC'
        if limit:
            query += ' LIMIT ? OFFSET ?'
            params.extend([limit, offset])
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def count_hits(scan_id, min_snr=0, on_off=None, db_path=None):
    """Count hits with optional filters."""
    conn = get_db(db_path)
    try:
        query = 'SELECT COUNT(*) as cnt FROM hits WHERE scan_id = ?'
        params = [scan_id]
        if min_snr > 0:
            query += ' AND snr >= ?'
            params.append(min_snr)
        if on_off:
            query += ' AND on_off = ?'
            params.append(on_off)
        row = conn.execute(query, params).fetchone()
        return row['cnt'] if row else 0
    finally:
        conn.close()


def get_hit_stats(scan_id, db_path=None):
    """Get hit statistics for a scan, including SNR distribution."""
    conn = get_db(db_path)
    try:
        # Total counts
        row = conn.execute('''
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN on_off = 'ON' THEN 1 ELSE 0 END) as on_count,
                SUM(CASE WHEN on_off = 'OFF' THEN 1 ELSE 0 END) as off_count,
                MAX(snr) as top_snr
            FROM hits WHERE scan_id = ?
        ''', (scan_id,)).fetchone()
        
        # SNR distribution
        snr_ranges = [
            (5, 7), (7, 10), (10, 15), (15, 25), (25, 50), (50, 999999)
        ]
        snr_dist = []
        for lo, hi in snr_ranges:
            r = conn.execute('''
                SELECT COUNT(*) as cnt FROM hits 
                WHERE scan_id = ? AND snr >= ? AND snr < ?
            ''', (scan_id, lo, hi)).fetchone()
            snr_dist.append({'range': f'{lo}-{hi}' if hi < 999999 else f'{lo}+', 'count': r['cnt']})
        
        return {
            'total_hits': row['total'] if row else 0,
            'on_hits': row['on_count'] if row else 0,
            'off_hits': row['off_count'] if row else 0,
            'top_snr': round(row['top_snr'], 2) if row and row['top_snr'] else 0,
            'snr_distribution': snr_dist,
        }
    finally:
        conn.close()


# ─── Cross-Epoch Operations ──────────────────────────────────────────

def save_cross_epoch_result(result_dict, db_path=None):
    """Save a cross-epoch search result to the database."""
    conn = get_db(db_path)
    try:
        summary = result_dict.get('summary', {})
        scan_ids = json.dumps(summary.get('epoch_info', {}).keys() and 
                              [k for k in summary.get('epoch_info', {}).keys()] or [])
        # Better: get scan_ids from the summary or candidates
        # We'll extract from epoch_details in candidates
        candidates = result_dict.get('candidates', [])
        if candidates:
            all_scan_dirs = set()
            for c in candidates:
                for ed in c.get('epoch_details', []):
                    all_scan_dirs.add(ed.get('scan_dir', ''))
        conn.execute('''
            INSERT INTO cross_epoch_results 
            (scan_ids, min_snr, tolerance_hz, min_epochs, candidate_count, result_json)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (
            scan_ids,
            summary.get('min_snr', 0),
            summary.get('freq_tolerance_hz', 10),
            summary.get('min_epochs', 2),
            len(candidates),
            json.dumps(result_dict),
        ))
        conn.commit()
        return conn.execute('SELECT last_insert_rowid()').fetchone()[0]
    finally:
        conn.close()


def get_cross_epoch_history(limit=50, db_path=None):
    """List cached cross-epoch results, newest first."""
    conn = get_db(db_path)
    try:
        rows = conn.execute('''
            SELECT id, scan_ids, min_snr, tolerance_hz, min_epochs,
                   candidate_count, created_at
            FROM cross_epoch_results
            ORDER BY created_at DESC
            LIMIT ?
        ''', (limit,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def load_cross_epoch_result(result_id, db_path=None):
    """Load a specific cross-epoch result by id."""
    conn = get_db(db_path)
    try:
        row = conn.execute(
            'SELECT * FROM cross_epoch_results WHERE id = ?', (result_id,)
        ).fetchone()
        if row:
            d = dict(row)
            d['result'] = json.loads(d.pop('result_json'))
            return d
        return None
    finally:
        conn.close()


def cross_epoch_search_sql(scan_ids, min_snr=0, tolerance_hz=10, min_epochs=2, db_path=None):
    """SQL-based cross-epoch search using bucketed barycentric frequencies.
    
    This replaces the Python JSON-parsing approach with indexed SQL queries.
    Returns same dict format as barycentric_correct.cross_epoch_match().
    """
    conn = get_db(db_path)
    try:
        freq_tol_mhz = tolerance_hz * 1e-6
        grid_resolution = int(1.0 / freq_tol_mhz) if freq_tol_mhz > 0 else 100000

        # Step 1: Get all ON hit buckets per scan, counting distinct scan_ids per bucket
        # Use a CTE for the bucketing
        placeholders = ','.join('?' * len(scan_ids))

        # Get ON buckets with epoch counts
        on_query = f'''
            WITH on_buckets AS (
                SELECT 
                    CAST(ROUND(barycentric_freq * {grid_resolution}) AS INTEGER) as bucket,
                    scan_id,
                    barycentric_freq,
                    freq as obs_freq,
                    drift_rate,
                    snr,
                    source_file,
                    mjd,
                    ROW_NUMBER() OVER (PARTITION BY CAST(ROUND(barycentric_freq * {grid_resolution}) AS INTEGER), scan_id ORDER BY snR DESC) as rn
                FROM hits
                WHERE scan_id IN ({placeholders})
                  AND on_off = 'ON'
                  AND barycentric_freq IS NOT NULL
            '''
        if min_snr > 0:
            on_query += f' AND snr >= {min_snr}'
        
        on_query += ''')
            SELECT bucket, scan_id, barycentric_freq, obs_freq, drift_rate, snr, source_file, mjd
            FROM on_buckets WHERE rn = 1
        '''

        on_rows = conn.execute(on_query, scan_ids).fetchall()

        # Get OFF buckets
        off_query = f'''
            SELECT DISTINCT CAST(ROUND(barycentric_freq * {grid_resolution}) AS INTEGER) as bucket
            FROM hits
            WHERE scan_id IN ({placeholders})
              AND on_off = 'OFF'
              AND barycentric_freq IS NOT NULL
        '''
        if min_snr > 0:
            off_query += f' AND snr >= {min_snr}'

        off_buckets = set()
        for row in conn.execute(off_query, scan_ids):
            off_buckets.add(row['bucket'])
            # Also add adjacent buckets (matching the Python algorithm)
            off_buckets.add(row['bucket'] - 1)
            off_buckets.add(row['bucket'] + 1)

        # Step 2: Group ON hits by bucket, count distinct scans
        bucket_data = {}  # bucket -> {scan_ids: set, hits: []}
        for row in on_rows:
            b = row['bucket']
            if b in off_buckets:
                continue  # Present in OFF -> skip
            if b not in bucket_data:
                bucket_data[b] = {'scan_ids': set(), 'hits': []}
            bucket_data[b]['scan_ids'].add(row['scan_id'])
            bucket_data[b]['hits'].append(dict(row))

        # Step 3: Filter by min_epochs and build candidates
        candidates = []
        total_freqs_checked = len(bucket_data) + len(off_buckets)  # approximate

        for bucket, data in bucket_data.items():
            n_epochs = len(data['scan_ids'])
            if n_epochs < min_epochs:
                continue

            hits = data['hits']
            drift_rates = [h['drift_rate'] or 0 for h in hits]
            snrs = [h['snr'] or 0 for h in hits]
            freqs_bary = [h['barycentric_freq'] or 0 for h in hits]
            freqs_obs = [h['obs_freq'] or 0 for h in hits]

            candidates.append({
                'barycentric_freq_mhz': round(float(np.mean(freqs_bary)), 8),
                'barycentric_freq_std_mhz': round(float(np.std(freqs_bary)), 8),
                'observed_freqs_mhz': [round(float(f), 6) for f in freqs_obs],
                'mean_drift_rate': round(float(np.mean(drift_rates)), 6),
                'drift_rates': [round(float(d), 6) for d in drift_rates],
                'max_snr': float(max(snrs)) if snrs else 0,
                'snrs': [round(float(s), 2) for s in snrs],
                'on_count': len(hits),
                'epoch_count': n_epochs,
                'off_count': 0,
                'epochs': sorted(list(data['scan_ids'])),
                'epoch_details': [],
            })

        # Sort by epoch count (desc), then SNR (desc)
        candidates.sort(key=lambda c: (c['epoch_count'], c['max_snr']), reverse=True)

        # Compute false alarm probability
        for cand in candidates:
            cand['log_false_alarm_prob'] = -10.0  # placeholder, will refine

        # Count total unique ON freqs (buckets with ON hits)
        on_bucket_count = len(set(r['bucket'] for r in on_rows))

        return {
            'candidates': candidates,
            'summary': {
                'total_scans': len(scan_ids),
                'total_epochs': len(scan_ids),
                'total_on_frequencies': on_bucket_count,
                'total_candidates': len(candidates),
                'freq_tolerance_hz': tolerance_hz,
                'min_epochs': min_epochs,
                'min_snr': min_snr,
                'from_db': True,
            },
        }
    finally:
        conn.close()


# ─── Import Functions ────────────────────────────────────────────────

def import_scan_from_json(scan_dir, db_path=None):
    """Import a scan's data from JSON files into the database.
    
    Reads:
    - scan_meta.json for scan metadata
    - *_hits.json files for raw hits
    - barycentric/combined_corrected.json for barycentric corrections (if exists)
    
    Returns dict with import stats.
    """
    scan_dir = os.path.abspath(scan_dir)
    scan_meta_path = os.path.join(scan_dir, 'scan_meta.json')

    stats = {'scan_dir': scan_dir, 'hits_imported': 0, 'bary_updated': 0}

    # Load scan meta
    meta = None
    if os.path.isfile(scan_meta_path):
        with open(scan_meta_path, encoding='utf-8-sig') as f:
            meta = json.load(f)

    if not meta:
        stats['error'] = 'No scan_meta.json found'
        return stats

    scan_id = meta.get('scan_id', os.path.basename(scan_dir))

    # Check if already imported
    existing = get_scan(scan_id, db_path)
    if existing and existing.get('total_hits', 0) > 0:
        hit_count = count_hits(scan_id, db_path=db_path)
        if hit_count > 0:
            stats['skipped'] = True
            stats['hits_in_db'] = hit_count
            return stats

    # Upsert scan metadata
    upsert_scan(meta, db_path)

    # Check for combined_corrected.json (has all hits + barycentric data)
    combined_path = os.path.join(scan_dir, 'barycentric', 'combined_corrected.json')
    if os.path.isfile(combined_path):
        # Import from combined (has barycentric data already)
        with open(combined_path) as f:
            combined = json.load(f)
        hits = combined.get('hits', [])
        bulk_insert_hits(scan_id, hits, db_path)

        # Update scan barycentric info
        bary_vel = hits[0].get('barycentric_velocity_mps', 0) if hits else 0
        bary_mjd = combined.get('mjd', 0)
        update_scan_barycentric(
            scan_id, bary_vel, bary_mjd,
            combined.get('ra_hours'),
            combined.get('dec_deg'),
            combined.get('telescope', 'parkes'),
            db_path
        )
        stats['hits_imported'] = len(hits)
        stats['bary_updated'] = len(hits)
    else:
        # Import from individual hits files
        import glob as glob_module
        hit_files = glob_module.glob(
            os.path.join(scan_dir, '**/*_hits.json'), recursive=True)
        # Filter out bary_hits files
        hit_files = [f for f in hit_files if '_bary_' not in os.path.basename(f)]

        all_hits = []
        for hf in hit_files:
            with open(hf) as f:
                data = json.load(f)
            fname = data.get('file', '')
            if not fname:
                # Derive from JSON filename: strip _hits.json, remove _partial, add .h5
                fname = os.path.basename(hf).replace('_partial_hits.json', '.h5').replace('_hits.json', '.h5')
                if not fname.endswith('.h5'):
                    fname += '.h5'
            is_on = '_S_' in fname
            for h in data.get('hits', []):
                h['on_off'] = 'ON' if is_on else 'OFF'
                h['source_file'] = fname
                all_hits.append(h)

        if all_hits:
            bulk_insert_hits(scan_id, all_hits, db_path)
            stats['hits_imported'] = len(all_hits)

        # Check for separate barycentric files
        bary_dir = os.path.join(scan_dir, 'barycentric')
        if os.path.isdir(bary_dir):
            bary_files = glob_module.glob(os.path.join(bary_dir, '*_bary_hits.json'))
            bary_updates = []
            bary_vel = 0
            bary_mjd = 0
            for bf in bary_files:
                with open(bf) as f:
                    data = json.load(f)
                bary_vel = data.get('barycentric_velocity_mps', bary_vel)
                bary_mjd = data.get('barycentric_mjd', bary_mjd)
                for h in data.get('hits', []):
                    if 'barycentric_freq' in h:
                        bary_updates.append({
                            'freq': h.get('freq'),
                            'barycentric_freq': h['barycentric_freq'],
                            'source_file': h.get('file', h.get('source_file', '')),
                        })
            if bary_updates:
                update_barycentric_freqs(scan_id, bary_updates, db_path)
                stats['bary_updated'] = len(bary_updates)
                update_scan_barycentric(
                    scan_id, bary_vel, bary_mjd, None, None, 'parkes', db_path)

    return stats


# ─── Utility ─────────────────────────────────────────────────────────

def db_stats(db_path=None):
    """Get database statistics."""
    conn = get_db(db_path)
    try:
        n_scans = conn.execute('SELECT COUNT(*) FROM scans').fetchone()[0]
        n_hits = conn.execute('SELECT COUNT(*) FROM hits').fetchone()[0]
        n_cross = conn.execute('SELECT COUNT(*) FROM cross_epoch_results').fetchone()[0]
        db_size = os.path.getsize(db_path or DB_PATH) if os.path.isfile(db_path or DB_PATH) else 0
        return {
            'scans': n_scans,
            'hits': n_hits,
            'cross_epoch_results': n_cross,
            'db_size_mb': round(db_size / 1e6, 1),
            'db_path': db_path or DB_PATH,
        }
    finally:
        conn.close()


if __name__ == '__main__':
    print(f"SETI SQLite Database")
    print(f"  Path: {DB_PATH}")
    init_db()
    s = db_stats()
    print(f"  Scans: {s['scans']}")
    print(f"  Hits: {s['hits']}")
    print(f"  Cross-epoch results: {s['cross_epoch_results']}")
    print(f"  DB size: {s['db_size_mb']} MB")
