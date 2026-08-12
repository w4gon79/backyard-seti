"""Train binary CNN classifier on clean SETI data - v2.

Key changes from v1:
  - Supports 128x128 crops (more visual context)
  - Passes metadata (drift_rate, snr, freq) to the model
  - Data augmentation (horizontal flip, noise, brightness jitter)
  - Deeper head with more dropout
  - Cosine annealing LR schedule

Usage:
    python ml/train_classifier.py --target PROXCEN --crop-size 128 --snr-min 8 --epochs 200
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


def load_clean_crops(target='PROXCEN', crop_size=128, snr_min=8):
    """Load cached classifier crops + metadata from extract_classifier.py."""
    cache_path = os.path.join(SETI_ROOT, 'ml', 'data',
                              f'classifier_{target}_{crop_size}_snr{snr_min}.npz')
    if not os.path.isfile(cache_path):
        raise FileNotFoundError(
            f"No cached crops at {cache_path}. Run extract_classifier.py first."
        )

    data = np.load(cache_path, allow_pickle=True)
    crops = data['crops']        # (n_samples, crop_size, crop_size)
    labels = data['labels']      # (n_samples,) 0=candidate, 1=RFI
    hit_ids = data['hit_ids']    # (n_samples,)

    crops = crops[:, np.newaxis, :, :]

    # Load metadata if available (v2 format)
    if 'metadata' in data:
        metadata = data['metadata']
        print(f"Loaded metadata: {metadata.shape}")
    else:
        metadata = np.zeros((len(crops), 3), dtype=np.float32)
        print("WARNING: No metadata in cache file. Using zeros.")

    n_cand = int(np.sum(labels == 0))
    n_rfi = int(np.sum(labels == 1))

    print(f"Loaded {len(crops)} crops from {cache_path}")
    print(f"  Shape: {crops.shape}")
    print(f"  Candidates: {n_cand}")
    print(f"  RFI: {n_rfi}")

    return (torch.from_numpy(crops).float(),
            torch.from_numpy(labels).float(),
            torch.from_numpy(metadata).float(),
            hit_ids)


def augment_batch(x, meta):
    """Apply random augmentation to a batch of crops.

    Augmentations:
      - Random horizontal flip (50%)
      - Gaussian noise (sigma=0.02, 50% chance)
      - Brightness jitter (±10%, 50% chance)

    Metadata is NOT augmented (it's ground truth for each hit).
    """
    batch_size = x.size(0)

    # Horizontal flip
    flip_mask = torch.rand(batch_size) < 0.5
    x[flip_mask] = torch.flip(x[flip_mask], dims=[3])

    # Gaussian noise
    noise_mask = torch.rand(batch_size) < 0.5
    if noise_mask.any():
        noise = torch.randn_like(x[noise_mask]) * 0.02
        x[noise_mask] = torch.clamp(x[noise_mask] + noise, 0, 1)

    # Brightness jitter
    bright_mask = torch.rand(batch_size) < 0.5
    if bright_mask.any():
        factors = 0.9 + 0.2 * torch.rand((bright_mask.sum(), 1, 1, 1),
                                         device=x.device)
        x[bright_mask] = torch.clamp(x[bright_mask] * factors, 0, 1)

    return x, meta


def train_classifier(crops_tensor, labels_tensor, metadata_tensor,
                     crop_size=128, latent_dim=64, epochs=200, batch_size=32,
                     lr=0.001, weight_decay=0.001, train_split=0.8,
                     early_stop_patience=20, device='auto', augment=True):
    """Train the binary classifier with metadata + augmentation."""

    if device == 'auto':
        device = 'cuda' if torch.cuda.is_available() else 'cpu'
    print(f"Training on: {device}")
    print(f"Crop size: {crop_size}x{crop_size}")
    print(f"Augmentation: {'ON' if augment else 'OFF'}")

    n_total = len(crops_tensor)
    n_train = int(n_total * train_split)
    n_val = n_total - n_train

    # Stratified-ish split (random, but with small data it's fine)
    indices = torch.randperm(n_total)
    train_idx = indices[:n_train]
    val_idx = indices[n_train:]

    # Full dataset returns (crop, label, metadata)
    full_data = TensorDataset(crops_tensor, labels_tensor, metadata_tensor)
    train_ds = TensorDataset(
        crops_tensor[train_idx], labels_tensor[train_idx], metadata_tensor[train_idx]
    )
    val_ds = TensorDataset(
        crops_tensor[val_idx], labels_tensor[val_idx], metadata_tensor[val_idx]
    )

    train_loader = DataLoader(train_ds, batch_size=batch_size, shuffle=True, num_workers=0)
    val_loader = DataLoader(val_ds, batch_size=batch_size, shuffle=False, num_workers=0)

    print(f"Train: {n_train}, Val: {n_val}")

    # Class weights
    n_cand = int((labels_tensor == 0).sum())
    n_rfi = int((labels_tensor == 1).sum())
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
        dropout=0.4,
        metadata_dim=metadata_tensor.size(1),
    ).to(device)

    n_params = sum(p.numel() for p in model.parameters())
    print(f"Model parameters: {n_params:,}")

    # Optimizer + scheduler + loss
    optimizer = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=weight_decay)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=epochs, eta_min=1e-6)
    criterion = nn.BCEWithLogitsLoss(pos_weight=pos_weight)

    # Training loop
    history = {
        'train_loss': [], 'val_loss': [],
        'train_acc': [], 'val_acc': [],
        'val_f1': [], 'lr': [],
    }
    best_score = 0.0
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

        for batch_x, batch_y, batch_meta in train_loader:
            x = batch_x.to(device)
            y = batch_y.to(device)
            meta = batch_meta.to(device)

            if augment:
                x, meta = augment_batch(x.clone(), meta)

            optimizer.zero_grad()
            logits = model(x, meta)
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
            for batch_x, batch_y, batch_meta in val_loader:
                x = batch_x.to(device)
                y = batch_y.to(device)
                meta = batch_meta.to(device)
                logits = model(x, meta)
                loss = criterion(logits, y)
                val_losses.append(loss.item())

                preds = (torch.sigmoid(logits) > 0.5).float()
                val_correct += (preds == y).sum().item()
                val_total += len(y)

                tp += int(((preds == 1) & (y == 1)).sum())
                fp += int(((preds == 1) & (y == 0)).sum())
                fn += int(((preds == 0) & (y == 1)).sum())
                tn += int(((preds == 0) & (y == 0)).sum())

        val_loss = np.mean(val_losses)
        val_acc = val_correct / val_total if val_total > 0 else 0

        precision = tp / (tp + fp) if (tp + fp) > 0 else 0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0
        val_f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0

        current_lr = optimizer.param_groups[0]['lr']
        scheduler.step()

        history['train_loss'].append(train_loss)
        history['val_loss'].append(val_loss)
        history['train_acc'].append(train_acc)
        history['val_acc'].append(val_acc)
        history['val_f1'].append(val_f1)
        history['lr'].append(current_lr)

        # Track best by composite score (F1 + accuracy)
        score = val_f1 + val_acc
        marker = ''
        if score > best_score:
            best_score = score
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
            'dropout': 0.4,
            'metadata_dim': metadata_tensor.size(1),
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

        axes[0, 0].plot(history['train_loss'], label='Train')
        axes[0, 0].plot(history['val_loss'], label='Val')
        axes[0, 0].set_xlabel('Epoch')
        axes[0, 0].set_ylabel('BCE Loss')
        axes[0, 0].set_title('Loss')
        axes[0, 0].legend()

        axes[0, 1].plot(history['train_acc'], label='Train')
        axes[0, 1].plot(history['val_acc'], label='Val')
        axes[0, 1].set_xlabel('Epoch')
        axes[0, 1].set_ylabel('Accuracy')
        axes[0, 1].set_title('Accuracy')
        axes[0, 1].legend()

        axes[1, 0].plot(history['val_f1'], label='Val F1', color='green')
        axes[1, 0].set_xlabel('Epoch')
        axes[1, 0].set_ylabel('F1 Score')
        axes[1, 0].set_title('Validation F1')
        axes[1, 0].legend()

        axes[1, 1].plot(history['lr'])
        axes[1, 1].set_xlabel('Epoch')
        axes[1, 1].set_ylabel('Learning Rate')
        axes[1, 1].set_title('LR Schedule (Cosine Annealing)')

        plt.suptitle(f'Classifier v2 Training ({crop_size}x{crop_size} + metadata + augmentation)', fontsize=14)
        plt.tight_layout()
        plot_path = os.path.join(ckpt_dir, 'classifier_training_history.png')
        plt.savefig(plot_path, dpi=100)
        print(f"Training plot: {plot_path}")
    except Exception as e:
        print(f"Could not save training plot: {e}")

    return model, history


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Train SETI classifier v2')
    parser.add_argument('--target', default='PROXCEN')
    parser.add_argument('--crop-size', type=int, default=128)
    parser.add_argument('--snr-min', type=float, default=8)
    parser.add_argument('--latent-dim', type=int, default=64)
    parser.add_argument('--epochs', type=int, default=200)
    parser.add_argument('--batch-size', type=int, default=32)
    parser.add_argument('--lr', type=float, default=0.001)
    parser.add_argument('--no-augment', action='store_true', help='Disable data augmentation')
    args = parser.parse_args()

    crops, labels, metadata, hit_ids = load_clean_crops(args.target, args.crop_size, args.snr_min)

    model, history = train_classifier(
        crops,
        labels,
        metadata,
        crop_size=args.crop_size,
        latent_dim=args.latent_dim,
        epochs=args.epochs,
        batch_size=args.batch_size,
        lr=args.lr,
        augment=not args.no_augment,
    )
