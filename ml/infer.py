"""Inference: score all hits in the DB using a trained autoencoder.

Computes reconstruction error (anomaly score) for each hit's waterfall crop.
Writes scores to the hits table. Also flags top N% as anomalies.

Usage:
    python ml/infer.py --checkpoint checkpoints/autoencoder_best.pt
    python ml/infer.py --checkpoint checkpoints/autoencoder_best.pt --target PROXCEN --top-percent 5
"""

import os
import sys
import argparse
import numpy as np
import sqlite3

import torch

SETI_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SETI_ROOT)

from ml.models.autoencoder import WaterfallAutoencoder, compute_anomaly_score
from ml.extract import extract_crops_from_file, find_h5_for_hit


def add_ml_columns(db_path):
    """Add ml_class and anomaly_score columns to hits table if missing."""
    conn = sqlite3.connect(db_path)
    cols = [r[1] for r in conn.execute('PRAGMA table_info(hits)').fetchall()]
    if 'anomaly_score' not in cols:
        conn.execute('ALTER TABLE hits ADD COLUMN anomaly_score REAL')
    if 'ml_class' not in cols:
        conn.execute('ALTER TABLE hits ADD COLUMN ml_class TEXT')
    conn.commit()
    conn.close()


def score_all_hits(checkpoint_path, target='PROXCEN', batch_size=512, 
                   top_percent=5, device='auto', db_path=None, max_per_file=5000):
    """Score all hits for a target using the trained autoencoder."""
    if db_path is None:
        db_path = os.path.join(SETI_ROOT, 'data', 'seti_hits.db')
    
    # Device
    if device == 'auto':
        device = 'cuda' if torch.cuda.is_available() else 'cpu'
    print(f"Inference on: {device}")
    
    # Load checkpoint
    ckpt = torch.load(checkpoint_path, map_location=device, weights_only=False)
    config = ckpt['config']
    model = WaterfallAutoencoder(**config).to(device)
    model.load_state_dict(ckpt['model_state'])
    model.eval()
    print(f"Loaded checkpoint: val_loss={ckpt['best_val_loss']:.6f}")
    
    # Ensure DB columns exist
    add_ml_columns(db_path)
    
    FINE_DIRS = [
        r'D:\seti_data\fine',
        r'G:\seti\data\fine',
    ]
    
    crop_size = config['crop_size']
    
    # Load all hits for target
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("""
        SELECT h.id, h.source_file, h.freq, h.on_off
        FROM hits h
        JOIN scans s ON h.scan_id = s.scan_id
        WHERE s.target = ?
        ORDER BY h.source_file
    """, (target,))
    hits = c.fetchall()
    print(f"Found {len(hits)} hits for {target}")
    
    # Group by source file
    file_groups = {}
    for hit in hits:
        sf = hit['source_file']
        if sf not in file_groups:
            file_groups[sf] = []
        file_groups[sf].append(hit)
    
    all_scores = {}  # hit_id -> anomaly_score
    
    for file_idx, (source_file, file_hits) in enumerate(file_groups.items()):
        h5_path = find_h5_for_hit(file_hits[0], FINE_DIRS)
        if not h5_path:
            print(f"  [{file_idx+1}/{len(file_groups)}] SKIP: {source_file} not found")
            continue
        
        # Subsample if too many hits in one file (same cap as extraction)
        if len(file_hits) > max_per_file:
            import random
            file_hits = random.sample(file_hits, max_per_file)
            freqs = [h['freq'] for h in file_hits]
        print(f"  [{file_idx+1}/{len(file_groups)}] {source_file}: {len(freqs)} hits", end='', flush=True)
        
        crops = extract_crops_from_file(h5_path, freqs, crop_size=crop_size)
        if crops is None:
            print(" - FAILED")
            continue
        
        # Score in batches
        crops_tensor = torch.from_numpy(crops[:, np.newaxis, :, :]).float().to(device)
        scores = []
        
        with torch.no_grad():
            for i in range(0, len(crops_tensor), batch_size):
                batch = crops_tensor[i:i+batch_size]
                batch_scores = compute_anomaly_score(model, batch, device)
                scores.extend(batch_scores.cpu().numpy())
        
        print(f" - scored ({len(scores)})")
        
        for i, hit in enumerate(file_hits):
            if i < len(scores):
                all_scores[hit['id']] = float(scores[i])
    
    if not all_scores:
        print("No hits scored!")
        return
    
    # Compute threshold for anomaly flag
    score_values = np.array(list(all_scores.values()))
    threshold = np.percentile(score_values, 100 - top_percent)
    
    n_anomaly = np.sum(score_values >= threshold)
    print(f"\nTotal scored: {len(all_scores)}")
    print(f"Anomaly threshold ({top_percent}th percentile): {threshold:.6f}")
    print(f"Flagged as anomalies: {n_anomaly}")
    
    # Write to DB
    c = conn.cursor()
    for hit_id, score in all_scores.items():
        cls = 'anomaly' if score >= threshold else 'normal'
        c.execute(
            "UPDATE hits SET anomaly_score = ?, ml_class = ? WHERE id = ?",
            (score, cls, hit_id)
        )
    conn.commit()
    conn.close()
    
    print(f"Scores written to DB ({db_path})")
    
    # Print top 10 anomalies
    sorted_scores = sorted(all_scores.items(), key=lambda x: x[1], reverse=True)
    print(f"\nTop 10 anomalies:")
    
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    for hit_id, score in sorted_scores[:10]:
        row = c.execute("SELECT freq, source_file, on_off, snr FROM hits WHERE id = ?", (hit_id,)).fetchone()
        if row:
            print(f"  ID {hit_id}: {row['freq']:.6f} MHz SNR={row['snr']:.1f} score={score:.6f} [{row['on_off']}] {row['source_file']}")
    conn.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Score hits with trained autoencoder')
    parser.add_argument('--checkpoint', default=None, help='Path to checkpoint (default: checkpoints/autoencoder_best.pt)')
    parser.add_argument('--target', default='PROXCEN')
    parser.add_argument('--top-percent', type=float, default=5, help='Flag top N%% as anomalies')
    parser.add_argument('--batch-size', type=int, default=512)
    parser.add_argument('--max-per-file', type=int, default=5000, help='Max hits per HDF5 file to score')
    args = parser.parse_args()
    
    ckpt = args.checkpoint or os.path.join(SETI_ROOT, 'ml', 'checkpoints', 'autoencoder_best.pt')
    if not os.path.isfile(ckpt):
        print(f"Checkpoint not found: {ckpt}")
        sys.exit(1)
    
    score_all_hits(
        checkpoint_path=ckpt,
        target=args.target,
        batch_size=args.batch_size,
        top_percent=args.top_percent,
        max_per_file=args.max_per_file,
    )
