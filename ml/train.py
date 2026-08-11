"""Train autoencoder on SETI waterfall crops.

Loads cached crops from extract.py, trains a convolutional autoencoder,
saves checkpoints. Uses early stopping on validation loss.

Usage:
    python ml/train.py --target PROXCEN --crop-size 64 --epochs 50
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

from ml.models.autoencoder import WaterfallAutoencoder


def load_crops(target='PROXCEN', crop_size=64):
    """Load cached crops from extract.py output."""
    cache_path = os.path.join(SETI_ROOT, 'ml', 'data', f'crops_{target}_{crop_size}.npz')
    if not os.path.isfile(cache_path):
        raise FileNotFoundError(f"No cached crops at {cache_path}. Run extract.py first.")
    
    data = np.load(cache_path)
    crops = data['crops']      # (n_samples, crop_size, crop_size)
    labels = data['labels']    # (n_samples,) 0=candidate, 1=RFI
    hit_ids = data['hit_ids']  # (n_samples,)
    
    # Add channel dimension: (n_samples, 1, crop_size, crop_size)
    crops = crops[:, np.newaxis, :, :]
    
    print(f"Loaded {len(crops)} crops from {cache_path}")
    print(f"  Shape: {crops.shape}")
    print(f"  Labels: {np.sum(labels==0)} candidates, {np.sum(labels==1)} RFI")
    
    return torch.from_numpy(crops), torch.from_numpy(labels), hit_ids


def train_autoencoder(crops_tensor, crop_size=64, latent_dim=32, 
                      epochs=50, batch_size=256, lr=0.001, 
                      weight_decay=0.0001, train_split=0.8,
                      early_stop_patience=10, device='auto'):
    """Train the autoencoder and return the model + training history."""
    
    # Device selection
    if device == 'auto':
        device = 'cuda' if torch.cuda.is_available() else 'cpu'
    print(f"Training on: {device}")
    
    # Split into train/val
    n_total = len(crops_tensor)
    n_train = int(n_total * train_split)
    n_val = n_total - n_train
    
    dataset = TensorDataset(crops_tensor)
    train_ds, val_ds = random_split(dataset, [n_train, n_val])
    
    train_loader = DataLoader(train_ds, batch_size=batch_size, shuffle=True, num_workers=0)
    val_loader = DataLoader(val_ds, batch_size=batch_size, shuffle=False, num_workers=0)
    
    print(f"Train: {n_train}, Val: {n_val}")
    
    # Initialize model
    model = WaterfallAutoencoder(
        crop_size=crop_size,
        latent_dim=latent_dim,
        base_channels=32,
        n_layers=3,
    ).to(device)
    
    n_params = sum(p.numel() for p in model.parameters())
    print(f"Model parameters: {n_params:,}")
    
    # Optimizer + scheduler + loss
    optimizer = torch.optim.Adam(model.parameters(), lr=lr, weight_decay=weight_decay)
    scheduler = torch.optim.lr_scheduler.StepLR(optimizer, step_size=10, gamma=0.5)
    criterion = nn.MSELoss()
    
    # Training loop
    history = {'train_loss': [], 'val_loss': [], 'lr': []}
    best_val_loss = float('inf')
    best_state = None
    patience_counter = 0
    
    ckpt_dir = os.path.join(SETI_ROOT, 'ml', 'checkpoints')
    os.makedirs(ckpt_dir, exist_ok=True)
    
    for epoch in range(1, epochs + 1):
        # Train
        model.train()
        train_losses = []
        for batch in train_loader:
            x = batch[0].to(device)
            optimizer.zero_grad()
            recon, _ = model(x)
            loss = criterion(recon, x)
            loss.backward()
            optimizer.step()
            train_losses.append(loss.item())
        
        train_loss = np.mean(train_losses)
        
        # Validate
        model.eval()
        val_losses = []
        with torch.no_grad():
            for batch in val_loader:
                x = batch[0].to(device)
                recon = model.reconstruct(x)
                loss = criterion(recon, x)
                val_losses.append(loss.item())
        
        val_loss = np.mean(val_losses)
        current_lr = optimizer.param_groups[0]['lr']
        
        history['train_loss'].append(train_loss)
        history['val_loss'].append(val_loss)
        history['lr'].append(current_lr)
        
        scheduler.step()
        
        # Save best
        if val_loss < best_val_loss:
            best_val_loss = val_loss
            best_state = {k: v.clone() for k, v in model.state_dict().items()}
            patience_counter = 0
        else:
            patience_counter += 1
        
        # Print progress
        if epoch % 5 == 0 or epoch == 1 or patience_counter >= early_stop_patience:
            print(f"  Epoch {epoch:3d}/{epochs}: train={train_loss:.6f} val={val_loss:.6f} lr={current_lr:.6f} {'*' if val_loss == best_val_loss else ''}")
        
        # Early stopping
        if patience_counter >= early_stop_patience:
            print(f"  Early stopping at epoch {epoch} (patience={early_stop_patience})")
            break
    
    # Restore best model
    if best_state:
        model.load_state_dict(best_state)
    
    # Save checkpoint
    ckpt_path = os.path.join(ckpt_dir, 'autoencoder_best.pt')
    torch.save({
        'model_state': model.state_dict(),
        'config': {
            'crop_size': crop_size,
            'latent_dim': latent_dim,
            'base_channels': 32,
            'n_layers': 3,
        },
        'best_val_loss': best_val_loss,
        'history': history,
    }, ckpt_path)
    
    print(f"\nBest val loss: {best_val_loss:.6f}")
    print(f"Checkpoint saved to {ckpt_path}")
    
    # Save training history plot
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        
        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 4))
        ax1.plot(history['train_loss'], label='Train')
        ax1.plot(history['val_loss'], label='Val')
        ax1.set_xlabel('Epoch')
        ax1.set_ylabel('MSE Loss')
        ax1.set_title('Training Loss')
        ax1.legend()
        ax1.set_yscale('log')
        
        ax2.plot(history['lr'])
        ax2.set_xlabel('Epoch')
        ax2.set_ylabel('Learning Rate')
        ax2.set_title('LR Schedule')
        
        plt.tight_layout()
        plot_path = os.path.join(ckpt_dir, 'training_history.png')
        plt.savefig(plot_path, dpi=100)
        print(f"Training plot saved to {plot_path}")
    except Exception as e:
        print(f"Could not save training plot: {e}")
    
    return model, history


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Train SETI autoencoder')
    parser.add_argument('--target', default='PROXCEN')
    parser.add_argument('--crop-size', type=int, default=64)
    parser.add_argument('--latent-dim', type=int, default=32)
    parser.add_argument('--epochs', type=int, default=50)
    parser.add_argument('--batch-size', type=int, default=256)
    parser.add_argument('--lr', type=float, default=0.001)
    args = parser.parse_args()
    
    crops, labels, hit_ids = load_crops(args.target, args.crop_size)
    
    model, history = train_autoencoder(
        crops,
        crop_size=args.crop_size,
        latent_dim=args.latent_dim,
        epochs=args.epochs,
        batch_size=args.batch_size,
        lr=args.lr,
    )
