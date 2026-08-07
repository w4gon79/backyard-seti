#!/usr/bin/env python3
"""
run_50mhz_validation.py - Run fine-res pipeline on a 50 MHz window across
all 6 PROXCEN cadence files to validate end-to-end on real data.

Uses 262,144-channel sub-bands for speed. 50 MHz / 2.79 Hz = ~18M channels.
That's ~68 sub-bands per file. At ~3.5s each, ~4 min per file, ~24 min total.

Output: JSON hit files per observation + combined summary with cadence info.
"""
import os
import sys
import json
import time
import numpy as np
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'src'))

DATA_DIR = "data/fine"
OUT_DIR = "results/validation_50mhz"

# 50 MHz window: 2800-2850 MHz (arbitrary, just a chunk of L-band)
WINDOW_F_START = 2800.0  # MHz
WINDOW_F_STOP = 2850.0   # MHz

# Sub-band parameters
SUB_BAND_CHANS = 262144
OVERLAP_CHANS = 512

os.makedirs(OUT_DIR, exist_ok=True)


def main():
    import fine_res_pipeline as frp

    # Find all fine-res files
    files = sorted([
        os.path.join(DATA_DIR, f)
        for f in os.listdir(DATA_DIR)
        if f.endswith('.h5')
    ])

    print(f"50 MHz Validation Run")
    print(f"  Window: {WINDOW_F_START} - {WINDOW_F_STOP} MHz")
    print(f"  Files: {len(files)}")
    print(f"  Sub-band: {SUB_BAND_CHANS} chans ({SUB_BAND_CHANS * 2.7939677e-6 * 1000:.1f} kHz each)")
    print(f"  Overlap: {OVERLAP_CHANS} chans")
    print()

    # Compute sub-bands within the 50 MHz window
    # Fine-res: foff = +2.7939677e-6 MHz (ascending), fch1 = 2743.957 MHz
    fch1 = 2743.95703125
    foff = 2.7939677238464355e-6
    nchans_total = 207618048

    # Find channel range for our window
    start_chan = max(0, int((WINDOW_F_START - fch1) / foff))
    stop_chan = min(nchans_total, int((WINDOW_F_STOP - fch1) / foff))
    window_nchans = stop_chan - start_chan

    # Generate sub-bands within the window
    sub_bands = []
    pos = start_chan
    while pos < stop_chan:
        end = min(pos + SUB_BAND_CHANS, stop_chan)
        f_start = fch1 + foff * pos
        f_stop = fch1 + foff * end
        sub_bands.append((f_start, f_stop, pos, end))
        pos += SUB_BAND_CHANS - OVERLAP_CHANS

    print(f"  Sub-bands in window: {len(sub_bands)}")
    print(f"  Window channels: {window_nchans:,} ({window_nchans * foff:.1f} MHz)")
    print()

    all_results = {}

    for filepath in files:
        filename = os.path.basename(filepath)
        stem = os.path.splitext(filename)[0]

        # Determine if ON (S) or OFF (R)
        is_on = '_S_' in filename

        print(f"{'=' * 70}")
        print(f"Processing: {filename} ({'ON' if is_on else 'OFF'})")
        print(f"{'=' * 70}")

        subdir = os.path.join(OUT_DIR, stem)
        subband_dir = os.path.join(subdir, 'subbands')
        ts_dir = os.path.join(subdir, 'turbo_seti')
        os.makedirs(subband_dir, exist_ok=True)
        os.makedirs(ts_dir, exist_ok=True)

        all_hits = []
        t0 = time.time()

        for i, (f_start, f_stop, ch_start, ch_stop) in enumerate(sub_bands):
            sub_name = f"{stem}_sub{i:04d}.h5"
            sub_path = os.path.join(subband_dir, sub_name)

            bw = (f_stop - f_start) * 1000  # kHz
            elapsed = time.time() - t0
            if i > 0:
                rate = elapsed / i
                eta = rate * (len(sub_bands) - i)
                eta_str = f" ETA {eta:.0f}s"
            else:
                eta_str = ""
            print(f"  [{i+1}/{len(sub_bands)}] {f_start:.4f}-{f_stop:.4f} MHz ({bw:.0f} kHz){eta_str}",
                  end='', flush=True)

            try:
                nch, _, _ = frp.extract_sub_band(filepath, f_start, f_stop, sub_path)
                hits = frp.run_turbo_seti(sub_path, ts_dir, max_drift=5.0, snr=5.0)

                for h in hits:
                    h['sub_band'] = i
                    h['on_off'] = 'ON' if is_on else 'OFF'
                    h['file'] = stem
                    all_hits.append(h)

                print(f" -> {len(hits)} hits")
            except Exception as e:
                print(f" ERROR: {e}")

            # Cleanup sub-band file
            import gc
            gc.collect()
            try:
                os.remove(sub_path)
            except (PermissionError, OSError):
                pass

        elapsed = time.time() - t0
        all_hits.sort(key=lambda h: h['snr'], reverse=True)

        # Save per-file results
        results_path = os.path.join(subdir, f'{stem}_hits.json')
        with open(results_path, 'w') as f:
            json.dump({
                'file': filename,
                'on_off': 'ON' if is_on else 'OFF',
                'window_mhz': [WINDOW_F_START, WINDOW_F_STOP],
                'total_hits': len(all_hits),
                'sub_bands_processed': len(sub_bands),
                'processing_time_s': elapsed,
                'hits': all_hits,
            }, f, indent=2)

        print(f"\n  Total: {len(all_hits)} hits in {elapsed:.1f}s")
        if all_hits:
            print(f"  Top 5:")
            for h in all_hits[:5]:
                print(f"    SNR {h['snr']:.1f} | {h['freq']:.6f} MHz | "
                      f"drift {h['drift_rate']:.4f} Hz/s | ch {h.get('channel', -1)}")

        all_results[filename] = {
            'hits': len(all_hits),
            'on_off': 'ON' if is_on else 'OFF',
            'time_s': elapsed,
            'top_hits': all_hits[:5],
        }
        print()

    # Combined summary
    print(f"{'=' * 70}")
    print(f"VALIDATION RUN COMPLETE")
    print(f"{'=' * 70}")
    print(f"  Window: {WINDOW_F_START} - {WINDOW_F_STOP} MHz")
    print(f"  {'File':<55} {'Type':>4} {'Hits':>6} {'Time':>7}")
    print(f"  {'-'*55} {'-'*4} {'-'*6} {'-'*7}")

    on_hits = []
    off_hits = []

    for filename, info in all_results.items():
        short = filename[:55]
        print(f"  {short:<55} {info['on_off']:>4} {info['hits']:>6} {info['time_s']:>6.1f}s")

        # Collect frequencies for ON/OFF comparison
        freqs = set()
        for h in info['top_hits']:
            freqs.add(round(h['freq'], 6))
        if info['on_off'] == 'ON':
            on_hits.append((filename, freqs))
        else:
            off_hits.append((filename, freqs))

    total_on = sum(v['hits'] for v in all_results.values() if v['on_off'] == 'ON')
    total_off = sum(v['hits'] for v in all_results.values() if v['on_off'] == 'OFF')
    print(f"\n  ON total: {total_on} hits")
    print(f"  OFF total: {total_off} hits")

    # Save combined results
    summary_path = os.path.join(OUT_DIR, 'validation_summary.json')
    with open(summary_path, 'w') as f:
        json.dump({
            'window_mhz': [WINDOW_F_START, WINDOW_F_STOP],
            'sub_band_chans': SUB_BAND_CHANS,
            'overlap_chans': OVERLAP_CHANS,
            'files': all_results,
            'total_on_hits': total_on,
            'total_off_hits': total_off,
        }, f, indent=2, default=str)

    print(f"\n  Summary: {summary_path}")


if __name__ == '__main__':
    main()
