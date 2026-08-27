"""Target Registry (Phase 3A)
============================
SQLite-backed registry of observation targets with coordinates,
aliases, and Breakthrough Listen data availability.

Replaces the hardcoded TARGET_COORDS dict in barycentric_correct.py as
the source of truth for target coordinates. The dict remains as a
seed/fallback; this table wins.

Tables (created idempotently by ensure_table):
    targets: name, display_name, aliases (JSON list), ra_hours, dec_deg,
             coord_source ('seed'|'simbad'|'manual'), BL availability
             counters, priority, notes
"""
import json
import os
import re
import sqlite3
import sys
import time as _time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

_PKG_DIR = os.path.dirname(os.path.abspath(__file__))
if _PKG_DIR not in sys.path:
    sys.path.insert(0, _PKG_DIR)

from db import get_db

# Seed registry from the legacy TARGET_COORDS dict (barycentric_correct).
# canonical name, display name, aliases, ra_hours, dec_deg
SEED_TARGETS = [
    ('PROXCEN', 'Proxima Centauri', ['PROXIMA_CEN', 'PROXIMA CENTAURI'],
     14.49527778, -62.67948889),
    ('TAU_CETI', 'Tau Ceti', [], 1.73111111, -15.93747222),
    ('KIC_8462852', "Tabby's Star", ['TABBY', 'KIC 8462852'],
     20.06903750, 44.45683333),
    ('HIP113357', 'HIP 113357', [], 22.78750000, -16.26666667),
    ('HD164595', 'HD 164595', [], 18.01208333, 39.14138889),
    ('TRAPPIST1', 'TRAPPIST-1', ['TRAPPIST-1'], 23.06283333, -5.04138889),
    ('WOLF359', 'Wolf 359', [], 10.93450000, 7.01450000),
    ('BARNARDS_STAR', "Barnard's Star", ['BARNARD'],
     17.95194444, 4.69450000),
    ('GLIESE581', 'Gliese 581', ['GL 581'], 15.32541667, -7.72000000),
    ('HD95735', 'HD 95735 (Lalande 21185)', ['GJ411', 'LALANDE 21185'],
     11.03250000, 33.28600000),
]

SIMBAD_TAP = 'https://simbad.u-strasbg.fr/simbad/sim-tap/sync'
BL_API = 'https://seti.berkeley.edu/opendata/api/query-files'
NAME_RE = re.compile(r'^[A-Za-z0-9_+\-]+$')


