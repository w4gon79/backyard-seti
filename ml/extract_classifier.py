"""Extract clean training data for the classifier.

Two classes:
  - RFI: hits at frequencies appearing in BOTH ON and OFF (confirmed RFI)
  - Candidate: ON-only hits at SNR >= threshold, no OFF freq match

Extracts waterfall crops from HDF5 files, saves balanced .npz cache.

Usage:
    python ml/extract_classifier.py --target PROXCEN --crop-size 64 --snr-min 8 --max-rfi 2000
"""

import os
import sys
import time
import argparse
import numpy as np
import sqlite3
import random

SETI_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SETI_ROOT)
sys.path.insert(0, os.path.join(SETI_ROOT, 'src'))

from ml.extract import extract_crops_from_file, find_h5_for_hit


def extract_clean_training_data(target='PROXCEN', crop_size=64, snr_min=8,
                                 max_rfi=2000, db_path=None):
    """Extract balanced clean training data for the classifier.
    
    RFI class: random sample of ON hits at frequencies also in OFF.
    Candidate class: all ON-only hits at SNR >= snr_min.
    """
    if db_path is None:
        db_path = os.path.join(SETI_ROOT, 'data', 'seti_hits.db')
    
    FINE_DIRS = [r'D:\seti_data\fine', r'G:\seti\data\fine']
    
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    
    # Build temp tables of rounded frequencies for fast lookup
    print("Building frequency index tables...")
    t0 = time.time()
    c.execute('DROP TABLE IF EXISTS on_freqs')
    c.execute('DROP TABLE IF EXISTS off_freqs')
    c.execute('CREATE TEMP TABLE off_freqs AS SELECT DISTINCT ROUND(freq, 3) AS f FROM hits WHERE on_off LIKE "%OFF%"')
    c.execute('CREATE INDEX IF NOT EXISTS idx_off_f ON off_freqs(f)')
    conn.commit()
    print(f"  Done in {time.time()-t0:.1f}s")
    
    # Candidate hits: ON-only, SNR >= threshold, no OFF freq match
    print(f"\nFetching clean candidates (ON-only, SNR >= {snr_min})...")
    t0 = time.time()
    c.execute("""
        SELECT h.id, h.source_file, h.freq, h.snr, h.on_off
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
    
    # RFI hits: ON hits at frequencies also in OFF (confirmed RFI)
    # Sample randomly, cap at max_rfi
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
    
    # Sample evenly across SNR range to get diverse RFI examples
    c.execute("""
        SELECT h.id, h.source_file, h.freq, h.snr, h.on_off
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
    
    # Cleanup temp tables
    c.execute('DROP TABLE IF EXISTS off_freqs')
    conn.commit()
    conn.close()
    
    if len(candidate_hits) == 0:
        print("ERROR: No candidate hits found!")
        return None
    if len(rfi_hits) == 0:
        print("ERROR: No RFI hits found!")
        return None
    
    # Extract crops for each class
    all_crops = []
    all_labels = []
    all_hit_ids = []
    
    for class_label, class_name, hits in [(0, 'candidate', candidate_hits), (1, 'rfi', rfi_hits)]:
        print(f"\n=== Extracting {class_name} crops ({len(hits)} hits) ===")
        
        # Group by source file
        file_groups = {}
        for hit in hits:
            sf = hit['source_file']
            if sf not in file_groups:
                file_groups[sf] = []
            file_groups[sf].append(hit)
        
        class_crops = []
        class_ids = []
        
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
        
        print(f"  {class_name}: {len(class_crops)} crops extracted")
        
        all_crops.extend(class_crops)
        all_labels.extend([class_label] * len(class_crops))
        all_hit_ids.extend(class_ids)
    
    if not all_crops:
        print("ERROR: No crops extracted!")
        return None
    
    crops_array = np.array(all_crops, dtype=np.float32)
    labels_array = np.array(all_labels, dtype=np.int32)
    hit_ids_array = np.array(all_hit_ids, dtype=np.int64)
    
    n_cand = np.sum(labels_array == 0)
    n_rfi = np.sum(labels_array == 1)
    
    print(f"\n=== Final Dataset ===")
    print(f"  Total crops: {len(crops_array)}")
    print(f"  Candidates: {n_cand}")
    print(f"  RFI: {n_rfi}")
    print(f"  Shape: {crops_array.shape}")
    
    # Save to cache
    cache_dir = os.path.join(SETI_ROOT, 'ml', 'data')
    os.makedirs(cache_dir, exist_ok=True)
    cache_path = os.path.join(cache_dir, f'classifier_{target}_{crop_size}_snr{snr_min}.npz')
    np.savez_compressed(
        cache_path,
        crops=crops_array,
        labels=labels_array,
        hit_ids=hit_ids_array,
    )
    print(f"\nSaved to {cache_path} ({os.path.getsize(cache_path)/1e6:.1f} MB)")
    
    return cache_path


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Extract clean classifier training data')
    parser.add_argument('--target', default='PROXCEN')
    parser.add_argument('--crop-size', type=int, default=64)
    parser.add_argument('--snr-min', type=float, default=8, help='Min SNR for training examples')
    parser.add_argument('--max-rfi', type=int, default=2000, help='Max RFI samples (for balancing)')
    args = parser.parse_args()
    
    extract_clean_training_data(
        target=args.target,
        crop_size=args.crop_size,
        snr_min=args.snr_min,
        max_rfi=args.max_rfi,
    )
