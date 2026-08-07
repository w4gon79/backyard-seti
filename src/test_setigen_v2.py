#!/usr/bin/env python3
"""
test_setigen_v2.py - Debug setigen signal injection step by step.
Figure out why add_signal + save_h5 loses the signal.
"""
import numpy as np
import os
import setigen
from setigen import Frame

input_path = "data/Parkes_57790_62144_PROXCEN_S_mid.h5"
os.makedirs("data/injected", exist_ok=True)

print("=== Step 1: Load Frame ===")
frame = Frame(waterfall=input_path)
data_before = np.array(frame.data)
print(f"  data shape: {frame.data.shape}")
print(f"  fmid: {frame.fmid}")
print(f"  max before: {np.max(data_before):.0f}")

print("\n=== Step 2: Inject signal using add_constant_signal ===")
# Use add_constant_signal which is simpler
# level = signal intensity in same units as data
# For SNR=25, level should be 25 * noise_std
noise_std = frame.noise_std
print(f"  noise_std: {noise_std:.0f}")
target_level = 25.0 * noise_std
print(f"  target level (25*sigma): {target_level:.0f}")

frame.add_constant_signal(
    f_start=frame.fmid,
    drift_rate=0.0,
    level=target_level,
    width=frame.df * 3,
    f_profile_type='gaussian',
)

data_after = np.array(frame.data)
print(f"  max after: {np.max(data_after):.0f}")
diff = data_after - data_before
nz = np.count_nonzero(diff)
mx = np.max(np.abs(diff))
print(f"  diff nonzero: {nz}, max: {mx:.0f}")

if nz == 0:
    print("  *** Signal NOT added to frame.data! ***")
    
    # Try add_signal directly
    print("\n=== Step 2b: Try add_signal directly ===")
    frame2 = Frame(waterfall=input_path)
    
    # Use simple level in t_profile
    signal = frame2.add_signal(
        path=setigen.constant_path(f_start=frame2.fmid, drift_rate=0.0),
        t_profile=setigen.constant_t_profile(level=target_level),
        f_profile=setigen.gaussian_f_profile(width=frame2.df * 3),
    )
    
    data_after2 = np.array(frame2.data)
    diff2 = data_after2 - data_before
    nz2 = np.count_nonzero(diff2)
    mx2 = np.max(np.abs(diff2))
    print(f"  diff nonzero: {nz2}, max: {mx2:.0f}")
    print(f"  signal returned shape: {np.array(signal).shape}")
    print(f"  signal max: {np.max(np.abs(np.array(signal))):.0f}")
else:
    print("  Signal IS in frame.data. Proceeding to save.")
    
    print("\n=== Step 3: Save ===")
    out_path = "data/injected/setigen_v2_s25.h5"
    frame.save_h5(out_path)
    print(f"  Saved to {out_path}")
    
    print("\n=== Step 4: Reload and verify ===")
    frame3 = Frame(waterfall=out_path)
    data_reloaded = np.array(frame3.data)
    diff3 = data_reloaded - data_before
    nz3 = np.count_nonzero(diff3)
    mx3 = np.max(np.abs(diff3))
    print(f"  diff nonzero: {nz3}, max: {mx3:.0f}")
    
    if nz3 > 0:
        print("  Signal survived save/reload!")
        
        print("\n=== Step 5: Run turbo_seti ===")
        import sys
        sys.path.insert(0, 'src')
        from bl_doppler_search import run_doppler_search
        dat_path = run_doppler_search(out_path, out_dir='results/injected',
                                       min_drift=-5, max_drift=5, snr=5)
        with open(dat_path) as f:
            lines = f.readlines()
        hits = [l for l in lines if not l.startswith('#') and l.strip()]
        print(f"\n  turbo_seti found {len(hits)} hits")
        for h in hits[:10]:
            print(f"    {h.strip()}")
    else:
        print("  Signal LOST on save/reload!")
        
        # Debug: check what _update_waterfall does
        print("\n=== Debug: waterfall data vs frame data ===")
        print(f"  frame.data max: {np.max(frame.data):.0f}")
        frame._update_waterfall(filename=out_path)
        wf_data = np.array(frame.waterfall.data)
        print(f"  waterfall.data shape: {wf_data.shape}")
        print(f"  waterfall.data max: {np.max(wf_data):.0f}")
        wf_diff = wf_data - data_before[np.newaxis, :]
        # Account for shape difference
        if wf_data.ndim == 3:
            wf_diff = wf_data[0] - data_before
        nz_wf = np.count_nonzero(wf_diff)
        print(f"  waterfall diff nonzero: {nz_wf}")
