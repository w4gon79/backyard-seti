#!/usr/bin/env python
"""Test memory usage of waterfall loading strategies.

Goal: Determine whether blimpy Waterfall(f_start, f_stop) actually limits
memory, or if it loads the full 12 GB file. Test h5py direct slicing as
an alternative. Then test the full endpoint logic for all 4 epochs.
"""
import os
import sys
import time
import tracemalloc
import gc

sys.path.insert(0, r'G:\seti')
sys.path.insert(0, r'G:\seti\src')

import numpy as np
from incoherent_stack import EPOCHS, find_h5

# Pick a test frequency - use a known Parkes fine-res frequency
TEST_FREQ_MHZ = 326.5  # arbitrary, in the Parkes band
CHAN_WIDTH_MHZ = 2.7939677e-6
WIDTH_CHANS = 100  # reduced default
HALF_WIDTH_MHZ = (WIDTH_CHANS + 50) * CHAN_WIDTH_MHZ
F_START = TEST_FREQ_MHZ - HALF_WIDTH_MHZ
F_STOP = TEST_FREQ_MHZ + HALF_WIDTH_MHZ

def fmt_mb(n_bytes):
    return f"{n_bytes / 1024 / 1024:.1f} MB"

def test_blimpy_memory():
    """Test 1: Load one file with blimpy Waterfall + f_start/f_stop."""
    print("=" * 60)
    print("TEST 1: blimpy Waterfall with f_start/f_stop filter")
    print("=" * 60)
    
    on_file = find_h5('Parkes_57791_72989_PROXCEN_S_fine.h5')
    if not on_file:
        print("  SKIP: file not found")
        return None, None
    
    file_size_gb = os.path.getsize(on_file) / 1024**3
    print(f"  File size: {file_size_gb:.2f} GB")
    print(f"  Requested freq window: {F_START:.6f} - {F_STOP:.6f} MHz")
    print(f"  Requested channels: ~{2 * (WIDTH_CHANS + 50)}")
    
    gc.collect()
    tracemalloc.start()
    snapshot1 = tracemalloc.take_snapshot()
    
    t0 = time.time()
    from blimpy import Waterfall
    wf = Waterfall(on_file, load_data=True, f_start=F_START, f_stop=F_STOP)
    t1 = time.time()
    
    data = np.array(wf.data, dtype=np.float32)
    if data.ndim == 3:
        data = data[:, 0, :]
    
    snapshot2 = tracemalloc.take_snapshot()
    tracemalloc.stop()
    
    print(f"  Load time: {t1 - t0:.1f}s")
    print(f"  Data shape: {data.shape} (tints × chans)")
    print(f"  Data memory: {fmt_mb(data.nbytes)}")
    
    # Check blimpy's internal memory
    stats = snapshot2.compare_to(snapshot1, 'filename')
    total_alloc = sum(s.size_diff for s in stats if s.size_diff > 0)
    print(f"  Python allocations during load: {fmt_mb(total_alloc)}")
    
    # Get frequency axis
    try:
        freqs = np.array(wf.container.sf_freqs, dtype=np.float64)
        print(f"  Freq axis from container: {len(freqs)} points, range {freqs[0]:.6f} - {freqs[-1]:.6f}")
    except Exception as e:
        print(f"  Freq axis error: {e}")
    
    h = wf.header
    tsamp = float(h.get('tsamp', 18.25))
    print(f"  tsamp: {tsamp}")
    print(f"  n_chans in header: {h.get('nchans', '?')}")
    
    del data, wf
    gc.collect()
    
    return None, None


