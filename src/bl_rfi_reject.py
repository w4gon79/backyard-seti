#!/usr/bin/env python3
"""
bl_rfi_reject.py - Compare ON/OFF cadence observations for RFI rejection.

Supports both single-pair (2 files) and full cadence (6 files: 3 ON + 3 OFF).

Usage:
    # Full 6-file cadence (recommended):
    python bl_rfi_reject.py --on file1_S.dat file2_S.dat file3_S.dat \\
                           --off file1_R.dat file2_R.dat file3_R.dat

    # Single pair:
    python bl_rfi_reject.py --on ON_file.dat --off OFF_file.dat

    # With plot:
    python bl_rfi_reject.py --on *.dat --off *.dat --plot --save-plot results/rfi.png
"""

import argparse
import os
import sys
from collections import defaultdict


def parse_dat(filepath):
    """Parse a turbo_seti .dat file and return list of hit dictionaries."""
    hits = []
    with open(filepath) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            parts = line.split('\t')
            if len(parts) < 6:
                continue
            try:
                hit = {
                    'drift_rate': float(parts[0]),
                    'snr': float(parts[1]),
                    'uncorrected_freq': float(parts[2]),
                    'corrected_freq': float(parts[3]),
                    'index': int(parts[4]),
                    'freq_start': float(parts[5]) if len(parts) > 5 else 0,
                    'freq_end': float(parts[6]) if len(parts) > 6 else 0,
                    'coarse_channel': int(parts[9]) if len(parts) > 9 else 0,
                    'total_hits': int(parts[10]) if len(parts) > 10 else 1,
                    'source_file': os.path.basename(filepath),
                }
                hits.append(hit)
            except (ValueError, IndexError):
                continue
    return hits


def freq_key(freq, tolerance_hz=500):
    """Quantize frequency to a bucket key for matching."""
    return round(freq * 1e6 / tolerance_hz)


def drift_key(drift, tolerance=0.1):
    """Quantize drift rate for matching."""
    return round(drift / tolerance)


def run_rfi_rejection(on_files, off_files, freq_tolerance_hz=500, drift_tolerance=0.1):
    """
    Full multi-observation RFI rejection.

    A true candidate must appear in ALL ON files but NO OFF files.
    RFI appears in both ON and OFF, or in only one observation (transient).

    Returns dict with categorized hits.
    """
    # Parse all files
    on_data = {}  # filename -> [hits]
    off_data = {}

    print("=== Parsing .dat files ===")
    for f in on_files:
        hits = parse_dat(f)
        on_data[f] = hits
        print(f"  ON  {os.path.basename(f)}: {len(hits)} hits")
    for f in off_files:
        hits = parse_dat(f)
        off_data[f] = hits
        print(f"  OFF {os.path.basename(f)}: {len(hits)} hits")

    total_on_hits = sum(len(h) for h in on_data.values())
    total_off_hits = sum(len(h) for h in off_data.values())
    print(f"\n  Total ON hits:  {total_on_hits}")
    print(f"  Total OFF hits: {total_off_hits}")

    if total_on_hits == 0 and total_off_hits == 0:
        return {
            'candidates': [],
            'rfi': [],
            'on_only_single': [],
            'off_only': [],
            'total_on': 0,
            'total_off': 0,
            'n_on_files': len(on_files),
            'n_off_files': len(off_files),
        }

    # Build OFF frequency index for quick lookup
    off_freq_index = defaultdict(list)  # (freq_key, drift_key) -> [hits]
    for f, hits in off_data.items():
        for h in hits:
            fk = freq_key(h['corrected_freq'], freq_tolerance_hz)
            dk = drift_key(h['drift_rate'], drift_tolerance)
            off_freq_index[(fk, dk)].append(h)
            # Also check neighboring freq buckets for tolerance edge cases
            for dfk in [-1, 1]:
                off_freq_index[(fk + dfk, dk)].append(h)

    # Categorize ON hits
    candidates = []           # In ALL ON files, NOT in any OFF file
    rfi_hits = []             # In any ON file AND any OFF file
    on_only_single = []       # In only ONE ON file, not in OFF (transient)

    # Track which ON files each frequency appears in
    on_freq_map = defaultdict(list)  # (freq_key, drift_key) -> [(filename, hit)]
    for f, hits in on_data.items():
        seen_in_file = set()  # Avoid double-counting within same file
        for h in hits:
            fk = freq_key(h['corrected_freq'], freq_tolerance_hz)
            dk = drift_key(h['drift_rate'], drift_tolerance)
            key = (fk, dk)
            if key not in seen_in_file:
                on_freq_map[key].append((f, h))
                seen_in_file.add(key)

    n_on_files = len(on_files)

    for key, file_hits in on_freq_map.items():
        fk, dk = key
        in_off = key in off_freq_index or (fk - 1, dk) in off_freq_index or (fk + 1, dk) in off_freq_index
        n_on_appearances = len(file_hits)

        if in_off:
            # Matched in OFF = RFI
            for fname, h in file_hits:
                h['matched_in_off'] = True
                h['n_on_appearances'] = n_on_appearances
                rfi_hits.append(h)
        elif n_on_appearances == n_on_files:
            # In ALL ON files but NOT in any OFF = CANDIDATE!
            for fname, h in file_hits:
                h['matched_in_off'] = False
                h['n_on_appearances'] = n_on_appearances
                candidates.append(h)
        else:
            # In some but not all ON files, not in OFF = transient
            for fname, h in file_hits:
                h['matched_in_off'] = False
                h['n_on_appearances'] = n_on_appearances
                on_only_single.append(h)

    # OFF-only hits (false positives in reference)
    on_keys = set(on_freq_map.keys())
    off_only = []
    for f, hits in off_data.items():
        for h in hits:
            fk = freq_key(h['corrected_freq'], freq_tolerance_hz)
            dk = drift_key(h['drift_rate'], drift_tolerance)
            key = (fk, dk)
            if key not in on_keys and (fk-1, dk) not in on_keys and (fk+1, dk) not in on_keys:
                off_only.append(h)

    return {
        'candidates': candidates,
        'rfi': rfi_hits,
        'on_only_single': on_only_single,
        'off_only': off_only,
        'total_on': total_on_hits,
        'total_off': total_off_hits,
        'n_on_files': n_on_files,
        'n_off_files': len(off_files),
    }


