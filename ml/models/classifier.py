"""Binary CNN classifier for SETI signal classification - v2.

Reuses the same conv encoder backbone, adds metadata features
(drift_rate, snr, freq) concatenated to the latent vector before
the classification head.

Output: probability of being RFI (1 = RFI, 0 = candidate).

Architecture:
  Input: (1, crop_size, crop_size) grayscale waterfall crop
         + (3,) metadata vector [drift_rate, log_snr, freq] (normalized)
  Encoder: conv layers -> flatten -> linear -> latent_dim
  Head: cat(latent, metadata) -> linear -> sigmoid
"""

import torch
import torch.nn as nn


class WaterfallClassifier(nn.Module):
    def __init__(self, crop_size=128, latent_dim=64, base_channels=32,
                 n_layers=3, dropout=0.4, metadata_dim=3):
        super().__init__()
        self.crop_size = crop_size
        self.latent_dim = latent_dim
        self.metadata_dim = metadata_dim

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

        feat_size = crop_size // (2 ** n_layers)
        self.feat_size = feat_size
        self.flat_dim = in_ch * feat_size * feat_size

        # Latent projection
        self.encoder_fc = nn.Linear(self.flat_dim, latent_dim)

        # Classification head: latent + metadata -> prediction
        head_input = latent_dim + metadata_dim
        self.head = nn.Sequential(
            nn.Linear(head_input, 32),
            nn.LeakyReLU(0.2, inplace=True),
            nn.Dropout(dropout),
            nn.Linear(32, 16),
            nn.LeakyReLU(0.2, inplace=True),
            nn.Dropout(dropout * 0.5),
            nn.Linear(16, 1),
        )

    def encode(self, x):
        h = self.encoder_conv(x)
        h = h.view(h.size(0), -1)
        z = self.encoder_fc(h)
        return z

    def forward(self, x, metadata=None):
        z = self.encode(x)
        if metadata is not None:
            z = torch.cat([z, metadata], dim=1)
        logits = self.head(z).squeeze(1)
        return logits

    def predict_proba(self, x, metadata=None, device='cpu'):
        """Return probability of RFI (class 1)."""
        self.eval()
        with torch.no_grad():
            x = x.to(device)
            if metadata is not None:
                metadata = metadata.to(device)
            logits = self.forward(x, metadata)
            return torch.sigmoid(logits)


def compute_classifier_score(model, batch_x, batch_meta, device='cpu'):
    """Compute RFI probability for each sample."""
    return model.predict_proba(batch_x, batch_meta, device)
