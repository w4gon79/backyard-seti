#!/usr/bin/env python3
"""Check BL API field names for filter debugging."""
import json, urllib.request

url = 'https://seti.berkeley.edu/opendata/api/query-files?target=PROXCEN'
req = urllib.request.Request(url, headers={'User-Agent': 'BackyardSETI/1.0'})
with urllib.request.urlopen(req, timeout=30) as resp:
    data = json.loads(resp.read())

total = len(data['data'])
print(f'Total observations: {total}')

# Show first 5 with all fields
for i, obs in enumerate(data['data'][:5]):
    print(f'\n--- Observation {i} ---')
    for k, v in obs.items():
        print(f'  {k}: {repr(v)[:80]}')

# Check unique values for resolution and file_type
res_values = set()
ft_values = set()
for obs in data['data']:
    res_values.add(obs.get('resolution', 'MISSING'))
    ft_values.add(obs.get('file_type', 'MISSING'))

print(f'\nUnique resolution values: {res_values}')
print(f'Unique file_type values: {ft_values}')
