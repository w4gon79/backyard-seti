"""Inference: classify all hits in DB using trained classifier v2.

Computes RFI probability for each hit using crops + metadata.
Writes scores to hits table.

Usage:
    python ml/infer_classifier.py --target PROXCEN --crop-size 128
"""

import os
import sys
import time
import argparse
import numpy as np
import sqlite3
import json

import torch

SETI_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SETI_ROOT)

from ml.models.classifier import WaterfallClassifier
from ml.extract import extract_crops_from_file, find_h5_for_hit


def add_classifier_columns(db_path):
    conn = sqlite3.connect(db_path)
    cols = [r[1] for r in conn.execute('PRAGMA table_info(hits)').fetchall()]
    if 'anomaly_score' not in cols:
        conn.execute('ALTER TABLE hits ADD COLUMN anomaly_score REAL')
    if 'ml_class' not in cols:
        conn.execute('ALTER TABLE hits ADD COLUMN ml_class TEXT')
    conn.commit()
    conn.close()


def normalize_meta(drift_rate, snr, freq, meta_stats):
    """Apply same normalization as extract_classifier.py."""
    d = drift_rate or 0.0
    s = np.log1p(snr or 0.0)
    f = freq or 0.0

    ds = meta_stats['drift_rate']['std']
    if ds > 0:
        d = (d - meta_stats['drift_rate']['mean']) / ds

    ss = meta_stats['snr']['std']
    if ss > 0:
        s = (s - meta_stats['snr']['mean']) / ss

    fs = meta_stats['freq']['std']
    if fs > 0:
        f = (f - meta_stats['freq']['mean']) / fs

    return [d, s, f]