def test_h5py_direct():
    """Test 2: Use h5py directly to read only the needed channel range."""
    print("\n" + "=" * 60)
    print("TEST 2: h5py direct channel slicing")
    print("=" * 60)
    
    on_file = find_h5('Parkes_57791_72989_PROXCEN_S_fine.h5')
    if not on_file:
        print("  SKIP: file not found")
        return
    
    import h5py
    
    gc.collect()
    tracemalloc.start()
    snap1 = tracemalloc.take_snapshot()
    
    t0 = time.time()
    with h5py.File(on_file, 'r') as f:
        # Explore structure
        print(f"  HDF5 keys: {list(f.keys())}")
        
        # Get dataset info
        if 'data' in f:
            ds = f['data']
            print(f"  data dataset shape: {ds.shape}, dtype: {ds.dtype}")
            print(f"  data dataset chunks: {ds.chunks}")
            n_chans_total = ds.shape[-1]
            n_tints_total = ds.shape[0]
            
            # Read header info from attributes
            for key in ['fch1', 'foff', 'nchans', 'tsamp']:
                if key in f['data'].attrs:
                    print(f"  data.attrs['{key}'] = {f['data'].attrs[key]}")
            
            # Also check header group
            if 'header' in f:
                hdr = dict(f['header'].attrs)
                fch1 = float(hdr.get('fch1', 0))
                foff = float(hdr.get('foff', 0))
                nchans = int(hdr.get('nchans', n_chans_total))
                tsamp = float(hdr.get('tsamp', 18.25))
                print(f"  header: fch1={fch1}, foff={foff}, nchans={nchans}, tsamp={tsamp}")
            else:
                # Fallback: compute from data shape
                fch1 = 0
                foff = 0
                tsamp = 18.25
                nchans = n_chans_total
            
            # Compute channel indices for our freq window
            # Parkes fine-res: fch1 is the highest freq, foff is negative
            # freq[i] = fch1 + i * foff
            # Solve for i: i = (freq - fch1) / foff
            if foff != 0:
                i_start = int((F_STOP - fch1) / foff)  # higher freq = lower index when foff < 0
                i_stop = int((F_START - fch1) / foff)
                i_start = max(0, i_start)
                i_stop = min(n_chans_total, i_stop)
                if i_start > i_stop:
                    i_start, i_stop = i_stop, i_start
                print(f"  Channel slice: [{i_start}:{i_stop}] of {n_chans_total}")
                print(f"  Slicing {i_stop - i_start} channels out of {n_chans_total}")
                
                t1 = time.time()
                # Read only the needed channels - h5py with chunked storage should be efficient
                raw_data = ds[:, :, i_start:i_stop]
                t2 = time.time()
                
                data = np.array(raw_data, dtype=np.float32)
                if data.ndim == 3:
                    data = data[:, 0, :]
                
                snap2 = tracemalloc.take_snapshot()
                tracemalloc.stop()
                
                print(f"  h5py read time: {t2 - t1:.1f}s")
                print(f"  Data shape: {data.shape}")
                print(f"  Data memory: {fmt_mb(data.nbytes)}")
                
                stats = snap2.compare_to(snap1, 'filename')
                total_alloc = sum(s.size_diff for s in stats if s.size_diff > 0)
                print(f"  Python allocations: {fmt_mb(total_alloc)}")
                
                # Compute freq axis for the slice
                freqs = np.array([fch1 + (i_start + j) * foff for j in range(i_stop - i_start)])
                print(f"  Freq range: {freqs[0]:.6f} - {freqs[-1]:.6f} MHz")
                
                return data, freqs, tsamp
            
            # Also check for SIGPROC-style header in root attrs
            root_attrs = dict(f.attrs)
            if root_attrs:
                print(f"  Root attrs: {list(root_attrs.keys())[:10]}")
        else:
            print("  No 'data' dataset found!")
            print(f"  Available: {list(f.keys())}")
            for k in f.keys():
                if hasattr(f[k], 'shape'):
                    print(f"    {k}: shape={f[k].shape}")
    
    return None, None, None


