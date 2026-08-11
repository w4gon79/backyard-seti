"""Test hdf5_reader on all target frequencies."""
import sys
import time
import traceback
sys.path.insert(0, r'G:\seti')

import numpy as np
from src.hdf5_reader import read_channel_slice, freq_to_chan, chan_to_freq, get_header

H5_PATH = r'D:\seti_data\fine\Parkes_57791_72989_PROXCEN_S_fine.h5'

def main():
    print("")
    print("=" * 60)
    print("HDF5 Reader Test Suite")
    print(f"File: {H5_PATH}")
    print("=" * 60)
    print("")

    all_passed = True

    # Test 1: Header
    print("[1] Header reading")
    try:
        header = get_header(H5_PATH)
        print(f"    source_name: {header.get('source_name')}")
        print(f"    fch1: {header.get('fch1')}")
        print(f"    foff: {header.get('foff')}")
        print(f"    nchans: {header.get('nchans')}")
        print(f"    tsamp: {header.get('tsamp')}")
        assert header['source_name'] == 'ProxCen_S'
        assert header['nchans'] == 207618048
        print("    PASS")
        print("")
    except Exception as e:
        print(f"    FAIL: {e}")
        traceback.print_exc()
        all_passed = False
        print("")

    # Test 2: Channel reads at 4 positions
    targets = [
        ("Early file (~2746 MHz)", 1_000_000),
        ("Mid file (~3000 MHz)", 91_674_873),
        ("Late file (~3023 MHz)", 100_000_000),
        ("Very late (~3303 MHz)", 200_000_000),
    ]

    for i, (label, chan) in enumerate(targets):
        print(f"[{i+2}] {label} (chan={chan:,}, half_width=100)")
        t0 = time.time()
        try:
            data = read_channel_slice(H5_PATH, center_chan=chan, half_width=100)
            elapsed = time.time() - t0
            print(f"    Shape: {data.shape}")
            print(f"    Dtype: {data.dtype}")
            print(f"    Memory: {data.nbytes / 1024:.1f} KB")
            print(f"    Time: {elapsed:.3f}s")
            print(f"    Min/Max/Mean: {data.min():.4e} / {data.max():.4e} / {data.mean():.4e}")
            print(f"    First 5: {data[0, :5]}")
            assert data.ndim == 2, f"Expected 2D, got {data.ndim}D"
            assert data.shape[0] == 20, f"Expected 20 time steps, got {data.shape[0]}"
            assert data.shape[1] == 200, f"Expected 200 channels, got {data.shape[1]}"
            assert np.all(np.isfinite(data)), "Non-finite values!"
            print("    PASS")
            print("")
        except Exception as e:
            print(f"    FAIL: {type(e).__name__}: {e}")
            traceback.print_exc()
            all_passed = False
            print("")

    # Test 6: Frequency conversion
    print("[6] Frequency <-> Channel conversion")
    try:
        chan = freq_to_chan(3000.093667, h5_path=H5_PATH)
        freq_back = chan_to_freq(chan, h5_path=H5_PATH)
        print(f"    freq_to_chan(3000.093667) = {chan:,}")
        print(f"    chan_to_freq({chan:,}) = {freq_back:.6f}")
        assert abs(freq_back - 3000.093667) < 0.01, "Roundtrip failed"
        print("    PASS")
        print("")
    except Exception as e:
        print(f"    FAIL: {e}")
        all_passed = False
        print("")

    # Test 7: Edge cases
    print("[7] Edge cases (chan 0, last chan, narrow slice)")
    try:
        # Channel 0 (clamped - only 100 channels from 0 to 100)
        data = read_channel_slice(H5_PATH, center_chan=0, half_width=100)
        assert data.shape == (20, 100), f"Expected (20, 100) for clamped chan 0, got {data.shape}"
        print(f"    Channel 0 (clamped): shape={data.shape} OK")

        # Last channel (clamped - 100 channels)
        data = read_channel_slice(H5_PATH, center_chan=207618047, half_width=100)
        assert data.shape[0] == 20
        assert data.shape[1] <= 200  # may be clamped
        print(f"    Last channel (clamped): shape={data.shape} OK")

        # Narrow slice
        data = read_channel_slice(H5_PATH, center_chan=91674873, half_width=10)
        assert data.shape == (20, 20)
        print(f"    Narrow (hw=10): shape={data.shape} OK")

        print("    PASS")
        print("")
    except Exception as e:
        print(f"    FAIL: {e}")
        all_passed = False
        print("")

    # Summary
    print("=" * 60)
    if all_passed:
        print("ALL TESTS PASSED")
    else:
        print("SOME TESTS FAILED")
    print("=" * 60)
    return 0 if all_passed else 1


if __name__ == '__main__':
    sys.exit(main())
