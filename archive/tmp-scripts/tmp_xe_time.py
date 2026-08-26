import time, sys
sys.path.insert(0, r'G:\seti\src')
from db import cross_epoch_search_sql

scans = ['GJ191_57782_2026-08-21_2131', 'GJ191_57927_2026-08-22_1112', 'GJ191_57813_2026-08-23_1300']
t0 = time.time()
r = cross_epoch_search_sql(scans, min_snr=0, tolerance_hz=10, min_epochs=2)
print(f"3-epoch search: {time.time()-t0:.1f}s, candidates={r['summary']['total_candidates']}, on_freqs={r['summary']['total_on_frequencies']}")
