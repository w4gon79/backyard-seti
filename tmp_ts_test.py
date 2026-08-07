#!/usr/bin/env python3
"""Find turbo_seti test data files and run a search on one."""
import os, sys, glob

# Search for .fil and .h5 files in the turbo_seti package and cloned repo
search_paths = [
    os.path.dirname(os.path.dirname(os.path.abspath(turbo_seti.__file__))) if 'turbo_seti' in sys.modules else "",
    "G:/seti/turbo_seti",
]

import turbo_seti
pkg_parent = os.path.dirname(os.path.dirname(turbo_seti.__file__))
search_paths = [pkg_parent, "G:/seti/turbo_seti"]

found = []
for base in search_paths:
    if not base or not os.path.exists(base):
        continue
    for ext in ['*.fil', '*.h5', '*.hdf5']:
        for f in glob.glob(os.path.join(base, '**', ext), recursive=True):
            size = os.path.getsize(f)
            if size < 100 * 1024 * 1024:  # skip files > 100MB
                found.append((f, size))

print(f"Found {len(found)} test data files:")
for f, size in sorted(found):
    print(f"  {f} ({size/1024:.0f} KB)")

# If we found any, run turbo_seti on the first one
if found:
    test_file = found[0][0]
    print(f"\n=== Running turbo_seti on {test_file} ===")
    from turbo_seti.find_doppler.find_doppler import FindDoppler
    
    out_dir = os.path.dirname(test_file)
    doppler = FindDoppler(test_file, min_drift=1e-05, max_drift=5, snr=10, out_dir=out_dir)
    doppler.search()
    
    # Check results
    stem = os.path.splitext(os.path.basename(test_file))[0]
    dat_path = os.path.join(out_dir, stem + '.dat')
    if os.path.exists(dat_path):
        with open(dat_path) as f:
            lines = f.readlines()
        hits = [l for l in lines if not l.startswith('#') and l.strip()]
        print(f"\nResults: {len(hits)} hits")
        for h in hits[:10]:
            print(f"  {h.strip()}")
    else:
        print("No .dat file generated")
else:
    print("No test files found in turbo_seti installation.")
    print("\nLet me create a synthetic filterbank file from scratch.")
