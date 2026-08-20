"""Metadata-only baseline: can logistic regression separate RFI from candidates?"""
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import cross_val_score, cross_val_predict
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import classification_report, confusion_matrix
from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier

data = np.load(r'G:\seti\ml\data\classifier_PROXCEN_128_snr8.0.npz', allow_pickle=True)
X = data['metadata_raw']  # drift_rate, snr, freq (raw)
y = data['labels']

print(f"Dataset: {len(y)} samples ({np.sum(y==0)} candidates, {np.sum(y==1)} RFI)")
print(f"Features: drift_rate, snr, freq\n")

scaler = StandardScaler()
X_scaled = scaler.fit_transform(X)

# Logistic Regression
print("=== Logistic Regression (5-fold CV) ===")
lr = LogisticRegression(max_iter=1000)
acc = cross_val_score(lr, X_scaled, y, cv=5, scoring='accuracy')
f1 = cross_val_score(lr, X_scaled, y, cv=5, scoring='f1')
auc = cross_val_score(lr, X_scaled, y, cv=5, scoring='roc_auc')
print(f"  Accuracy: {acc.mean():.3f} +/- {acc.std():.3f}")
print(f"  F1:       {f1.mean():.3f} +/- {f1.std():.3f}")
print(f"  ROC AUC:  {auc.mean():.3f} +/- {auc.std():.3f}")

lr.fit(X_scaled, y)
for name, coef in zip(['drift_rate', 'snr', 'freq'], lr.coef_[0]):
    print(f"  {name}: weight={coef:.4f}")

# Random Forest (captures non-linear relationships)
print("\n=== Random Forest (5-fold CV) ===")
rf = RandomForestClassifier(n_estimators=200, max_depth=5, random_state=42)
acc = cross_val_score(rf, X_scaled, y, cv=5, scoring='accuracy')
f1 = cross_val_score(rf, X_scaled, y, cv=5, scoring='f1')
auc = cross_val_score(rf, X_scaled, y, cv=5, scoring='roc_auc')
print(f"  Accuracy: {acc.mean():.3f} +/- {acc.std():.3f}")
print(f"  F1:       {f1.mean():.3f} +/- {f1.std():.3f}")
print(f"  ROC AUC:  {auc.mean():.3f} +/- {auc.std():.3f}")

rf.fit(X_scaled, y)
for name, imp in zip(['drift_rate', 'snr', 'freq'], rf.feature_importances_):
    print(f"  {name}: importance={imp:.4f}")

# Gradient Boosting
print("\n=== Gradient Boosting (5-fold CV) ===")
gb = GradientBoostingClassifier(n_estimators=200, max_depth=3, random_state=42)
acc = cross_val_score(gb, X_scaled, y, cv=5, scoring='accuracy')
f1 = cross_val_score(gb, X_scaled, y, cv=5, scoring='f1')
auc = cross_val_score(gb, X_scaled, y, cv=5, scoring='roc_auc')
print(f"  Accuracy: {acc.mean():.3f} +/- {acc.std():.3f}")
print(f"  F1:       {f1.mean():.3f} +/- {f1.std():.3f}")
print(f"  ROC AUC:  {auc.mean():.3f} +/- {auc.std():.3f}")

# Feature distributions by class
print("\n=== Feature Distributions by Class ===")
for i, name in enumerate(['drift_rate', 'snr', 'freq']):
    cand = X[y == 0][:, i]
    rfi = X[y == 1][:, i]
    print(f"\n  {name}:")
    print(f"    Candidate: mean={cand.mean():.4f}, std={cand.std():.4f}, median={np.median(cand):.4f}")
    print(f"    RFI:       mean={rfi.mean():.4f}, std={rfi.std():.4f}, median={np.median(rfi):.4f}")
    # Effect size (Cohen's d)
    pooled_std = np.sqrt((cand.std()**2 + rfi.std()**2) / 2)
    if pooled_std > 0:
        d = (cand.mean() - rfi.mean()) / pooled_std
        print(f"    Cohen's d: {d:.4f} ({'negligible' if abs(d)<0.2 else 'small' if abs(d)<0.5 else 'medium' if abs(d)<0.8 else 'large'})")

print(f"\nBaseline (always majority class): {max(np.sum(y==0), np.sum(y==1))/len(y):.3f}")
