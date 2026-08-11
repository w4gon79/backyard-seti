# SETI ML Module

Phase 3 machine learning pipeline for signal classification and anomaly detection.

## Structure

```
ml/
├── README.md           # This file
├── pipeline.py         # End-to-end: extract -> train -> score
├── extract.py          # Training data extraction from HDF5 hits
├── train.py            # Model training (autoencoder, classifier)
├── infer.py            # Inference: score hits, write to DB
├── eval.py             # Evaluation metrics and plots
├── models/
│   ├── __init__.py
│   ├── autoencoder.py  # Unsupervised anomaly detection model
│   └── classifier.py   # Supervised RFI classifier (future)
├── data/               # Cached training tensors (.npy, .npz)
├── checkpoints/        # Saved model weights (.pt)
└── config.yaml         # Hyperparameters, paths, model config
```

## Design Principles

1. **Batch processing only.** No ML inference runs inside the Flask dashboard.
2. **One-directional DB integration.** ML pipeline writes `ml_class`, `ml_confidence`, `anomaly_score` to the `hits` table. Dashboard reads them.
3. **Offline training.** HDF5 file reads happen in background jobs with no timeout pressure.
4. **Cached tensors.** Waterfall crops are extracted once, cached as numpy arrays, reused across training epochs.

## Phases

### Phase 3A: Anomaly Detector (unsupervised)
- Autoencoder trained on all hit waterfalls
- Flags signals with highest reconstruction error
- Surfaces the most unusual signals for human review
- Zero labeled data needed

### Phase 3B: RFI Classifier (supervised, future)
- CNN trained on ON/OFF labels as noisy supervision
- Replaces heuristic classification with learned features
- Requires Phase 3A data pipeline as prerequisite

## Usage

```bash
# Extract training data from completed scans
python ml/extract.py --target PROXCEN --crop-size 64

# Train autoencoder
python ml/train.py --model autoencoder --epochs 50

# Score all hits in DB
python ml/infer.py --checkpoint checkpoints/autoencoder_best.pt

# Full pipeline
python ml/pipeline.py --target PROXCEN
```
