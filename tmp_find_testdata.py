#!/usr/bin/env python3
"""
Direct test: use turbo_seti's own test data to verify the pipeline works.
turbo_seti ships with sample filterbank files containing known signals.
"""
import os, sys

# Find turbo_seti's test data
import turbo_seti
ts_dir = os.path.dirname(turbo_seti.__file__)
print(f"turbo_seti installed at: {ts_dir}")

# Look for test data files
for root, dirs, files in os.walk(os.path.dirname(ts_dir)):
    for f in files:
        if f.endswith('.fil') or f.endswith('.h5'):
            fpath = os.path.join(root, f)
            size = os.path.getsize(fpath)
            if size < 500 * 1024 * 1024:  # skip huge files
                rel = os.path.relpath(fpath, os.path.dirname(ts_dir))
                print(f"  Found: {rel} ({size/1024:.0f} KB)")

# Also check if there's a test directory in the cloned repo
repo_test = "G:/seti/turbo_seti/test"
if os.path.exists(repo_test):
    print(f"\nCloned repo test dir: {repo_test}")
    for f in os.listdir(repo_test):
        fpath = os.path.join(repo_test, f)
        if os.path.isfile(fpath):
            size = os.path.getsize(fpath)
            print(f"  {f} ({size/1024:.0f} KB)")