def test_full_endpoint_logic():
    """Test 3: Full endpoint logic - all 4 epochs, sequential loading."""
    print("\n" + "=" * 60)
    print("TEST 3: Full endpoint logic (all 4 epochs, sequential)")
    print("=" * 60)
    
    from blimpy import Waterfall
    from barycentric_correct import compute_barycentric_velocity, extract_mjd_from_filename, resolve_target_coords
    
    target = 'PROXCEN'
    target_ra, target_dec, _src = resolve_target_coords(target)
    
    width_chans = 100  # reduced default
    max_tints = 10     # reduced default
    chan_width_mhz = CHAN_WIDTH_MHZ
    half_width_mhz = (width_chans + 50) * chan_width_mhz
    
    target_chans = 2 * width_chans
    f_min_bary = TEST_FREQ_MHZ - width_chans * chan_width_mhz
    f_max_bary = TEST_FREQ_MHZ + width_chans * chan_width_mhz
    common_grid = np.linspace(f_min_bary, f_max_bary, target_chans)
    
    c = 299792458.0
    epoch_spectra_2d = []
    epoch_labels = []
    
    for ep_label, ep_def in EPOCHS.items():
        mjd_int = ep_def['mjd_int']
        seqs = ep_def['seqs']
        
        first_on = f"Parkes_{mjd_int}_{seqs[0][0]}_PROXCEN_S_fine.h5"
        mjd = extract_mjd_from_filename(first_on)
        v_bary = compute_barycentric_velocity(mjd, target_ra, target_dec, 'parkes')
        corr = 1.0 - v_bary / c
        
        f_start_obs = TEST_FREQ_MHZ - half_width_mhz
        f_stop_obs = TEST_FREQ_MHZ + half_width_mhz
        
        on_seq, off_seq = seqs[0]
        on_file = f"Parkes_{mjd_int}_{on_seq}_PROXCEN_S_fine.h5"
        off_file = f"Parkes_{mjd_int}_{off_seq}_PROXCEN_R_fine.h5"
        on_path = find_h5(on_file)
        off_path = find_h5(off_file)
        
        if not on_path or not off_path:
            print(f"  {ep_label}: files not found, skipping")
            continue
        
        print(f"\n  Epoch {ep_label} (MJD {mjd_int})...")
        gc.collect()
        
        mem_before = tracemalloc.get_traced_memory()[0] if tracemalloc.is_tracing() else 0
        
        try:
            t0 = time.time()
            wf_on = Waterfall(on_path, load_data=True, f_start=f_start_obs, f_stop=f_stop_obs)
            on_data = np.array(wf_on.data, dtype=np.float32)
            if on_data.ndim == 3:
                on_data = on_data[:, 0, :]
            h = wf_on.header
            tsamp = float(h.get('tsamp', 18.25))
            n_chans_loaded = on_data.shape[1]
            if n_chans_loaded > 1:
                on_freqs = np.linspace(f_start_obs, f_stop_obs, n_chans_loaded)
            else:
                on_freqs = np.array([f_start_obs])
            del wf_on
            gc.collect()
            
            wf_off = Waterfall(off_path, load_data=True, f_start=f_start_obs, f_stop=f_stop_obs)
            off_data = np.array(wf_off.data, dtype=np.float32)
            if off_data.ndim == 3:
                off_data = off_data[:, 0, :]
            del wf_off
            gc.collect()
            
            t1 = time.time()
            print(f"    Load time: {t1 - t0:.1f}s")
            print(f"    ON shape: {on_data.shape}, OFF shape: {off_data.shape}")
            
            # Align time
            n_t = min(on_data.shape[0], off_data.shape[0])
            on_data = on_data[:n_t]
            off_data = off_data[:n_t]
            
            residual = on_data - off_data
            del on_data, off_data
            gc.collect()
            
            # Barycentric correction
            bary_freqs = on_freqs * corr
            sort_idx = np.argsort(bary_freqs)
            bary_sorted = bary_freqs[sort_idx]
            
            interp_2d = np.zeros((residual.shape[0], target_chans))
            for t_idx in range(residual.shape[0]):
                row_sorted = residual[t_idx, sort_idx]
                interp_2d[t_idx] = np.interp(common_grid, bary_sorted, row_sorted)
            
            del residual
            gc.collect()
            
            # Limit time integrations
            n_tints = interp_2d.shape[0]
            if n_tints > max_tints:
                indices = np.linspace(0, n_tints - 1, max_tints, dtype=int)
                interp_2d = interp_2d[indices]
            
            epoch_spectra_2d.append(interp_2d)
            epoch_labels.append(ep_label)
            print(f"    Result: {interp_2d.shape} = {fmt_mb(interp_2d.nbytes)}")
            
        except Exception as e:
            print(f"    ERROR: {e}")
            continue
    
    if not epoch_spectra_2d:
        print("\n  FAILED: No epoch data loaded")
        return False
    
    # Align time bins
    min_tints = min(s.shape[0] for s in epoch_spectra_2d)
    for i in range(len(epoch_spectra_2d)):
        if epoch_spectra_2d[i].shape[0] > min_tints:
            indices = np.linspace(0, epoch_spectra_2d[i].shape[0] - 1, min_tints, dtype=int)
            epoch_spectra_2d[i] = epoch_spectra_2d[i][indices]
    
    stacked = np.mean(epoch_spectra_2d, axis=0)
    
    total_mem = sum(s.nbytes for s in epoch_spectra_2d) + stacked.nbytes
    print(f"\n  Epochs loaded: {len(epoch_spectra_2d)}")
    print(f"  Total result memory: {fmt_mb(total_mem)}")
    print(f"  Stacked shape: {stacked.shape}")
    print(f"  min_tints: {min_tints}")
    
    print("\n  ✅ SUCCESS: All epochs loaded without OOM!")
    return True


