#!/usr/bin/env python3
"""Quick test: load one PROXCEN file and run FindDoppler to see where it crashes."""
import sys, os, traceback
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

filepath = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "Parkes_57790_62144_PROXCEN_S_mid.h5")
results_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "results")

print(f"Testing: {filepath}")
print(f"Exists: {os.path.exists(filepath)}")

try:
    from bl_doppler_search import run_doppler_search
    dat_path = run_doppler_search(filepath, out_dir=results_dir, min_drift=-5, max_drift=5, snr=25)
    print(f"Success! Output: {dat_path}")
except Exception as e:
    print(f"FAILED:")
    traceback.print_exc()
