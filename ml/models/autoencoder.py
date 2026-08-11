"""Autoencoder for SETI anomaly detection.

Convolutional autoencoder that learns to reconstruct waterfall crops.
High reconstruction error = anomalous signal = interesting.

Architecture:
  Input: (1, crop_size, crop_size) grayscale waterfall crop
  Encoder: 3 conv layers -> flatten -> linear -> latent_dim
  Decoder: linear -> unflatten -> 3 conv transpose layers -> output
"""

import torch
import torch.nn as nn


class WaterfallAutoencoder(nn.Module):
    def __init__(self, crop_size=64, latent_dim=32, base_channels=32, n_layers=3):
        super().__init__()
        self.crop_size = crop_size
        self.latent_dim = latent_dim
        self.base_channels = base_channels

        # Build encoder conv layers
        enc_layers = []
        in_ch = 1
        out_ch = base_channels
        for i in range(n_layers):
            enc_layers.append(nn.Conv2d(in_ch, out_ch, kernel_size=3, stride=2, padding=1))
            enc_layers.append(nn.BatchNorm2d(out_ch))
            enc_layers.append(nn.LeakyReLU(0.2, inplace=True))
            in_ch = out_ch
            out_ch *= 2
        self.encoder_conv = nn.Sequential(*enc_layers)

        # Calculate flattened size after conv layers
        feat_size = crop_size // (2 ** n_layers)
        self.feat_size = feat_size
        self.flat_dim = in_ch * feat_size * feat_size

        # Latent projection
        self.encoder_fc = nn.Linear(self.flat_dim, latent_dim)
        self.decoder_fc = nn.Linear(latent_dim, self.flat_dim)

        # Build decoder conv transpose layers (mirror encoder)
        dec_layers = []
        in_ch = base_channels * (2 ** (n_layers - 1))
        out_ch = in_ch // 2
        for i in range(n_layers - 1):
            dec_layers.append(nn.ConvTranspose2d(in_ch, out_ch, kernel_size=3, stride=2, padding=1, output_padding=1))
            dec_layers.append(nn.BatchNorm2d(out_ch))
            dec_layers.append(nn.LeakyReLU(0.2, inplace=True))
            in_ch = out_ch
            out_ch //= 2
        # Final layer: back to 1 channel, crop_size x crop_size
        dec_layers.append(nn.ConvTranspose2d(in_ch, 1, kernel_size=3, stride=2, padding=1, output_padding=1))
        dec_layers.append(nn.Sigmoid())  # Output in [0, 1] (normalized crops)
        self.decoder_conv = nn.Sequential(*dec_layers)

    def encode(self, x):
        h = self.encoder_conv(x)
        h = h.view(h.size(0), -1)
        z = self.encoder_fc(h)
        return z

    def decode(self, z):
        h = self.decoder_fc(z)
        h = h.view(h.size(0), -1, self.feat_size, self.feat_size)
        out = self.decoder_conv(h)
        return out

    def forward(self, x):
        z = self.encode(x)
        out = self.decode(z)
        return out, z

    def reconstruct(self, x):
        """Return only the reconstruction (for inference)."""
        out, _ = self.forward(x)
        return out


def compute_anomaly_score(model, batch, device='cpu'):
    """Compute per-sample reconstruction error (MSE).

    Returns tensor of shape (batch_size,) with anomaly scores.
    Higher = more anomalous.
    """
    model.eval()
    with torch.no_grad():
        recon = model.reconstruct(batch.to(device))
        # MSE per sample (averaged over channels, height, width)
        scores = torch.mean((recon - batch.to(device)) ** 2, dim=(1, 2, 3))
    return scores