def ensure_table(db_path=None):
    """Create the targets table if missing; seed on first creation."""
    conn = get_db(db_path)
    try:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS targets (
                name            TEXT PRIMARY KEY,
                display_name    TEXT,
                aliases         TEXT DEFAULT '[]',
                ra_hours        REAL,
                dec_deg         REAL,
                coord_source    TEXT,
                bl_fine_files   INTEGER,
                bl_fine_epochs  INTEGER,
                bl_query_name   TEXT,
                bl_total_files  INTEGER,
                bl_checked_at   TEXT,
                priority        INTEGER DEFAULT 0,
                notes           TEXT,
                created_at      TEXT DEFAULT (datetime('now'))
            )
        ''')
        # Migration for DBs created before bl_query_name/bl_total_files
        for _col, _typ in (('bl_query_name', 'TEXT'),
                           ('bl_total_files', 'INTEGER')):
            try:
                conn.execute(f'ALTER TABLE targets ADD COLUMN {_col} {_typ}')
            except sqlite3.OperationalError:
                pass  # column already exists
        n = conn.execute('SELECT COUNT(*) FROM targets').fetchone()[0]
        if n == 0:
            for name, disp, aliases, ra, dec in SEED_TARGETS:
                conn.execute('''
                    INSERT OR IGNORE INTO targets
                    (name, display_name, aliases, ra_hours, dec_deg,
                     coord_source, priority)
                    VALUES (?, ?, ?, ?, ?, 'seed', ?)
                ''', (name, disp, json.dumps(aliases), ra, dec,
                      10 if name == 'PROXCEN' else 0))
            print(f'[registry] seeded {len(SEED_TARGETS)} targets')
        conn.commit()
    finally:
        conn.close()


def _row_to_target(row):
    d = dict(row)
    try:
        d['aliases'] = json.loads(d.get('aliases') or '[]')
    except (TypeError, ValueError):
        d['aliases'] = []
    return d


def list_targets(db_path=None):
    """All registry rows, name order."""
    conn = get_db(db_path)
    try:
        rows = conn.execute(
            'SELECT * FROM targets ORDER BY priority DESC, name').fetchall()
        return [_row_to_target(r) for r in rows]
    finally:
        conn.close()


def get_target(name, db_path=None):
    """Case-insensitive lookup by canonical name or alias."""
    if not name:
        return None
    conn = get_db(db_path)
    try:
        row = conn.execute(
            'SELECT * FROM targets WHERE UPPER(name) = UPPER(?)',
            (str(name),)).fetchone()
        if row:
            return _row_to_target(row)
        for row in conn.execute('SELECT * FROM targets').fetchall():
            aliases = []
            try:
                aliases = json.loads(row['aliases'] or '[]')
            except (TypeError, ValueError):
                pass
            if str(name).upper() in [a.upper() for a in aliases]:
                return _row_to_target(row)
        return None
    finally:
        conn.close()


# --- SIMBAD name translation --------------------------------------------
# SIMBAD stores Bayer designations as 3-letter greek + IAU constellation
# abbreviation ("eps Eri"), never spelled out ("epsilon eridani"). Ident
# matching is case-sensitive, and substring matches on numbered stars
# return junk ("61 Vir" matches "V* V461 Vir"), so we translate typed
# names locally and match idents exactly.
_GREEK_PREFIX = {
    'alpha': ('alf', 'alp'), 'alf': ('alf',), 'alp': ('alp', 'alf'),
    'beta': ('bet',), 'bet': ('bet',),
    'gamma': ('gam',), 'gam': ('gam',),
    'delta': ('del',), 'del': ('del',),
    'epsilon': ('eps',), 'eps': ('eps',),
    'zeta': ('zet',), 'zet': ('zet',),
    'eta': ('eta',), 'eta': ('eta',),
    'theta': ('the', 'tet'), 'the': ('the', 'tet'), 'tet': ('tet', 'the'),
    'iota': ('iot',), 'iot': ('iot',),
    'kappa': ('kap',), 'kap': ('kap',),
    'lambda': ('lam',), 'lam': ('lam',),
    'mu': ('mu',), 'nu': ('nu',), 'xi': ('xi',),
    'omicron': ('omi',), 'omi': ('omi',),
    'pi': ('pi',), 'rho': ('rho',),
    'sigma': ('sig',), 'sig': ('sig',),
    'tau': ('tau',), 'tau': ('tau',),
    'upsilon': ('ups',), 'ups': ('ups',),
    'phi': ('phi',), 'chi': ('chi',), 'psi': ('psi',),
    'omega': ('ome',), 'ome': ('ome',),
}

# (abbrev, nominative, genitive), all lowercase keys in lookup
_CONST_ABBREV = [
    ('And', 'andromeda', 'andromedae'), ('Ant', 'antlia', 'antliae'),
    ('Aps', 'apus', 'apodis'), ('Aqr', 'aquarius', 'aquarii'),
    ('Aql', 'aquila', 'aquilae'), ('Ara', 'ara', 'arae'),
    ('Ari', 'aries', 'arietis'), ('Aur', 'auriga', 'aurigae'),
    ('Boo', 'bootes', 'bootis'), ('Cae', 'caelum', 'caeli'),
    ('Cam', 'camelopardalis', 'camelopardalis'), ('Cnc', 'cancer', 'cancri'),
    ('CMa', 'canis major', 'canis majoris'),
    ('CMi', 'canis minor', 'canis minoris'),
    ('CVn', 'canes venatici', 'canum venaticorum'),
    ('Cap', 'capricornus', 'capricorni'), ('Car', 'carina', 'carinae'),
    ('Cas', 'cassiopeia', 'cassiopeiae'), ('Cen', 'centaurus', 'centauri'),
    ('Cep', 'cepheus', 'cephei'), ('Cet', 'cetus', 'ceti'),
    ('Cha', 'chamaeleon', 'chamaeleontis'), ('Cir', 'circinus', 'circini'),
    ('Col', 'columba', 'columbae'), ('Com', 'coma berenices', 'comae berenices'),
    ('CrA', 'corona australis', 'coronae australis'),
    ('CrB', 'corona borealis', 'coronae borealis'),
    ('Crv', 'corvus', 'corvi'), ('Crt', 'crater', 'crateris'),
    ('Cru', 'crux', 'crucis'), ('Cyg', 'cygnus', 'cygni'),
    ('Del', 'delphinus', 'delphini'), ('Dor', 'dorado', 'doradus'),
    ('Dra', 'draco', 'draconis'), ('Equ', 'equuleus', 'equulei'),
    ('Eri', 'eridanus', 'eridani'), ('For', 'fornax', 'fornacis'),
    ('Gem', 'gemini', 'geminorum'), ('Gru', 'grus', 'gruis'),
    ('Her', 'hercules', 'herculis'), ('Hor', 'horologium', 'horologii'),
    ('Hya', 'hydra', 'hydrae'), ('Hyi', 'hydrus', 'hydri'),
    ('Ind', 'indus', 'indi'), ('Lac', 'lacerta', 'lacertae'),
    ('Leo', 'leo', 'leonis'), ('LMi', 'leo minor', 'leonis minoris'),
    ('Lep', 'lepus', 'leporis'), ('Lib', 'libra', 'librae'),
    ('Lup', 'lupus', 'lupi'), ('Lyn', 'lynx', 'lyncis'),
    ('Lyr', 'lyra', 'lyrae'), ('Men', 'mensa', 'mensae'),
    ('Mic', 'microscopium', 'microscopii'), ('Mon', 'monoceros', 'monocerotis'),
    ('Mus', 'musca', 'muscae'), ('Nor', 'norma', 'normae'),
    ('Oct', 'octans', 'octantis'), ('Oph', 'ophiuchus', 'ophiuchi'),
    ('Ori', 'orion', 'orionis'), ('Pav', 'pavo', 'pavonis'),
    ('Peg', 'pegasus', 'pegasi'), ('Per', 'perseus', 'persei'),
    ('Phe', 'phoenix', 'phoenicis'), ('Pic', 'pictor', 'pictoris'),
    ('Psc', 'pisces', 'piscium'),
    ('PsA', 'piscis austrinus', 'piscis austrini'),
    ('Pup', 'puppis', 'puppis'), ('Pyx', 'pyxis', 'pyxidis'),
    ('Ret', 'reticulum', 'reticuli'), ('Sge', 'sagitta', 'sagittae'),
    ('Sgr', 'sagittarius', 'sagittarii'), ('Sco', 'scorpius', 'scorpii'),
    ('Scl', 'sculptor', 'sculptoris'), ('Sct', 'scutum', 'scuti'),
    ('Ser', 'serpens', 'serpentis'), ('Sex', 'sextans', 'sextantis'),
    ('Tau', 'taurus', 'tauri'), ('Tel', 'telescopium', 'telescopii'),
    ('Tri', 'triangulum', 'trianguli'),
    ('TrA', 'triangulum australe', 'trianguli australis'),
    ('Tuc', 'tucana', 'tucanae'),
    ('UMa', 'ursa major', 'ursae majoris'),
    ('UMi', 'ursa minor', 'ursae minoris'),
    ('Vel', 'vela', 'velorum'), ('Vir', 'virgo', 'virginis'),
    ('Vol', 'volans', 'volantis'), ('Vul', 'vulpecula', 'vulpeculae'),
]
_CONST_LOOKUP = {}
for _ab, _nom, _gen in _CONST_ABBREV:
    _CONST_LOOKUP[_nom] = _ab
    _CONST_LOOKUP[_gen] = _ab
    _CONST_LOOKUP[_ab.lower()] = _ab


def _simbad_candidates(raw):
    """SIMBAD ident candidates for a cleaned name, best first."""
    cands = []

    def add(s):
        s = s.strip()
        if s and s not in cands:
            cands.append(s)

    toks = raw.split()
    # Messier / NGC normalization (2026-08-26): SIMBAD idents use spaced
    # forms ('M 31', main_id 'M  31'), so 'M31' must try those variants or
    # a single failed exact-id query strands the user with no match.
    m_ngc = re.fullmatch(r'[Nn][Gg][Cc]\s*(\d{1,4})', raw)
    m_mes = re.fullmatch(r'[Mm]\s*(\d{1,3})', raw)
    if m_ngc:
        n = int(m_ngc.group(1))
        for v in (f'NGC {n}', f'NGC  {n}', f'NGC{n}'):
            add(v)
    elif m_mes:
        n = int(m_mes.group(1))
        for v in (f'M {n}', f'M  {n}', f'MESSIER {n:03d}', f'MESSIER{n:03d}',
                  f'MESSIER {n}', f'M{n:03d}'):
            add(v)
    if len(toks) >= 2:
        first = toks[0].lower()
        greeks = _GREEK_PREFIX.get(first)
        if greeks:
            rest = ' '.join(toks[1:])
            suffix = ''
            if len(toks) >= 3 and toks[-1].upper() in (
                    'A', 'B', 'C', 'AB', 'AC', 'BC', 'D'):
                suffix = ' ' + toks[-1].upper()
                rest = ' '.join(toks[1:-1])
            const = _CONST_LOOKUP.get(rest.lower())
            if not const and len(rest) == 3:
                const = rest.title()  # user already typed the abbrev
            if const:
                for g in greeks:
                    add(f'{g} {const}{suffix}')
    # whole-name case variants (ident match is case-sensitive)
    add(raw)
    if len(toks) >= 2:
        add(' '.join([toks[0]] + [t.capitalize() for t in toks[1:]]))
    add(raw.upper())
    if len(toks) == 1:
        add(raw.capitalize())
    return cands


def _tap_rows(query, retries=3):
    """Run a SIMBAD TAP sync query, return data rows.
    Retries transient failures; RAISES RuntimeError on final failure so
    callers can tell 'query failed' apart from 'no such object'
    (2026-08-26: previously returned [] on error, which surfaced to users
    as the misleading 'No SIMBAD match')."""
    url = SIMBAD_TAP + '?' + urllib.parse.urlencode(
        {'request': 'doQuery', 'lang': 'adql', 'format': 'json',
         'query': query})
    last_err = None
    for attempt in range(retries):
        if attempt:
            _time.sleep(2 * attempt)
        req = urllib.request.Request(url,
                                     headers={'User-Agent': 'BackyardSETI/1.0'})
        try:
            with urllib.request.urlopen(req, timeout=25) as r:
                data = json.loads(r.read().decode())
            return data.get('data', [])
        except Exception as e:
            last_err = e
            print(f'[registry] SIMBAD TAP attempt {attempt + 1}/{retries} failed: {e}')
    raise RuntimeError(f'SIMBAD query failed after {retries} attempts: {last_err}')


def simbad_search(name, limit=5):
    """Resolve a target name via SIMBAD. Returns list of
    {main_id, ra_hours, dec_deg}, best match first, [] on no match.
    Strategy: translate to SIMBAD-style identifiers locally (Bayer greek
    + constellation abbrev), match idents exactly preferring stars
    (main_id starts with '*'), then fall back to substring search for
    proper names like "Proxima Cen"."""
    raw = str(name).replace('%', '').replace('"', '')
    raw = re.sub(r'[_\s]+', ' ', raw).strip()
    if not raw:
        return []
    limit = int(limit)

    out, seen = [], set()
    for cand in _simbad_candidates(raw):
        esc = cand.replace("'", "''")
        q = ("SELECT TOP {n} b.main_id, b.ra, b.dec FROM basic b "
             "JOIN ident i ON i.oidref = b.oid WHERE i.id = '{c}'"
             ).format(n=limit, c=esc)
        rows = _tap_rows(q)
        rows.sort(key=lambda r: 0 if str(r[0]).startswith('*') else 1)
        for main_id, ra, dec in rows:
            if ra is None or dec is None or main_id in seen:
                continue
            seen.add(main_id)
            out.append({'main_id': main_id,
                        'ra_hours': float(ra) / 15.0,
                        'dec_deg': float(dec)})
        if out:
            return out[:limit]

    # Last resort: substring on ident (works for proper names).
    # 2026-08-26: pattern had a mandatory trailing space ('%name %') which
    # missed most idents; plain contains now.
    for pat in (raw, raw.capitalize()):
        esc = pat.replace("'", "''")
        q = ("SELECT TOP {n} b.main_id, b.ra, b.dec FROM basic b "
             "JOIN ident i ON i.oidref = b.oid "
             "WHERE i.id LIKE '%{p} %'").format(n=limit, p=esc)
        try:
            rows = _tap_rows(q)
        except RuntimeError:
            break  # network dead; don't retry substring variants
        for main_id, ra, dec in rows:
            if ra is None or dec is None or main_id in seen:
                continue
            seen.add(main_id)
            out.append({'main_id': main_id,
                        'ra_hours': float(ra) / 15.0,
                        'dec_deg': float(dec)})
        if out:
            break
    return out[:limit]


# Catalog prefixes BL is known to index (used to rank SIMBAD cross-ids)
_BL_CATALOG_RANK = ('GJ', 'HIP', 'HD', 'KIC', 'TRAPPIST', 'NAME', 'BD',
                    'LHS', 'ROSS', 'WOLF', 'EPIC', 'TIC')


def _bl_token(s):
    """BL catalog tokens are separator-free: strip spaces, underscores,
    hyphens, dots (KIC_8462852 -> KIC8462852, 'GJ 71' -> GJ71)."""
    return re.sub(r'[\s_\-.]+', '', str(s)).upper()


def _bl_query(name):
    """One BL API query. Returns (n_files, n_fine, mjd_set), None on error."""
    url = BL_API + '?target=' + urllib.parse.quote(str(name))
    req = urllib.request.Request(url, headers={'User-Agent': 'BackyardSETI/1.0'})
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            data = json.loads(r.read().decode())
    except Exception:
        return None
    files = data.get('data', [])
    n_fine, mjds = 0, set()
    for f in files:
        u = f.get('url') or f.get('filename') or ''
        if '_fine.' in u:
            n_fine += 1
            parts = u.split('/')[-1].split('_')
            if len(parts) > 1 and parts[1].isdigit():
                mjds.add(parts[1])
    return len(files), n_fine, mjds


def simbad_idents(main_id, limit=40):
    """All SIMBAD identifiers for an object."""
    esc = str(main_id).replace("'", "''")
    q = ("SELECT TOP {n} i.id FROM ident i JOIN basic b "
         "ON i.oidref = b.oid WHERE b.main_id = '{e}'"
         ).format(n=int(limit), e=esc)
    return [r[0] for r in _tap_rows(q) if r and r[0]]


def _bl_result(n_files, n_fine, mjds, query):
    return {'n_files': n_files, 'n_fine': n_fine,
            'fine_epochs': sorted(mjds), 'n_fine_epochs': len(mjds),
            'bl_query_name': query,
            'checked_at': datetime.now(timezone.utc).isoformat(
                timespec='seconds')}


def check_bl_availability(name, prior_query=None, variants=None,
                         simbad_names=None):
    """Query BL open data, trying name variants until one matches.

    BL's ?target= match is literal (case-insensitive) against
    separator-free catalog tokens: KIC8462852 hits, KIC_8462852 misses;
    'GJ 71' hits, GJ71 hits, GJ_71 misses. So the canonical registry
    name often misses. Candidate order: remembered winning query, the
    stripped canonical name, stripped aliases/variants, then SIMBAD
    cross-ids (Barnard's Star -> GJ699). First query returning files
    wins and is stored as bl_query_name for next time."""
    cands, seen = [], set()

    def add(raw):
        if not raw:
            return
        for c in (_bl_token(raw), str(raw).strip().upper()):
            if c and c not in seen:
                seen.add(c)
                cands.append(c)

    add(prior_query)
    add(name)
    for v in (variants or []):
        add(v)

    first_error = None
    best = (0, None, 0, set())
    for c in cands:
        res = _bl_query(c)
        if res is None:
            if first_error is None:
                first_error = c
            continue
        n_files, n_fine, mjds = res
        if n_files > 0:
            return _bl_result(n_files, n_fine, mjds, c)
        if n_files > best[0]:
            best = (n_files, c, n_fine, mjds)

    # Nothing yet: try SIMBAD cross-ids in BL's preferred catalog forms.
    # simbad_names (e.g. display_name "Barnard's Star") helps when the
    # canonical registry name can't resolve (BARNARDS_STAR has no
    # apostrophe, SIMBAD indexes the possessive form).
    resolved = []
    for nm in [name] + list(simbad_names or []):
        resolved = simbad_search(nm, limit=1)
        if resolved:
            break
    if resolved:
        idents = simbad_idents(resolved[0]['main_id'])
        idents.sort(key=lambda s: (
            0 if str(s).split(' ')[0].split('-')[0] in _BL_CATALOG_RANK
            else 1, len(s)))
        tried = 0
        for ident in idents:
            tok = _bl_token(ident)
            if not tok or tok in seen or tried >= 12:
                continue
            seen.add(tok)
            tried += 1
            res = _bl_query(tok)
            if res is None:
                continue
            n_files, n_fine, mjds = res
            if n_files > 0:
                return _bl_result(n_files, n_fine, mjds, tok)
            if n_files > best[0]:
                best = (n_files, tok, n_fine, mjds)

    if best[1] is None and first_error is not None:
        return {'error': f'BL API failed for all variants '
                         f'(first error at "{first_error}")'}
    return _bl_result(best[0], best[2], best[3], best[1])


def _store_bl(name, avail, db_path=None):
    if avail.get('error'):
        return
    conn = get_db(db_path)
    try:
        conn.execute('''
            UPDATE targets SET bl_fine_files = ?, bl_fine_epochs = ?,
                              bl_query_name = ?, bl_total_files = ?,
                              bl_checked_at = ?
            WHERE name = ?
        ''', (avail['n_fine'], avail['n_fine_epochs'],
              avail.get('bl_query_name'), avail['n_files'],
              avail['checked_at'], name))
        conn.commit()
    finally:
        conn.close()


def add_target(name, ra_hours=None, dec_deg=None, display_name=None,
               aliases=None, notes=None, priority=0, check_bl=True,
               db_path=None):
    """Add a target. Coordinates: manual > SIMBAD. Raises ValueError on
    bad name, duplicate, or unresolvable coordinates."""
    name = str(name).strip().upper().replace(' ', '_')
    if not NAME_RE.match(name):
        raise ValueError(
            f'Invalid target name "{name}" (letters/digits/_/+/- only)')
    ensure_table(db_path)
    if get_target(name, db_path):
        raise ValueError(f'Target "{name}" (or alias) already in registry')

    coord_source = None
    if ra_hours is not None and dec_deg is not None:
        ra_hours, dec_deg = float(ra_hours), float(dec_deg)
        coord_source = 'manual'
    else:
        cands = simbad_search(name)
        if cands:
            ra_hours = cands[0]['ra_hours']
            dec_deg = cands[0]['dec_deg']
            coord_source = 'simbad'
        else:
            raise ValueError(
                f'Could not resolve coordinates for "{name}" '
                f'(no manual coords, SIMBAD no match)')

    conn = get_db(db_path)
    try:
        conn.execute('''
            INSERT INTO targets
            (name, display_name, aliases, ra_hours, dec_deg,
             coord_source, priority, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (name, display_name or name,
              json.dumps([a.upper() for a in (aliases or [])]),
              ra_hours, dec_deg, coord_source, priority, notes))
        conn.commit()
    finally:
        conn.close()

    avail = None
    if check_bl:
        avail = check_bl_availability(
            name, variants=aliases,
            simbad_names=[display_name] if display_name else None)
        _store_bl(name, avail, db_path)
    return get_target(name, db_path)


def refresh_bl(name, db_path=None):
    """Re-run BL availability for a registered target."""
    t = get_target(name, db_path)
    if not t:
        raise ValueError(f'Target "{name}" not in registry')
    avail = check_bl_availability(
        t['name'], prior_query=t.get('bl_query_name'),
        variants=t.get('aliases'),
        simbad_names=[t['display_name']]
        if t.get('display_name') and t['display_name'] != t['name'] else None)
    _store_bl(t['name'], avail, db_path)
    return avail


if __name__ == '__main__':
    ensure_table()
    for t in list_targets():
        print(f"{t['name']:<16} ra={t['ra_hours']:.4f} dec={t['dec_deg']:.4f} "
              f"src={t['coord_source']} bl_fine={t['bl_fine_files']} "
              f"bl_epochs={t['bl_fine_epochs']}")