def classify_all_hits(checkpoint_path, target='PROXCEN', batch_size=512,
                       device='auto', db_path=None, max_per_file=5000):
    if db_path is None:
        db_path = os.path.join(SETI_ROOT, 'data', 'seti_hits.db')

    if device == 'auto':
        device = 'cuda' if torch.cuda.is_available() else 'cpu'
    print(f"Inference on: {device}")

    ckpt = torch.load(checkpoint_path, map_location=device, weights_only=False)
    config = ckpt['config']
    crop_size = config['crop_size']
    model = WaterfallClassifier(**config).to(device)
    model.load_state_dict(ckpt['model_state'])
    model.eval()
    print(f"Loaded checkpoint: val_acc={ckpt.get('best_val_acc', '?'):.3f} val_f1={ckpt.get('best_val_f1', '?'):.3f}")
    print(f"Crop size: {crop_size}x{crop_size}, metadata_dim: {config.get('metadata_dim', 3)}")

    # Load meta stats from cache file if available
    cache_path = os.path.join(SETI_ROOT, 'ml', 'data',
                              f'classifier_{target}_{crop_size}_snr8.0.npz')
    meta_stats = None
    if os.path.isfile(cache_path):
        data = np.load(cache_path, allow_pickle=True)
        if 'meta_stats_json' in data:
            meta_stats = json.loads(str(data['meta_stats_json']))
            print(f"Loaded meta stats: {meta_stats}")
    if meta_stats is None:
        # Fallback defaults
        meta_stats = {
            'drift_rate': {'mean': 0.0, 'std': 1.0},
            'snr': {'mean': 0.0, 'std': 1.0},
            'freq': {'mean': 0.0, 'std': 1.0},
        }
        print("WARNING: No meta stats found, using defaults")

    add_classifier_columns(db_path)

    FINE_DIRS = [r'D:\seti_data\fine', r'G:\seti\data\fine']

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    c.execute("""
        SELECT h.id, h.source_file, h.freq, h.on_off, h.snr, h.drift_rate
        FROM hits h
        JOIN scans s ON h.scan_id = s.scan_id
        WHERE s.target = ?
        ORDER BY h.source_file
    """, (target,))
    hits = c.fetchall()
    print(f"Found {len(hits)} hits for {target}")

    file_groups = {}
    for hit in hits:
        sf = hit['source_file']
        if sf not in file_groups:
            file_groups[sf] = []
        file_groups[sf].append(hit)

    all_scores = {}
    total_scored = 0
    total_files = len(file_groups)

    for file_idx, (source_file, file_hits) in enumerate(file_groups.items()):
        h5_path = find_h5_for_hit(file_hits[0], FINE_DIRS)
        if not h5_path:
            print(f"  [{file_idx+1}/{total_files}] SKIP: {source_file} not found")
            continue

        if len(file_hits) > max_per_file:
            import random
            file_hits = random.sample(file_hits, max_per_file)

        freqs = [h['freq'] for h in file_hits]
        print(f"  [{file_idx+1}/{total_files}] {source_file}: {len(freqs)} hits", end='', flush=True)
        t0 = time.time()

        crops = extract_crops_from_file(h5_path, freqs, crop_size=crop_size)
        if crops is None:
            print(" - FAILED")
            continue

        # Build metadata tensor for this batch
        meta_batch = np.array([
            normalize_meta(h['drift_rate'], h['snr'], h['freq'], meta_stats)
            for h in file_hits[:len(crops)]
        ], dtype=np.float32)

        crops_tensor = torch.from_numpy(crops[:, np.newaxis, :, :]).float().to(device)
        meta_tensor = torch.from_numpy(meta_batch).to(device)
        probs = []

        with torch.no_grad():
            for i in range(0, len(crops_tensor), batch_size):
                batch_x = crops_tensor[i:i+batch_size]
                batch_m = meta_tensor[i:i+batch_size]
                batch_probs = torch.sigmoid(model(batch_x, batch_m))
                probs.extend(batch_probs.cpu().numpy())

        print(f" - scored ({len(probs)}, {time.time()-t0:.1f}s)")

        for i, hit in enumerate(file_hits):
            if i < len(probs):
                all_scores[hit['id']] = float(probs[i])

        total_scored += len(probs)

        if (file_idx + 1) % 10 == 0:
            _write_scores(c, all_scores)
            conn.commit()
            print(f"  --- Progress: {total_scored} scored ---")

    _write_scores(c, all_scores)
    conn.commit()

    if not all_scores:
        print("No hits scored!")
        conn.close()
        return

    score_values = np.array(list(all_scores.values()))
    print(f"\n{'='*60}")
    print(f"CLASSIFICATION COMPLETE")
    print(f"{'='*60}")
    print(f"Total scored: {len(all_scores)}")
    print(f"RFI probability: mean={np.mean(score_values):.4f}, std={np.std(score_values):.4f}")
    print(f"  < 0.3 (likely candidate): {np.sum(score_values < 0.3)}")
    print(f"  0.3-0.7 (uncertain):      {np.sum((score_values >= 0.3) & (score_values < 0.7))}")
    print(f"  >= 0.7 (likely RFI):      {np.sum(score_values >= 0.7)}")

    conn.close()
    print(f"\nScores written to DB ({db_path})")


def _write_scores(cursor, scores_dict):
    for hit_id, score in scores_dict.items():
        cls = 'rfi' if score >= 0.5 else 'candidate'
        cursor.execute(
            "UPDATE hits SET anomaly_score = ?, ml_class = ? WHERE id = ?",
            (score, cls, hit_id)
        )


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Classify hits with trained classifier')
    parser.add_argument('--checkpoint', default=None)
    parser.add_argument('--target', default='PROXCEN')
    parser.add_argument('--crop-size', type=int, default=128)
    parser.add_argument('--batch-size', type=int, default=512)
    parser.add_argument('--max-per-file', type=int, default=5000)
    args = parser.parse_args()

    ckpt = args.checkpoint or os.path.join(SETI_ROOT, 'ml', 'checkpoints', 'classifier_best.pt')
    if not os.path.isfile(ckpt):
        print(f"Checkpoint not found: {ckpt}")
        sys.exit(1)

    classify_all_hits(
        checkpoint_path=ckpt,
        target=args.target,
        batch_size=args.batch_size,
        max_per_file=args.max_per_file,
    )
