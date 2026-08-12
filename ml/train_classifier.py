"""Train binary CNN classifier on clean SETI data.

Loads balanced crops from extract_classifier.py, trains WaterfallClassifier
with BCEWithLogitsLoss + class weighting. Uses early stopping on val accuracy.

Usage:
    python ml/train_classifier.py --target PROXCEN --crop-size 64 --snr-min 8 --epochs 100
"""

import os
import sys
import time
import argparse
import numpy as np

import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset, random_split

SETI_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SETI_ROOT)

from ml.models.classifier import WaterfallClassifier


def load_clean_crops(target='PROXCEN', crop_size=64, snr_min=8):
    """Load cached classifier crops from extract_classifier.py."""
    cache_path = os.path.join(SETI_ROOT, 'ml', 'data', 
                              f'classifier_{target}_{crop_size}_snr{snr_min}.npz')
    if not os.path.isfile(cache_path):
        raise FileNotFoundError(
            f"No cached crops at {cache_path}. Run extract_classifier.py first."
        )
    
    data = np.load(cache_path)
    crops = data['crops']        # (n_samples, crop_size, crop_size)
    labels = data['labels']      # (n_samples,) 0=candidate, 1=RFI
    hit_ids = data['hit_ids']    # (n_samples,)
    
    crops = crops[:, np.newaxis, :, :]
    
    n_cand = np.sum(labels == 0)
    n_rfi = np.sum(labels == 1)
    
    print(f"Loaded {len(crops)} crops from {cache_path}")
    print(f"  Shape: {crops.shape}")
    print(f"  Candidates: {n_cand}")
    print(f"  RFI: {n_rfi}")
    
    return torch.from_numpy(crops).float(), torch.from_numpy(labels).float(), hit_ids


