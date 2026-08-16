"""Extract clean training data for the classifier - v2.

Key changes from v1:
  - Larger crops (128x128 default) for more visual context
  - Metadata features (drift_rate, snr, freq) saved alongside crops
  - Metadata is normalized and fed to the classifier dense head

Usage:
    python ml/extract_classifier.py --target PROXCEN --crop-size 128 --snr-min 8 --max-rfi 2000
"""

import os
import sys
import time
import json
import argparse
import numpy as np
import sqlite3

SETI_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SETI_ROOT)
sys.path.insert(0, os.path.join(SETI_ROOT, 'src'))

from ml.extract import extract_crops_from_file, find_h5_for_hit


# Metadata fields to extract from DB for each hit
META_FIELDS = ['drift_rate', 'snr', 'freq']


def extract_clean_training_data(target='PROXCEN', crop_size=128, snr_min=8,
                                 max_rfi=2000, db_path=None):
    """Extract balanced clean training data with crops + metadata."""
    if db_path is None:
        db_path = os.path.join(SETI_ROOT, 'data', 'seti_hits.db')
    
    FINE_DIRS = [r'D:\seti_data\fine', r'G:\seti\data\fine']
    
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    
    # Build temp table of OFF frequencies for fast lookup
    print("Building frequency index tables...")
    t0 = time.time()
    c.execute('DROP TABLE IF EXISTS off_freqs')
    c.execute('CREATE TEMP TABLE off_freqs AS SELECT DISTINCT ROUND(freq, 3) AS f FROM hits WHERE on_off LIKE "%OFF%"')
    c.execute('CREATE INDEX IF NOT EXISTS idx_off_f ON off_freqs(f)')
    conn.commit()
    print(f"  Done in {time.time()-t0:.1f}s")
    
    # Candidate hits: ON-only, SNR >= threshold, no OFF freq match
    print(f"\nFetching clean candidates (ON-only, SNR >= {snr_min})...")
    t0 = time.time()
    c.execute("""
        SELECT h.id, h.source_file, h.freq, h.snr, h.on_off, h.drift_rate
        FROM hits h
        JOIN scans s ON h.scan_id = s.scan_id
        WHERE s.target = ?
        AND h.on_off LIKE '%ON%'
        AND h.snr >= ?
        AND NOT EXISTS (SELECT 1 FROM off_freqs o WHERE o.f = ROUND(h.freq, 3))
        ORDER BY h.snr DESC
    """, (target, snr_min))
    candidate_hits = c.fetchall()
    print(f"  Found {len(candidate_hits)} candidates ({time.time()-t0:.1f}s)")
    
    # RFI hits: ON hits at frequencies also in OFF, sampled
    print(f"\nFetching confirmed RFI sample (cap {max_rfi})...")
    t0 = time.time()
    c.execute("""
        SELECT COUNT(*) FROM hits h
        JOIN scans s ON h.scan_id = s.scan_id
        WHERE s.target = ?
        AND h.on_off LIKE '%ON%'
        AND h.snr >= ?
        AND EXISTS (SELECT 1 FROM off_freqs o WHERE o.f = ROUND(h.freq, 3))
    """, (target, snr_min))
    total_rfi_available = c.fetchone()[0]
    print(f"  Total RFI available at SNR >= {snr_min}: {total_rfi_available}")
    
    c.execute("""
        SELECT h.id, h.source_file, h.freq, h.snr, h.on_off, h.drift_rate
        FROM hits h
        JOIN scans s ON h.scan_id = s.scan_id
        WHERE s.target = ?
        AND h.on_off LIKE '%ON%'
        AND h.snr >= ?
        AND EXISTS (SELECT 1 FROM off_freqs o WHERE o.f = ROUND(h.freq, 3))
        ORDER BY RANDOM()
        LIMIT ?
    """, (target, snr_min, max_rfi))
    rfi_hits = c.fetchall()
    print(f"  Sampled {len(rfi_hits)} RFI hits ({time.time()-t0:.1f}s)")
    
    c.execute('DROP TABLE IF EXISTS off_freqs')
    conn.commit()
    conn.close()
    
    if len(candidate_hits) == 0:
        print("ERROR: No candidate hits found!")
        return None
    if len(rfi_hits) == 0:
        print("ERROR: No RFI hits found!")
        return None
    
    # Extract crops + metadata for each class
    all_crops = []
    all_labels = []
    all_hit_ids = []
    all_metadata = []  # (n_samples, 3) array: [drift_rate, snr, freq]
    
    for class_label, class_name, hits in [(0, 'candidate', candidate_hits), (1, 'rfi', rfi_hits)]:
        print(f"\n=== Extracting {class_name} crops ({len(hits)} hits, {crop_size}x{crop_size}) ===")
        
        file_groups = {}
        for hit in hits:
            sf = hit['source_file']
            if sf not in file_groups:
                file_groups[sf] = []
            file_groups[sf].append(hit)
        
        class_crops = []
        class_ids = []
        class_meta = []
        
        for file_idx, (source_file, file_hits) in enumerate(file_groups.items()):
            h5_path = find_h5_for_hit(file_hits[0], FINE_DIRS)
            if not h5_path:
                print(f"  [{file_idx+1}/{len(file_groups)}] SKIP: {source_file} not found")
                continue
            
            freqs = [h['freq'] for h in file_hits]
            print(f"  [{file_idx+1}/{len(file_groups)}] {source_file}: {len(freqs)} hits", end='', flush=True)
            t0 = time.time()
            
            crops = extract_crops_from_file(h5_path, freqs, crop_size=crop_size)
            if crops is None:
                print(f" - FAILED ({time.time()-t0:.1f}s)")
                continue
            
            print(f" - {crops.shape[0]} crops ({time.time()-t0:.1f}s)")
            
            for i, hit in enumerate(file_hits):
                if i < len(crops):
                    class_crops.append(crops[i])
                    class_ids.append(hit['id'])
                    # Metadata: drift_rate, snr, freq
                    class_meta.append([
                        hit['drift_rate'] or 0.0,
                        hit['snr'] or 0.0,
                        hit['freq'] or 0.0,
                    ])
        
        print(f"  {class_name}: {len(class_crops)} crops extracted")
        
        all_crops.extend(class_crops)
        all_labels.extend([class_label] * len(class_crops))
        all_hit_ids.extend(class_ids)
        all_metadata.extend(class_meta)
    
    if not all_crops:
        print("ERROR: No crops extracted!")
        return None
    
    crops_array = np.array(all_crops, dtype=np.float32)
    labels_array = np.array(all_labels, dtype=np.int32)
    hit_ids_array = np.array(all_hit_ids, dtype=np.int64)
    metadata_array = np.array(all_metadata, dtype=np.float32)
    
    # Normalize metadata: log-scale SNR (skewed), z-score freq, keep drift_rate raw
    meta_norm = metadata_array.copy()
    # SNR: log1p then z-score
    meta_norm[:, 1] = np.log1p(meta_norm[:, 1])
    snr_mean, snr_std = meta_norm[:, 1].mean(), meta_norm[:, 1].std()
    if snr_std > 0:
        meta_norm[:, 1] = (meta_norm[:, 1] - snr_mean) / snr_std
    # Freq: z-score (rough, just for model input)
    freq_mean, freq_std = meta_norm[:, 2].mean(), meta_norm[:, 2].std()
    if freq_std > 0:
        meta_norm[:, 2] = (meta_norm[:, 2] - freq_mean) / freq_std
    # Drift rate: z-score (most are near 0)
    drift_mean, drift_std = meta_norm[:, 0].mean(), meta_norm[:, 0].std()
    if drift_std > 0:
        meta_norm[:, 0] = (meta_norm[:, 0] - drift_mean) / drift_std
    
    # Save normalization stats for inference
    meta_stats = {
        'drift_rate': {'mean': float(drift_mean), 'std': float(drift_std)},
        'snr': {'mean': float(snr_mean), 'std': float(snr_std), 'log1p': True},
        'freq': {'mean': float(freq_mean), 'std': float(freq_std)},
    }
    
    n_cand = np.sum(labels_array == 0)
    n_rfi = np.sum(labels_array == 1)
    
    print(f"\n=== Final Dataset ===")
    print(f"  Total crops: {len(crops_array)}")
    print(f"  Candidates: {n_cand}")
    print(f"  RFI: {n_rfi}")
    print(f"  Crop shape: {crops_array.shape}")
    print(f"  Metadata shape: {metadata_array.shape}")
    print(f"  Metadata stats: {meta_stats}")
    
    # Save to cache
    cache_dir = os.path.join(SETI_ROOT, 'ml', 'data')
    os.makedirs(cache_dir, exist_ok=True)
    cache_path = os.path.join(cache_dir, f'classifier_{target}_{crop_size}_snr{snr_min}.npz')
    np.savez_compressed(
        cache_path,
        crops=crops_array,
        labels=labels_array,
        hit_ids=hit_ids_array,
        metadata=meta_norm,
        metadata_raw=metadata_array,
        meta_stats_json=np.array(json.dumps(meta_stats)),  # store as string
    )
    print(f"\nSaved to {cache_path} ({os.path.getsize(cache_path)/1e6:.1f} MB)")
    
    return cache_path


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Extract clean classifier training data')
    parser.add_argument('--target', default='PROXCEN')
    parser.add_argument('--crop-size', type=int, default=128)
    parser.add_argument('--snr-min', type=float, default=8)
    parser.add_argument('--max-rfi', type=int, default=2000)
    args = parser.parse_args()
    
    extract_clean_training_data(
        target=args.target,
        crop_size=args.crop_size,
        snr_min=args.snr_min,
        max_rfi=args.max_rfi,
    )