def print_report(result):
    """Print formatted RFI rejection report."""
    print("\n" + "=" * 70)
    print("RFI REJECTION REPORT - Full Cadence Analysis")
    print("=" * 70)
    print(f"\nObservations: {result['n_on_files']} ON + {result['n_off_files']} OFF")
    print(f"Total ON hits:  {result['total_on']}")
    print(f"Total OFF hits: {result['total_off']}")

    n_cand = len(result['candidates'])
    n_rfi = len(result['rfi'])
    n_trans = len(result['on_only_single'])
    n_off = len(result['off_only'])

    print(f"\n--- Results ---")
    print(f"RFI (ON + OFF match):         {n_rfi:>5}  <-- REJECTED")
    print(f"Candidates (all ON, no OFF):  {n_cand:>5}  <-- SIGNALS OF INTEREST")
    print(f"Transient (some ON, no OFF):  {n_trans:>5}  <-- likely transient RFI")
    print(f"OFF only (no ON match):       {n_off:>5}  <-- reference noise")

    total_on = max(result['total_on'], 1)
    print(f"\nRFI rejection rate: {n_rfi}/{result['total_on']} = {100*n_rfi/total_on:.0f}% of ON hits matched RFI")

    if result['candidates']:
        print(f"\n{'='*70}")
        print(f"*** CANDIDATE SIGNALS ***")
        print(f"Appeared in ALL {result['n_on_files']} ON observations, NOT in any OFF")
        print(f"{'='*70}")
        print(f"\n{'#':>3} {'Drift (Hz/s)':>13} {'SNR':>7} {'Freq (MHz)':>14} "
              f"{'Channel':>8} {'In N ON files':>13}")
        print("-" * 65)
        for i, h in enumerate(sorted(result['candidates'], key=lambda x: -x['snr'])[:30]):
            print(f"{i+1:>3} {h['drift_rate']:>13.4f} {h['snr']:>7.1f} "
                  f"{h['corrected_freq']:>14.6f} {h['index']:>8} "
                  f"{h.get('n_on_appearances', '?'):>7}/{result['n_on_files']}")

    if result['on_only_single']:
        print(f"\n{'='*70}")
        print(f"TRANSIENT SIGNALS (in some ON files, not all, not in OFF)")
        print(f"These are likely transient RFI, not real candidates")
        print(f"{'='*70}")
        print(f"\n{'#':>3} {'Drift (Hz/s)':>13} {'SNR':>7} {'Freq (MHz)':>14} "
              f"{'In N ON files':>13} {'Source':>20}")
        print("-" * 75)
        for i, h in enumerate(sorted(result['on_only_single'], key=lambda x: -x['snr'])[:20]):
            src = h.get('source_file', '?')[:20]
            print(f"{i+1:>3} {h['drift_rate']:>13.4f} {h['snr']:>7.1f} "
                  f"{h['corrected_freq']:>14.6f} "
                  f"{h.get('n_on_appearances', '?'):>7}/{result['n_on_files']} {src:>20}")
        if len(result['on_only_single']) > 20:
            print(f"  ... and {len(result['on_only_single']) - 20} more")

    if result['rfi']:
        print(f"\n{'='*70}")
        print(f"REJECTED RFI (in both ON and OFF)")
        print(f"{'='*70}")
        print(f"\n{'#':>3} {'Drift (Hz/s)':>13} {'SNR':>7} {'Freq (MHz)':>14}")
        print("-" * 45)
        for i, h in enumerate(sorted(result['rfi'], key=lambda x: -x['snr'])[:20]):
            print(f"{i+1:>3} {h['drift_rate']:>13.4f} {h['snr']:>7.1f} "
                  f"{h['corrected_freq']:>14.6f}")
        if len(result['rfi']) > 20:
            print(f"  ... and {len(result['rfi']) - 20} more")

    print(f"\n{'='*70}")
    if n_cand == 0:
        print("RESULT: No candidate signals found.")
        if result['total_on'] == 0:
            print("Zero hits across all observations. Clean target.")
        else:
            print(f"All {result['total_on']} hits were explained as RFI or transient noise.")
        print("Consistent with no artificial narrowband signals from this target.")
    else:
        print(f"RESULT: {n_cand} candidate signal(s) survived RFI rejection!")
        print("These appear in all ON observations but no OFF observations.")
        print("\nNEXT STEPS:")
        print("1. Check if frequency matches known satellites or ground transmitters")
        print("2. Download fine-resolution files for detailed signal characterization")
        print("3. Search other observation dates for repeatability")
        print("4. This is NOT a confirmed signal, just a candidate worth investigating")
    print(f"{'='*70}")


