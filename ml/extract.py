"""Training data extraction: waterfall crops from HDF5 hit frequencies.

Extracts small (crop_size x crop_size) waterfall patches centered on each
hit frequency in the database. Caches as .npz files for training.

This is a batch job. It reads multi-GB HDF5 files using blimpy's Waterfall
with frequency filtering. Run it offline, not from the Flask dashboard.
"""

import os
import sys
import time
import argparse
import numpy as np
import sqlite3
import json

# SETI root
SETI_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SETI_ROOT)
sys.path.insert(0, os.path.join(SETI_ROOT, 'src'))


def extract_crops_from_file(h5_path, hit_freqs, crop_size=64, max_tints=None):
    """Extract waterfall crops from an HDF5 file at given hit frequencies.

    Opens the HDF5 file ONCE and batch-reads all channel slices in a single
    pass to avoid 5000+ separate file open/close cycles.

    Returns array of shape (n_hits, crop_size, crop_size).
    """
    import sys, os
    SETI_ROOT_LOCAL = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    sys.path.insert(0, os.path.join(SETI_ROOT_LOCAL, 'src'))
    import hdf5plugin  # noqa: F401 -- registers bitshuffle filter
    import h5py

    half = crop_size // 2
    crops = []
    processed = 0
    errors = 0

    # Read header first (needed for channel computation)
    with h5py.File(h5_path, 'r') as f:
        dset = f['data']
        attrs = dict(dset.attrs)
        fch1 = float(attrs.get('fch1', 0))
        foff = float(attrs.get('foff', 0))
        nchans = int(attrs.get('nchans', 207618048))
        n_times = dset.shape[0]

        # Sort hits by channel number for sequential disk access
        hit_channels = []
        for hit_freq in hit_freqs:
            center_chan = int(round((hit_freq - fch1) / foff))
            if center_chan - half >= 0 and center_chan + half <= nchans:
                hit_channels.append((center_chan, hit_freq))
        hit_channels.sort(key=lambda x: x[0])

        if not hit_channels:
            return None

        for center_chan, hit_freq in hit_channels:
            chan_start = center_chan - half
            chan_stop = center_chan + half

            try:
                data = np.array(dset[:, 0, chan_start:chan_stop], dtype=np.float32)
            except Exception:
                errors += 1
                continue

            n_tints = data.shape[0]

            if n_tints > crop_size:
                indices = np.linspace(0, n_tints - 1, crop_size, dtype=int)
                data = data[indices]
            elif n_tints < crop_size:
                pad = np.tile(data[-1:], (crop_size - n_tints, 1))
                data = np.vstack([data, pad])

            c_min, c_max = data.min(), data.max()
            if c_max > c_min:
                data = (data - c_min) / (c_max - c_min)
            else:
                data = np.zeros_like(data)

            crops.append(data)
            processed += 1
            if processed % 500 == 0:
                print(f"      {processed}/{len(hit_channels)} crops...", flush=True)

    if errors > 0:
        print(f"      ({errors} channels skipped due to read errors)")

    if not crops:
        return None

    return np.array(crops, dtype=np.float32)


def find_h5_for_hit(hit_row, fine_dirs):
    """Find the HDF5 file that contains a given hit."""
    source_file = hit_row['source_file']
    for d in fine_dirs:
        path = os.path.join(d, source_file)
        if os.path.isfile(path):
            return path
    return None


def extract_all_crops(target='PROXCEN', crop_size=64, max_per_file=5000, db_path=None):
    """Extract crops for all hits of a given target.

    Groups hits by source file to minimize HDF5 reads.
    Returns (crops, hit_ids) arrays.
    """
    if db_path is None:
        db_path = os.path.join(SETI_ROOT, 'data', 'seti_hits.db')

    FINE_DIRS = [
        r'D:\seti_data\fine',
        r'G:\seti\data\fine',
    ]

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    # Get all hits with source files
    c.execute("""
        SELECT h.id, h.source_file, h.freq, h.barycentric_freq, h.on_off, h.snr,
               s.target
        FROM hits h
        JOIN scans s ON h.scan_id = s.scan_id
        WHERE s.target = ?
        ORDER BY h.source_file
    """, (target,))
    hits = c.fetchall()
    conn.close()

    if not hits:
        print(f"No hits found for target {target}")
        return None, None

    print(f"Found {len(hits)} hits for {target}")

    # Group by source file
    file_groups = {}
    for hit in hits:
        sf = hit['source_file']
        if sf not in file_groups:
            file_groups[sf] = []
        file_groups[sf].append(hit)

    print(f"Across {len(file_groups)} source files")

    all_crops = []
    all_hit_ids = []
    all_labels = []  # 0 = ON only (candidate), 1 = also in OFF (RFI)
    total_processed = 0

    for file_idx, (source_file, file_hits) in enumerate(file_groups.items()):
        h5_path = find_h5_for_hit(file_hits[0], FINE_DIRS)
        if not h5_path:
            print(f"  [{file_idx+1}/{len(file_groups)}] SKIP: {source_file} not found")
            continue

        # Subsample if too many hits in one file
        if len(file_hits) > max_per_file:
            import random
            file_hits = random.sample(file_hits, max_per_file)

        # Get frequencies (use raw freq for file lookup, not barycentric)
        freqs = [h['freq'] for h in file_hits]

        print(f"  [{file_idx+1}/{len(file_groups)}] {source_file}: {len(freqs)} hits", end='', flush=True)
        t0 = time.time()

        crops = extract_crops_from_file(h5_path, freqs, crop_size=crop_size)
        if crops is None:
            print(f" - FAILED ({time.time()-t0:.1f}s)")
            continue

        print(f" - {crops.shape[0]} crops ({time.time()-t0:.1f}s)")

        # Determine ON/OFF label for each hit
        # Build a set of OFF frequencies in this file for quick lookup
        off_freqs = set()
        for h in file_hits:
            if h['on_off'] == 'OFF':
                off_freqs.add(round(h['freq'], 6))

        for i, hit in enumerate(file_hits):
            if i >= len(crops):
                break
            # Label as RFI if this frequency also appears in OFF data
            is_rfi = round(hit['freq'], 6) in off_freqs
            all_crops.append(crops[i])
            all_hit_ids.append(hit['id'])
            all_labels.append(1 if is_rfi else 0)

        total_processed += len(crops)

    if not all_crops:
        print("No crops extracted!")
        return None, None

    crops_array = np.array(all_crops, dtype=np.float32)
    hit_ids_array = np.array(all_hit_ids, dtype=np.int64)
    labels_array = np.array(all_labels, dtype=np.int32)

    print(f"\nTotal: {len(all_crops)} crops extracted")

    # Save to cache
    cache_dir = os.path.join(SETI_ROOT, 'ml', 'data')
    os.makedirs(cache_dir, exist_ok=True)
    cache_path = os.path.join(cache_dir, f'crops_{target}_{crop_size}.npz')
    np.savez_compressed(
        cache_path,
        crops=crops_array,
        hit_ids=hit_ids_array,
        labels=labels_array,
    )
    print(f"Saved to {cache_path} ({os.path.getsize(cache_path)/1e6:.1f} MB)")

    return crops_array, hit_ids_array


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Extract training data from HDF5 hits')
    parser.add_argument('--target', default='PROXCEN', help='Target name')
    parser.add_argument('--crop-size', type=int, default=64, help='Crop size (channels)')
    parser.add_argument('--max-per-file', type=int, default=5000, help='Max hits per HDF5 file')
    args = parser.parse_args()

    extract_all_crops(
        target=args.target,
        crop_size=args.crop_size,
        max_per_file=args.max_per_file,
    )
