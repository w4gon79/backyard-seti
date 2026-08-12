"""Binary CNN classifier for SETI signal classification.

Reuses the same conv encoder backbone from the autoencoder, but replaces
the decoder with a classification head (linear + sigmoid).

Output: probability of being RFI (1 = RFI, 0 = candidate).

Architecture:
  Input: (1, crop_size, crop_size) grayscale waterfall crop
  Encoder: 3 conv layers -> flatten -> linear -> latent_dim
  Head: linear -> sigmoid
"""

import torch
import torch.nn as nn


class WaterfallClassifier(nn.Module):
    def __init__(self, crop_size=64, latent_dim=32, base_channels=32, n_layers=3, dropout=0.3):
        super().__init__()
        self.crop_size = crop_size
        self.latent_dim = latent_dim

        # Build encoder conv layers (same as autoencoder)
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

        feat_size = crop_size // (2 ** n_layers)
        self.feat_size = feat_size
        self.flat_dim = in_ch * feat_size * feat_size

        # Latent projection
        self.encoder_fc = nn.Linear(self.flat_dim, latent_dim)

        # Classification head
        self.head = nn.Sequential(
            nn.Linear(latent_dim, 16),
            nn.LeakyReLU(0.2, inplace=True),
            nn.Dropout(dropout),
            nn.Linear(16, 1),
            # sigmoid applied in forward via BCEWithLogitsLoss
        )

    def encode(self, x):
        h = self.encoder_conv(x)
        h = h.view(h.size(0), -1)
        z = self.encoder_fc(h)
        return z

    def forward(self, x):
        z = self.encode(x)
        logits = self.head(z).squeeze(1)
        return logits

    def predict_proba(self, x, device='cpu'):
        """Return probability of RFI (class 1)."""
        self.eval()
        with torch.no_grad():
            logits = self.forward(x.to(device))
            return torch.sigmoid(logits)


def compute_classifier_score(model, batch, device='cpu'):
    """Compute RFI probability for each sample.
    
    Returns tensor of shape (batch_size,) with values in [0, 1].
    1 = confident RFI, 0 = confident candidate.
    """
    return model.predict_proba(batch, device)
