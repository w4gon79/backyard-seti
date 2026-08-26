"""Find poison checkpoints: any results/*/checkpoint.json that is empty or
invalid JSON. A bad one anywhere in results/ can crash resume endpoints
that glob every scan dir."""
import glob
import json
import os

bad = []
for p in glob.glob(r'G:\seti\results\*\checkpoint.json'):
    try:
        if os.path.getsize(p) == 0:
            bad.append((p, '0 bytes'))
            continue
        with open(p) as f:
            json.load(f)
    except Exception as e:
        bad.append((p, f'{type(e).__name__}: {str(e)[:60]}'))

if bad:
    print('BAD CHECKPOINTS:')
    for p, why in bad:
        print(f'  {p}  ({why})')
else:
    print('all checkpoints valid')