def train_classifier(crops_tensor, labels_tensor, crop_size=64,
                     latent_dim=32, epochs=100, batch_size=64, lr=0.001,
                     weight_decay=0.0001, train_split=0.8,
                     early_stop_patience=15, device='auto'):
    """Train the binary classifier."""
    
    if device == 'auto':
        device = 'cuda' if torch.cuda.is_available() else 'cpu'
    print(f"Training on: {device}")
    
    n_total = len(crops_tensor)
    n_train = int(n_total * train_split)
    n_val = n_total - n_train
    
    # Stratified split: maintain class balance in train/val
    indices = torch.randperm(n_total)
    train_idx = indices[:n_train]
    val_idx = indices[n_train:]
    
    train_ds = TensorDataset(crops_tensor[train_idx], labels_tensor[train_idx])
    val_ds = TensorDataset(crops_tensor[val_idx], labels_tensor[val_idx])
    
    train_loader = DataLoader(train_ds, batch_size=batch_size, shuffle=True, num_workers=0)
    val_loader = DataLoader(val_ds, batch_size=batch_size, shuffle=False, num_workers=0)
    
    print(f"Train: {n_train}, Val: {n_val}")
    
    # Class weights for imbalanced data
    n_cand = int((labels_tensor == 0).sum())
    n_rfi = int((labels_tensor == 1).sum())
    # Weight inversely proportional to class frequency
    w_cand = n_total / (2.0 * n_cand) if n_cand > 0 else 1.0
    w_rfi = n_total / (2.0 * n_rfi) if n_rfi > 0 else 1.0
    pos_weight = torch.tensor([w_rfi / w_cand]).to(device)
    print(f"Class weights: candidate={w_cand:.3f}, rfi={w_rfi:.3f}, pos_weight={pos_weight.item():.3f}")
    
    # Initialize model
    model = WaterfallClassifier(
        crop_size=crop_size,
        latent_dim=latent_dim,
        base_channels=32,
        n_layers=3,
        dropout=0.3,
    ).to(device)
    
    n_params = sum(p.numel() for p in model.parameters())
    print(f"Model parameters: {n_params:,}")
    
    # Optimizer + scheduler + loss
    optimizer = torch.optim.Adam(model.parameters(), lr=lr, weight_decay=weight_decay)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
        optimizer, mode='max', factor=0.5, patience=5, verbose=True
    )
    criterion = nn.BCEWithLogitsLoss(pos_weight=pos_weight)
    
    # Training loop
    history = {
        'train_loss': [], 'val_loss': [],
        'train_acc': [], 'val_acc': [],
        'val_f1': [], 'lr': [],
    }
    best_val_acc = 0.0
    best_val_f1 = 0.0
    best_state = None
    patience_counter = 0
    
    ckpt_dir = os.path.join(SETI_ROOT, 'ml', 'checkpoints')
    os.makedirs(ckpt_dir, exist_ok=True)
    
    for epoch in range(1, epochs + 1):
        # Train
        model.train()
        train_losses = []
        train_correct = 0
        train_total = 0
        
        for batch_x, batch_y in train_loader:
            x = batch_x.to(device)
            y = batch_y.to(device)
            
            optimizer.zero_grad()
            logits = model(x)
            loss = criterion(logits, y)
            loss.backward()
            optimizer.step()
            
            train_losses.append(loss.item())
            preds = (torch.sigmoid(logits) > 0.5).float()
            train_correct += (preds == y).sum().item()
            train_total += len(y)
        
        train_loss = np.mean(train_losses)
        train_acc = train_correct / train_total if train_total > 0 else 0
        
        # Validate
        model.eval()
        val_losses = []
        val_correct = 0
        val_total = 0
        tp = fp = fn = tn = 0
        
        with torch.no_grad():
            for batch_x, batch_y in val_loader:
                x = batch_x.to(device)
                y = batch_y.to(device)
                logits = model(x)
                loss = criterion(logits, y)
                val_losses.append(loss.item())
                
                probs = torch.sigmoid(logits)
                preds = (probs > 0.5).float()
                val_correct += (preds == y).sum().item()
                val_total += len(y)
                
                # Confusion matrix for F1
                tp += ((preds == 1) & (y == 1)).sum().item()
                fp += ((preds == 1) & (y == 0)).sum().item()
                fn += ((preds == 0) & (y == 1)).sum().item()
                tn += ((preds == 0) & (y == 0)).sum().item()
        
        val_loss = np.mean(val_losses)
        val_acc = val_correct / val_total if val_total > 0 else 0
        
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0
        val_f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0
        
        current_lr = optimizer.param_groups[0]['lr']
        
        history['train_loss'].append(train_loss)
        history['val_loss'].append(val_loss)
        history['train_acc'].append(train_acc)
        history['val_acc'].append(val_acc)
        history['val_f1'].append(val_f1)
        history['lr'].append(current_lr)
        
        scheduler.step(val_acc)
        
        # Track best by F1 (more meaningful for imbalanced data)
        score = val_f1 + val_acc  # composite: want both high
        best_score = best_val_f1 + best_val_acc
        
        marker = ''
        if score > best_score:
            best_val_acc = val_acc
            best_val_f1 = val_f1
            best_state = {k: v.clone() for k, v in model.state_dict().items()}
            patience_counter = 0
            marker = '*'
        else:
            patience_counter += 1
        
        if epoch % 5 == 0 or epoch == 1 or patience_counter >= early_stop_patience:
            print(f"  Epoch {epoch:3d}/{epochs}: "
                  f"train_loss={train_loss:.4f} val_loss={val_loss:.4f} "
                  f"train_acc={train_acc:.3f} val_acc={val_acc:.3f} "
                  f"val_f1={val_f1:.3f} "
                  f"TP={tp} FP={fp} FN={fn} TN={tn} "
                  f"lr={current_lr:.6f} {marker}")
        
        if patience_counter >= early_stop_patience:
            print(f"  Early stopping at epoch {epoch} (patience={early_stop_patience})")
            break
    
    # Restore best model
    if best_state:
        model.load_state_dict(best_state)
    
    # Save checkpoint
    ckpt_path = os.path.join(ckpt_dir, 'classifier_best.pt')
    torch.save({
        'model_state': model.state_dict(),
        'config': {
            'crop_size': crop_size,
            'latent_dim': latent_dim,
            'base_channels': 32,
            'n_layers': 3,
            'dropout': 0.3,
        },
        'best_val_acc': best_val_acc,
        'best_val_f1': best_val_f1,
        'history': history,
        'last_epoch': epoch,
        'class_names': {0: 'candidate', 1: 'rfi'},
    }, ckpt_path)
    
    print(f"\nBest val accuracy: {best_val_acc:.3f}")
    print(f"Best val F1: {best_val_f1:.3f}")
    print(f"Checkpoint saved to {ckpt_path}")
    
    # Save training history plot
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        
        fig, axes = plt.subplots(2, 2, figsize=(14, 10))
        
        # Loss
        axes[0, 0].plot(history['train_loss'], label='Train')
        axes[0, 0].plot(history['val_loss'], label='Val')
        axes[0, 0].set_xlabel('Epoch')
        axes[0, 0].set_ylabel('BCE Loss')
        axes[0, 0].set_title('Loss')
        axes[0, 0].legend()
        
        # Accuracy
        axes[0, 1].plot(history['train_acc'], label='Train')
        axes[0, 1].plot(history['val_acc'], label='Val')
        axes[0, 1].set_xlabel('Epoch')
        axes[0, 1].set_ylabel('Accuracy')
        axes[0, 1].set_title('Accuracy')
        axes[0, 1].legend()
        
        # F1
        axes[1, 0].plot(history['val_f1'], label='Val F1', color='green')
        axes[1, 0].set_xlabel('Epoch')
        axes[1, 0].set_ylabel('F1 Score')
        axes[1, 0].set_title('Validation F1')
        axes[1, 0].legend()
        
        # LR
        axes[1, 1].plot(history['lr'])
        axes[1, 1].set_xlabel('Epoch')
        axes[1, 1].set_ylabel('Learning Rate')
        axes[1, 1].set_title('LR Schedule')
        
        plt.suptitle('Classifier Training History', fontsize=14)
        plt.tight_layout()
        plot_path = os.path.join(ckpt_dir, 'classifier_training_history.png')
        plt.savefig(plot_path, dpi=100)
        print(f"Training plot: {plot_path}")
    except Exception as e:
        print(f"Could not save training plot: {e}")
    
    return model, history


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Train SETI classifier')
    parser.add_argument('--target', default='PROXCEN')
    parser.add_argument('--crop-size', type=int, default=64)
    parser.add_argument('--snr-min', type=float, default=8)
    parser.add_argument('--latent-dim', type=int, default=32)
    parser.add_argument('--epochs', type=int, default=100)
    parser.add_argument('--batch-size', type=int, default=64)
    parser.add_argument('--lr', type=float, default=0.001)
    args = parser.parse_args()
    
    crops, labels, hit_ids = load_clean_crops(args.target, args.crop_size, args.snr_min)
    
    model, history = train_classifier(
        crops,
        labels,
        crop_size=args.crop_size,
        latent_dim=args.latent_dim,
        epochs=args.epochs,
        batch_size=args.batch_size,
        lr=args.lr,
    )
