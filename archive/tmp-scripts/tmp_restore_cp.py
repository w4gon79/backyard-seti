"""Restore the truncated NGC1426 scan checkpoint from last known state."""
import json
import time

CP = r'G:\seti\results\NGC1426_58058_2026-08-16_1418\checkpoint.json'
state = {
    "file_index": 0,
    "file_total": 4,
    "file_name": "Parkes_58058_44645_NGC1426_S_fine.h5",
    "sub_band_index": 795,
    "sub_band_total": 958,
    "completed_files": [],
    "timestamp": time.strftime('%Y-%m-%dT%H:%M:%S'),
}
with open(CP, 'w') as f:
    json.dump(state, f, indent=2)
print('restored:', json.load(open(CP)))
