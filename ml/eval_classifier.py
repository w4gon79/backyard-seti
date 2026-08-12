"""Evaluate trained classifier: metrics, plots, confusion matrix.

Usage:
    python ml/eval_classifier.py --checkpoint checkpoints/classifier_best.pt
"""

import os
import sys
import argparse
import numpy as np

import torch
from sklearn.metrics import (classification_report, confusion_matrix, 
                              roc_auc_score, roc_curve, precision_recall_curve,
                              average_precision_score)

SETI_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SETI_ROOT)

from ml.models.classifier import WaterfallClassifier
from ml.train_classifier import load_clean_crops


def evaluate_classifier(checkpoint_path, target='PROXCEN', crop_size=64, snr_min=8, device='auto'):
    """Full evaluation with metrics and plots."""
    if device == 'auto':
        device = 'cuda' if torch.cuda.is_available() else 'cpu'
    
    ckpt = torch.load(checkpoint_path, map_location=device, weights_only=False)
    config = ckpt['config']
    history = ckpt.get('history', {})
    model = WaterfallClassifier(**config).to(device)
    model.load_state_dict(ckpt['model_state'])
    model.eval()
    
    print(f"Checkpoint: {checkpoint_path}")
    print(f"Best val accuracy: {ckpt.get('best_val_acc', '?')}")
    print(f"Best val F1: {ckpt.get('best_val_f1', '?')}")
    
    # Load crops
    crops, labels, hit_ids = load_clean_crops(target, config['crop_size'], snr_min)
    crops_dev = crops.to(device)
    
    print(f"\nEvaluating {len(crops)} crops on {device}")
    
    # Get probabilities
    batch_size = 512
    all_probs = []
    
    with torch.no_grad():
        for i in range(0, len(crops_dev), batch_size):
            batch = crops_dev[i:i+batch_size]
            logits = model(batch)
            probs = torch.sigmoid(logits)
            all_probs.extend(probs.cpu().numpy())
    
    probs = np.array(all_probs)
    labels_np = labels.numpy().astype(int)
    preds = (probs > 0.5).astype(int)
    
    # Metrics
    print(f"\n{'='*60}")
    print("CLASSIFICATION REPORT")
    print(f"{'='*60}")
    print(classification_report(labels_np, preds, target_names=['candidate', 'rfi']))
    
    # Confusion matrix
    cm = confusion_matrix(labels_np, preds)
    print(f"Confusion Matrix:")
    print(f"                Pred Candidate  Pred RFI")
    print(f"  Actual Cand       {cm[0,0]:>10}     {cm[0,1]:>8}")
    print(f"  Actual RFI        {cm[1,0]:>10}     {cm[1,1]:>8}")
    
    # ROC AUC
    auc = roc_auc_score(labels_np, probs)
    ap = average_precision_score(labels_np, probs)
    print(f"\nROC AUC: {auc:.4f}")
    print(f"Average Precision: {ap:.4f}")
    
    # Score distribution by class
    cand_probs = probs[labels_np == 0]
    rfi_probs = probs[labels_np == 1]
    print(f"\nCandidate probabilities: mean={np.mean(cand_probs):.4f}, std={np.std(cand_probs):.4f}")
    print(f"RFI probabilities:       mean={np.mean(rfi_probs):.4f}, std={np.std(rfi_probs):.4f}")
    
    # Plots
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        
        ckpt_dir = os.path.join(SETI_ROOT, 'ml', 'checkpoints')
        
        # Plot 1: Score distribution
        fig, axes = plt.subplots(1, 2, figsize=(14, 5))
        axes[0].hist(probs, bins=50, alpha=0.7, color='#4fc3f7')
        axes[0].set_xlabel('RFI Probability')
        axes[0].set_ylabel('Count')
        axes[0].set_title('Overall Score Distribution')
        
        axes[1].hist(cand_probs, bins=30, alpha=0.6, label='Candidate', color='#66bb6a')
        axes[1].hist(rfi_probs, bins=30, alpha=0.6, label='RFI', color='#ef5350')
        axes[1].set_xlabel('RFI Probability')
        axes[1].set_ylabel('Count')
        axes[1].set_title('Candidate vs RFI Separation')
        axes[1].legend()
        plt.tight_layout()
        plt.savefig(os.path.join(ckpt_dir, 'classifier_score_distribution.png'), dpi=100)
        print(f"\nScore distribution: {ckpt_dir}/classifier_score_distribution.png")
        
        # Plot 2: Training history
        if history:
            fig, axes = plt.subplots(2, 2, figsize=(14, 10))
            axes[0,0].plot(history['train_loss'], label='Train')
            axes[0,0].plot(history['val_loss'], label='Val')
            axes[0,0].set_title('Loss')
            axes[0,0].legend()
            
            axes[0,1].plot(history['train_acc'], label='Train')
            axes[0,1].plot(history['val_acc'], label='Val')
            axes[0,1].set_title('Accuracy')
            axes[0,1].legend()
            
            axes[1,0].plot(history['val_f1'], label='Val F1', color='green')
            axes[1,0].set_title('Validation F1')
            axes[1,0].legend()
            
            axes[1,1].plot(history['lr'])
            axes[1,1].set_title('LR Schedule')
            
            plt.suptitle('Classifier Training History', fontsize=14)
            plt.tight_layout()
            plt.savefig(os.path.join(ckpt_dir, 'classifier_training_history.png'), dpi=100)
            print(f"Training history: {ckpt_dir}/classifier_training_history.png")
        
        # Plot 3: ROC curve
        fpr, tpr, _ = roc_curve(labels_np, probs)
        fig, ax = plt.subplots(figsize=(6, 6))
        ax.plot(fpr, tpr, label=f'ROC (AUC={auc:.3f})', color='#4fc3f7')
        ax.plot([0, 1], [0, 1], '--', color='gray', alpha=0.5)
        ax.set_xlabel('False Positive Rate')
        ax.set_ylabel('True Positive Rate')
        ax.set_title('ROC Curve')
        ax.legend()
        plt.tight_layout()
        plt.savefig(os.path.join(ckpt_dir, 'classifier_roc.png'), dpi=100)
        print(f"ROC curve: {ckpt_dir}/classifier_roc.png")
        
        # Plot 4: Precision-Recall curve
        precision, recall, _ = precision_recall_curve(labels_np, probs)
        fig, ax = plt.subplots(figsize=(6, 6))
        ax.plot(recall, precision, label=f'AP={ap:.3f}', color='#66bb6a')
        ax.set_xlabel('Recall')
        ax.set_ylabel('Precision')
        ax.set_title('Precision-Recall Curve')
        ax.legend()
        plt.tight_layout()
        plt.savefig(os.path.join(ckpt_dir, 'classifier_pr.png'), dpi=100)
        print(f"PR curve: {ckpt_dir}/classifier_pr.png")
        
    except Exception as e:
        print(f"Plot generation failed: {e}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Evaluate trained classifier')
    parser.add_argument('--checkpoint', default=None)
    parser.add_argument('--target', default='PROXCEN')
    parser.add_argument('--snr-min', type=float, default=8)
    args = parser.parse_args()
    
    ckpt = args.checkpoint or os.path.join(SETI_ROOT, 'ml', 'checkpoints', 'classifier_best.pt')
    if not os.path.isfile(ckpt):
        print(f"Checkpoint not found: {ckpt}")
        sys.exit(1)
    
    evaluate_classifier(ckpt, target=args.target, snr_min=args.snr_min)
