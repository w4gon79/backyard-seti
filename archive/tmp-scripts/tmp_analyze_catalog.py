"""Pull the 161 fine-res candidates and categorize them for scan prioritization."""
import json
import urllib.request

URL = ('http://localhost:8070/api/blcatalog?fine_only=1&min_epochs=3&require_onoff=1')
with urllib.request.urlopen(URL, timeout=30) as r:
    data = json.load(r)

targets = data['targets']
print(f"total: {len(targets)}")

GALAXY_PREFIXES = ('NGC', 'IC ', 'UGC', 'M31', 'M33', 'M51', 'M81', 'M82', 'M87', 'M101', 'M104', 'M106')
KNOWN_NAMED = {
    'Proxima', 'Barnard', 'Wolf 359', 'Lalande', 'Ross ', 'Tau Ceti', 'Epsilon Eri',
    'Kepler', 'TRAPPIST', 'KIC', 'KOI', 'Gliese', 'GJ ', 'HD ', 'HIP2', 'LHS',
    'Wolf 1061', 'Teegarden', 'YZ Ceti', 'Luyten', '61 Cyg', '70 Oph', 'Altair',
    'Vega', 'Fomalhaut', 'Sirius', 'Alpha Cen', 'Eps Ind', '61 Vir', 'HD '
}

galaxies, named, coord_only = [], [], []
for t in targets:
    name = t['target']
    if any(name.startswith(p) for p in GALAXY_PREFIXES):
        galaxies.append(t)
    elif any(k in name for k in KNOWN_NAMED):
        named.append(t)
    else:
        coord_only.append(t)

print(f"\n=== GALAXIES ({len(galaxies)}) ===")
for t in sorted(galaxies, key=lambda x: -x['fine_epochs']):
    print(f"{t['target']:20s} epochs={t['fine_epochs']:3d} files={t['n_fine']:5d} "
          f"on/off={t['fine_on']}/{t['fine_off']} tel={t.get('telescopes','')} "
          f"GB={t['fine_bytes']/1e9:7.1f}")

print(f"\n=== NAMED STARS/OTHER ({len(named)}) ===")
for t in sorted(named, key=lambda x: -x['fine_epochs'])[:40]:
    print(f"{t['target']:24s} epochs={t['fine_epochs']:3d} files={t['n_fine']:5d} "
          f"on/off={t['fine_on']}/{t['fine_off']}")

print(f"\n=== TOP 25 BY EPOCHS (any category) ===")
for t in sorted(targets, key=lambda x: -x['fine_epochs'])[:25]:
    print(f"{t['target']:24s} epochs={t['fine_epochs']:3d} files={t['n_fine']:5d} "
          f"on/off={t['fine_on']}/{t['fine_off']} tel={t.get('telescopes','')}")

print(f"\ncoord-only count: {len(coord_only)}")