def plot_results(result, output_path=None):
    """Generate frequency/SNR scatter plot."""
    import matplotlib
    if output_path:
        matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(14, 7))

    if result['rfi']:
        ax.scatter([h['corrected_freq'] for h in result['rfi']],
                   [h['snr'] for h in result['rfi']],
                   c='red', marker='x', s=50, alpha=0.7,
                   label=f"RFI (rejected): {len(result['rfi'])}")

    if result['on_only_single']:
        ax.scatter([h['corrected_freq'] for h in result['on_only_single']],
                   [h['snr'] for h in result['on_only_single']],
                   c='orange', marker='^', s=60, alpha=0.6,
                   label=f"Transient: {len(result['on_only_single'])}")

    if result['candidates']:
        ax.scatter([h['corrected_freq'] for h in result['candidates']],
                   [h['snr'] for h in result['candidates']],
                   c='green', marker='*', s=200, alpha=0.9,
                   label=f"CANDIDATES: {len(result['candidates'])}",
                   edgecolors='black', linewidth=0.5)

    if result['off_only']:
        ax.scatter([h['corrected_freq'] for h in result['off_only']],
                   [h['snr'] for h in result['off_only']],
                   c='gray', marker='.', s=30, alpha=0.4,
                   label=f"OFF only: {len(result['off_only'])}")

    ax.set_xlabel('Frequency (MHz)', fontsize=12)
    ax.set_ylabel('SNR', fontsize=12)
    n_on = result['n_on_files']
    n_off = result['n_off_files']
    ax.set_title(f'RFI Rejection: {n_on} ON vs {n_off} OFF Observations', fontsize=14)
    ax.legend(fontsize=11)
    ax.grid(True, alpha=0.3)

    plt.tight_layout()
    if output_path:
        plt.savefig(output_path, dpi=150)
        print(f"\nPlot saved: {output_path}")
    else:
        plt.show()


def main():
    parser = argparse.ArgumentParser(
        description='RFI rejection for BL cadence observations.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Full 6-file cadence:
    python bl_rfi_reject.py --on ON1.dat ON2.dat ON3.dat --off OFF1.dat OFF2.dat OFF3.dat

    # Single pair:
    python bl_rfi_reject.py --on ON.dat --off OFF.dat

    # With plot:
    python bl_rfi_reject.py --on ON1.dat ON2.dat ON3.dat --off OFF1.dat OFF2.dat OFF3.dat --plot
        """)
    parser.add_argument('--on', nargs='+', required=True, help='ON (source) .dat file(s)')
    parser.add_argument('--off', nargs='+', required=True, help='OFF (reference) .dat file(s)')
    parser.add_argument('--freq-tolerance', type=float, default=500,
                        help='Frequency match tolerance in Hz (default 500)')
    parser.add_argument('--drift-tolerance', type=float, default=0.1,
                        help='Drift rate match tolerance in Hz/s (default 0.1)')
    parser.add_argument('--plot', action='store_true', help='Generate scatter plot')
    parser.add_argument('--save-plot', metavar='PATH', help='Save plot to file')
    args = parser.parse_args()

    # Validate files exist
    for f in args.on + args.off:
        if not os.path.exists(f):
            print(f"Error: file not found: {f}")
            sys.exit(1)

    result = run_rfi_rejection(args.on, args.off,
                               freq_tolerance_hz=args.freq_tolerance,
                               drift_tolerance=args.drift_tolerance)

    print_report(result)

    if args.plot or args.save_plot:
        plot_results(result, args.save_plot)


if __name__ == '__main__':
    main()