def test_h5py_full_endpoint():
    """Test 4: Full endpoint logic using h5py instead of blimpy."""
    print("\n" + "=" * 60)
    print("TEST 4: Full endpoint logic with h5py (all 4 epochs)")
    print("=" * 60)
    
    import h5py
    from barycentric_correct import compute_barycentric_velocity, extract_mjd_from_filename, resolve_target_coords
    
    target = 'PROXCEN'
    target_ra, target_dec, _src = resolve_target_coords(target)
    
    width_chans = 100
    max_tints = 10
    chan_width_mhz = CHAN_WIDTH_MHZ
    half_width_mhz = (width_chans + 50) * chan_width_mhz
    
    target_chans = 2 * width_chans
    f_min_bary = TEST_FREQ_MHZ - width_chans * chan_width_mhz
    f_max_bary = TEST_FREQ_MHZ + width_chans * chan_width_mhz
    common_grid = np.linspace(f_min_bary, f_max_bary, target_chans)
    
    c = 299792458.0
    epoch_spectra_2d = []
    epoch_labels = []
    
    for ep_label, ep_def in EPOCHS.items():
        mjd_int = ep_def['mjd_int']
        seqs = ep_def['seqs']
        
        first_on = f"Parkes_{mjd_int}_{seqs[0][0]}_PROXCEN_S_fine.h5"
        mjd = extract_mjd_from_filename(first_on)
        v_bary = compute_barycentric_velocity(mjd, target_ra, target_dec, 'parkes')
        corr = 1.0 - v_bary / c
        
        on_seq, off_seq = seqs[0]
        on_file = f"Parkes_{mjd_int}_{on_seq}_PROXCEN_S_fine.h5"
        off_file = f"Parkes_{mjd_int}_{off_seq}_PROXCEN_R_fine.h5"
        on_path = find_h5(on_file)
        off_path = find_h5(off_file)
        
        if not on_path or not off_path:
            print(f"  {ep_label}: files not found, skipping")
            continue
        
        print(f"\n  Epoch {ep_label} (MJD {mjd_int})...")
        gc.collect()
        
        try:
            t0 = time.time()
            
            # Load ON data via h5py
            with h5py.File(on_path, 'r') as f:
                ds = f['data']
                n_chans_total = ds.shape[-1]
                
                # Get header
                hdr = dict(f['header'].attrs)
                fch1 = float(hdr.get('fch1', 0))
                foff = float(hdr.get('foff', 0))
                tsamp = float(hdr.get('tsamp', 18.25))
                
                f_start_obs = TEST_FREQ_MHZ - half_width_mhz
                f_stop_obs = TEST_FREQ_MHZ + half_width_mhz
                
                # Compute channel indices
                if foff < 0:
                    i_start = max(0, int((f_stop_obs - fch1) / foff))
                    i_stop = min(n_chans_total, int((f_start_obs - fch1) / foff) + 1)
                else:
                    i_start = max(0, int((f_start_obs - fch1) / foff))
                    i_stop = min(n_chans_total, int((f_stop_obs - fch1) / foff) + 1)
                
                if i_start > i_stop:
                    i_start, i_stop = i_stop, i_start
                
                on_data = np.array(ds[:, :, i_start:i_stop], dtype=np.float32)
                if on_data.ndim == 3:
                    on_data = on_data[:, 0, :]
                on_freqs = np.array([fch1 + (i_start + j) * foff for j in range(i_stop - i_start)])
            
            gc.collect()
            
            # Load OFF data via h5py
            with h5py.File(off_path, 'r') as f:
                ds = f['data']
                off_data = np.array(ds[:, :, i_start:i_stop], dtype=np.float32)
                if off_data.ndim == 3:
                    off_data = off_data[:, 0, :]
            
            gc.collect()
            
            t1 = time.time()
            print(f"    Load time: {t1 - t0:.1f}s")
            print(f"    ON shape: {on_data.shape}, OFF shape: {off_data.shape}")
            print(f"    ON mem: {fmt_mb(on_data.nbytes)}, OFF mem: {fmt_mb(off_data.nbytes)}")
            
            # Align time
            n_t = min(on_data.shape[0], off_data.shape[0])
            on_data = on_data[:n_t]
            off_data = off_data[:n_t]
            
            residual = on_data - off_data
            del on_data, off_data
            gc.collect()
            
            # Barycentric correction
            bary_freqs = on_freqs * corr
            sort_idx = np.argsort(bary_freqs)
            bary_sorted = bary_freqs[sort_idx]
            
            interp_2d = np.zeros((residual.shape[0], target_chans))
            for t_idx in range(residual.shape[0]):
                row_sorted = residual[t_idx, sort_idx]
                interp_2d[t_idx] = np.interp(common_grid, bary_sorted, row_sorted)
            
            del residual
            gc.collect()
            
            # Limit time integrations
            n_tints = interp_2d.shape[0]
            if n_tints > max_tints:
                indices = np.linspace(0, n_tints - 1, max_tints, dtype=int)
                interp_2d = interp_2d[indices]
            
            epoch_spectra_2d.append(interp_2d)
            epoch_labels.append(ep_label)
            print(f"    Result: {interp_2d.shape} = {fmt_mb(interp_2d.nbytes)}")
            
        except Exception as e:
            print(f"    ERROR: {e}")
            import traceback
            traceback.print_exc()
            continue
    
    if not epoch_spectra_2d:
        print("\n  FAILED: No epoch data loaded")
        return False
    
    min_tints = min(s.shape[0] for s in epoch_spectra_2d)
    for i in range(len(epoch_spectra_2d)):
        if epoch_spectra_2d[i].shape[0] > min_tints:
            indices = np.linspace(0, epoch_spectra_2d[i].shape[0] - 1, min_tints, dtype=int)
            epoch_spectra_2d[i] = epoch_spectra_2d[i][indices]
    
    stacked = np.mean(epoch_spectra_2d, axis=0)
    total_mem = sum(s.nbytes for s in epoch_spectra_2d) + stacked.nbytes
    print(f"\n  Epochs loaded: {len(epoch_spectra_2d)}")
    print(f"  Total result memory: {fmt_mb(total_mem)}")
    print(f"  Stacked shape: {stacked.shape}")
    
    print("\n  ✅ SUCCESS: h5py approach - All epochs loaded without OOM!")
    return True


if __name__ == '__main__':
    print("Waterfall Memory Test")
    print(f"Python: {sys.executable}")
    print(f"Test freq: {TEST_FREQ_MHZ} MHz, width: {WIDTH_CHANS} chans")
    
    # Test 1: blimpy memory check
    test_blimpy_memory()
    
    # Test 2: h5py direct slicing
    test_h5py_direct()
    
    # Test 3: Full endpoint logic with blimpy
    print("\n" + "#" * 60)
    print("# Testing full endpoint logic with blimpy (might OOM)")
    print("#" * 60)
    tracemalloc.start()
    result_blimpy = test_full_endpoint_logic()
    tracemalloc.stop()
    
    # Test 4: Full endpoint logic with h5py
    print("\n" + "#" * 60)
    print("# Testing full endpoint logic with h5py")
    print("#" * 60)
    tracemalloc.start()
    result_h5py = test_h5py_full_endpoint()
    tracemalloc.stop()
    
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"  blimpy full endpoint: {'PASS' if result_blimpy else 'FAIL'}")
    print(f"  h5py full endpoint:   {'PASS' if result_h5py else 'FAIL'}")
