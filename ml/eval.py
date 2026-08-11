"""Evaluation: model performance metrics and visualization.

Loads a trained checkpoint and the cached crops, computes metrics,
generates plots (loss curves, score distributions, sample reconstructions).

Usage:
    python ml/eval.py --checkpoint checkpoints/autoencoder_best.pt
"""

import os
import sys
import argparse
import numpy as np

import torch

SETI_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SETI_ROOT)

from ml.models.autoencoder import WaterfallAutoencoder, compute_anomaly_score
from ml.train import load_crops


def evaluate_model(checkpoint_path, target='PROXCEN', device='auto'):
    """Full evaluation: metrics, plots, sample reconstructions."""
    if device == 'auto':
        device = 'cuda' if torch.cuda.is_available() else 'cpu'
    
    # Load checkpoint
    ckpt = torch.load(checkpoint_path, map_location=device, weights_only=False)
    config = ckpt['config']
    history = ckpt.get('history', {})
    model = WaterfallAutoencoder(**config).to(device)
    model.load_state_dict(ckpt['model_state'])
    model.eval()
    
    crop_size = config['crop_size']
    
    # Load crops
    crops, labels, hit_ids = load_crops(target, crop_size)
    crops_dev = crops.to(device)
    
    print(f"Evaluating {len(crops)} crops on {device}")
    print(f"Labels: {torch.sum(labels==0).item()} candidates, {torch.sum(labels==1).item()} RFI")
    
    # Compute anomaly scores
    batch_size = 512
    all_scores = []
    
    with torch.no_grad():
        for i in range(0, len(crops_dev), batch_size):
            batch = crops_dev[i:i+batch_size]
            scores = compute_anomaly_score(model, batch, device)
            all_scores.extend(scores.cpu().numpy())
    
    scores = np.array(all_scores)
    labels_np = labels.numpy()
    
    # Metrics
    print(f"\n=== Anomaly Score Statistics ===")
    print(f"Overall: mean={np.mean(scores):.6f}, std={np.std(scores):.6f}")
    print(f"Range: {np.min(scores):.6f} - {np.max(scores):.6f}")
    
    candidate_scores = scores[labels_np == 0]
    rfi_scores = scores[labels_np == 1]
    
    if len(candidate_scores) > 0:
        print(f"\nCandidate (ON-only): mean={np.mean(candidate_scores):.6f}, std={np.std(candidate_scores):.6f}")
    if len(rfi_scores) > 0:
        print(f"RFI (ON+OFF):       mean={np.mean(rfi_scores):.6f}, std={np.std(rfi_scores):.6f}")
    
    # Percentiles
    for p in [50, 75, 90, 95, 99]:
        print(f"  {p}th percentile: {np.percentile(scores, p):.6f}")
    
    # Generate plots
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        
        ckpt_dir = os.path.join(SETI_ROOT, 'ml', 'checkpoints')
        
        # Plot 1: Score distribution
        fig, axes = plt.subplots(1, 2, figsize=(14, 5))
        
        axes[0].hist(scores, bins=100, alpha=0.7, color='#4fc3f7')
        axes[0].set_xlabel('Anomaly Score (MSE)')
        axes[0].set_ylabel('Count')
        axes[0].set_title('Score Distribution')
        axes[0].set_yscale('log')
        
        if len(candidate_scores) > 0 and len(rfi_scores) > 0:
            axes[1].hist(candidate_scores, bins=50, alpha=0.6, label='Candidate', color='#66bb6a')
            axes[1].hist(rfi_scores, bins=50, alpha=0.6, label='RFI', color='#ef5350')
            axes[1].set_xlabel('Anomaly Score')
            axes[1].set_ylabel('Count')
            axes[1].set_title('Candidate vs RFI')
            axes[1].legend()
        else:
            axes[1].text(0.5, 0.5, 'Need both labels\nfor comparison', 
                        ha='center', va='center', transform=axes[1].transAxes)
        
        plt.tight_layout()
        plot_path = os.path.join(ckpt_dir, 'score_distribution.png')
        plt.savefig(plot_path, dpi=100)
        print(f"\nScore distribution plot: {plot_path}")
        
        # Plot 2: Training history (if available)
        if history:
            fig, ax = plt.subplots(figsize=(8, 4))
            ax.plot(history['train_loss'], label='Train')
            ax.plot(history['val_loss'], label='Val')
            ax.set_xlabel('Epoch')
            ax.set_ylabel('MSE Loss')
            ax.set_title('Training History')
            ax.legend()
            ax.set_yscale('log')
            plt.tight_layout()
            plot_path = os.path.join(ckpt_dir, 'training_history.png')
            plt.savefig(plot_path, dpi=100)
            print(f"Training history plot: {plot_path}")
        
        # Plot 3: Sample reconstructions (top 5 anomalies + 5 normal)
        n_samples = 5
        sorted_idx = np.argsort(scores)[::-1]  # highest score first
        
        fig, axes = plt.subplots(2, n_samples, figsize=(n_samples * 3, 6))
        
        # Top anomalies (highest score)
        for i in range(min(n_samples, len(sorted_idx))):
            idx = sorted_idx[i]
            crop = crops[idx, 0].cpu().numpy()
            with torch.no_grad():
                recon = model.reconstruct(crops[idx:idx+1].to(device))[0, 0].cpu().numpy()
            
            axes[0, i].imshow(crop, aspect='auto', cmap='viridis', origin='lower')
            axes[0, i].set_title(f'score={scores[idx]:.4f}', fontsize=9, color='#ef5350')
            axes[0, i].axis('off')
            
            axes[1, i].imshow(recon, aspect='auto', cmap='viridis', origin='lower')
            axes[1, i].axis('off')
        
        axes[0, 0].set_ylabel('Anomaly', fontsize=10, color='#ef5350')
        axes[1, 0].set_ylabel('Recon', fontsize=10)
        
        plt.suptitle('Top Anomalies: Original (top) vs Reconstruction (bottom)', fontsize=12)
        plt.tight_layout()
        plot_path = os.path.join(ckpt_dir, 'sample_reconstructions.png')
        plt.savefig(plot_path, dpi=100)
        print(f"Sample reconstructions: {plot_path}")
        
    except Exception as e:
        print(f"Plot generation failed: {e}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Evaluate trained autoencoder')
    parser.add_argument('--checkpoint', default=None)
    parser.add_argument('--target', default='PROXCEN')
    args = parser.parse_args()
    
    ckpt = args.checkpoint or os.path.join(SETI_ROOT, 'ml', 'checkpoints', 'autoencoder_best.pt')
    if not os.path.isfile(ckpt):
        print(f"Checkpoint not found: {ckpt}")
        sys.exit(1)
    
    evaluate_model(ckpt, target=args.target)
