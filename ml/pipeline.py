"""Full ML pipeline: extract -> train -> score.

Runs all three phases sequentially. Useful for first-time setup or
periodic retraining.

Usage:
    python ml/pipeline.py --target PROXCEN
    python ml/pipeline.py --target PROXCEN --crop-size 64 --epochs 50
"""

import os
import sys
import time
import argparse

SETI_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SETI_ROOT)

from ml.extract import extract_all_crops
from ml.train import load_crops, train_autoencoder
from ml.infer import score_all_hits


def run_pipeline(target='PROXCEN', crop_size=64, epochs=50, 
                 latent_dim=32, batch_size=256, lr=0.001,
                 top_percent=5, max_per_file=5000):
    """Run the full ML pipeline end-to-end."""
    
    total_start = time.time()
    
    # ─── Phase 1: Extract ────────────────────────────────────────────
    print("=" * 60)
    print("PHASE 1: DATA EXTRACTION")
    print("=" * 60)
    
    t0 = time.time()
    crops, hit_ids = extract_all_crops(
        target=target,
        crop_size=crop_size,
        max_per_file=max_per_file,
    )
    
    if crops is None:
        print("ERROR: No crops extracted. Aborting pipeline.")
        return
    
    print(f"\nExtraction complete: {time.time()-t0:.1f}s\n")
    
    # ─── Phase 2: Train ──────────────────────────────────────────────
    print("=" * 60)
    print("PHASE 2: TRAINING")
    print("=" * 60)
    
    t0 = time.time()
    crops_tensor, labels, hit_ids = load_crops(target, crop_size)
    
    model, history = train_autoencoder(
        crops_tensor,
        crop_size=crop_size,
        latent_dim=latent_dim,
        epochs=epochs,
        batch_size=batch_size,
        lr=lr,
    )
    
    print(f"\nTraining complete: {time.time()-t0:.1f}s\n")
    
    # ─── Phase 3: Score ──────────────────────────────────────────────
    print("=" * 60)
    print("PHASE 3: INFERENCE")
    print("=" * 60)
    
    t0 = time.time()
    ckpt_path = os.path.join(SETI_ROOT, 'ml', 'checkpoints', 'autoencoder_best.pt')
    
    score_all_hits(
        checkpoint_path=ckpt_path,
        target=target,
        top_percent=top_percent,
    )
    
    print(f"\nInference complete: {time.time()-t0:.1f}s\n")
    
    # ─── Summary ─────────────────────────────────────────────────────
    total_time = time.time() - total_start
    print("=" * 60)
    print(f"PIPELINE COMPLETE: {total_time:.1f}s ({total_time/60:.1f} min)")
    print("=" * 60)
    
    # ─── Phase 4: Evaluate ───────────────────────────────────────────
    print("\nGenerating evaluation plots...")
    from ml.eval import evaluate_model
    evaluate_model(ckpt_path, target=target)
    
    print("\nDone. Check ml/checkpoints/ for model + plots.")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Run full ML pipeline')
    parser.add_argument('--target', default='PROXCEN')
    parser.add_argument('--crop-size', type=int, default=64)
    parser.add_argument('--epochs', type=int, default=50)
    parser.add_argument('--latent-dim', type=int, default=32)
    parser.add_argument('--batch-size', type=int, default=256)
    parser.add_argument('--lr', type=float, default=0.001)
    parser.add_argument('--top-percent', type=float, default=5)
    parser.add_argument('--max-per-file', type=int, default=5000)
    args = parser.parse_args()
    
    run_pipeline(
        target=args.target,
        crop_size=args.crop_size,
        epochs=args.epochs,
        latent_dim=args.latent_dim,
        batch_size=args.batch_size,
        lr=args.lr,
        top_percent=args.top_percent,
        max_per_file=args.max_per_file,
    )
