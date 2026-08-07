#!/usr/bin/env python3
"""Check waterfall data for blank right side issue."""
import json, urllib.request

url = 'http://localhost:8070/api/waterfall?file=fine/Parkes_57791_72989_PROXCEN_S_fine.h5&freq_mhz=2801.729316&width_chans=200'
resp = urllib.request.urlopen(url, timeout=60)
d = json.loads(resp.read())

data = d['data']
freqs = d['freqs']
n_chans = len(data[0])
print('n_chans:', n_chans)
print('n_tints:', len(data))

# Check column-by-column mean values to find the discontinuity
for j in range(0, n_chans, 20):
    col_mean = sum(data[i][j] for i in range(len(data))) / len(data)
    freq = freqs[j]
    print('  col {:3d} ({:.6f} MHz): mean={:.1f}'.format(j, freq, col_mean))

print()
# Check around channel 240 (where blank starts ~60%)
for j in range(230, 270, 5):
    col_mean = sum(data[i][j] for i in range(len(data))) / len(data)
    print('  col {:3d} ({:.6f} MHz): mean={:.1f}'.format(j, freqs[j], col_mean))
