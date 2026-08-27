"""Generate Gaia DR3 star-field catalogs for the Mission Control starmap.
Replaces lost tmp_gaia_stars.py (commit a780fdf). Same schema as existing
static/stars/*.json: {target, center_ra, center_dec, stars:[{ra,dec,mag,name}]}
G < 6.5, 20-deg radius, brightest-first.
"""
import json, os, time, urllib.request, urllib.parse

TAP = 'https://gea.esac.esa.int/tap-server/tap/sync'

def gaia_query(center_ra, center_dec, radius_deg=20.0, gmax=6.5, top=300):
    adql = (
        f"SELECT TOP {top} source_id, ra, dec, phot_g_mean_mag "
        f"FROM gaiadr3.gaia_source "
        f"WHERE 1=CONTAINS(POINT('ICRS', ra, dec), CIRCLE('ICRS', "
        f"{center_ra:.5f}, {center_dec:.5f}, {radius_deg})) "
        f"AND phot_g_mean_mag < {gmax} AND phot_g_mean_mag IS NOT NULL "
        f"ORDER BY phot_g_mean_mag ASC")
    params = urllib.parse.urlencode({
        'REQUEST': 'doQuery', 'LANG': 'ADQL', 'FORMAT': 'json', 'QUERY': adql})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(TAP + '?' + params, timeout=120) as r:
                data = json.load(r)
            break
        except Exception as e:
            print(f'  attempt {attempt+1} failed: {e}')
            time.sleep(5)
    else:
        return None
    rows = data['data']
    cols = [c['name'] for c in data['metadata']]
    i_sid, i_ra, i_dec, i_mag = (cols.index('source_id'), cols.index('ra'),
                                 cols.index('dec'), cols.index('phot_g_mean_mag'))
    return [{'ra': float(r[i_ra]), 'dec': float(r[i_dec]),
             'mag': float(r[i_mag]),
             'name': f'Gaia DR3 {r[i_sid]}'} for r in rows]

def header_coords(relpath):
    u = 'http://localhost:8070/api/header?file=' + urllib.parse.quote(relpath, safe='')
    d = json.load(urllib.request.urlopen(u, timeout=60))
    h = d.get('header', {})
    ra_deg = float(h['src_raj']) * 15.0   # src_raj is in HOURS
    dec_deg = float(h['src_dej'])
    return ra_deg, dec_deg

OUT = r'G:\seti\dashboard\static\stars'
jobs = {'MESSIER031': (10.684700, 41.268900)}
for tgt, fn in [('HIP2792', 'fine/blc71_guppi_58832_16530_HIP2792_0058.gpuspec.0000.h5'),
                ('HIP3077', 'fine/blc71_guppi_58832_17168_HIP3077_0060.gpuspec.0000.h5'),
                ('HIP3223', 'fine/blc71_guppi_58832_17801_HIP3223_0062.gpuspec.0000.h5')]:
    try:
        jobs[tgt] = header_coords(fn)
        print(f'{tgt} coords from header: {jobs[tgt]}')
    except Exception as e:
        print(f'{tgt} header lookup failed: {e}')

for tgt, (ra, dec) in jobs.items():
    out = os.path.join(OUT, f'{tgt}.json')
    if os.path.exists(out):
        print(f'{tgt}: exists, skip'); continue
    print(f'{tgt}: querying Gaia at RA {ra:.4f} Dec {dec:.4f}...')
    stars = gaia_query(ra, dec)
    if not stars:
        print(f'{tgt}: QUERY FAILED'); continue
    cat = {'target': tgt, 'center_ra': ra, 'center_dec': dec, 'stars': stars}
    with open(out, 'w') as f:
        json.dump(cat, f)
    print(f'{tgt}: wrote {len(stars)} stars (mag {stars[0]["mag"]:.2f}-{stars[-1]["mag"]:.2f})')
