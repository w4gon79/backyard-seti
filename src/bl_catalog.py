"""BL Catalog: cached sweep of every BL target's file availability.

BL's API has no open "return everything" endpoint for h5 products (the
empty-target door only dumps a 10k-capped list of GBT raw voltage
files), so open browsing is materialized by a one-time background
sweep: list-targets -> base names (strip _S/_R variants; the base
query returns all variant files) -> query each once -> aggregate into
the bl_catalog table. Resumable (swept targets are skipped unless
force) and cancellable.
"""
import json
import re
import threading
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from db import get_db

BL_API = 'https://seti.berkeley.edu/opendata/api'
_UA = {'User-Agent': 'BackyardSETI/1.0'}

# Parkes grammar: Tel_MJD_SEQ_TARGET_[_SR]_res.h5 (S/R position marker
# optional; bare files carry no ON/OFF information)
_PARKES_PAT = re.compile(
    r'(Parkes|GBT|APF)_(\d+)_(\d+)_([A-Za-z0-9+\-.]+?)(?:_([SR]))?_'
    r'(fine|mid|time)\.h5$')
# GBT guppi grammar: *guppi_MJD_SEQ_TARGET_*.h5 (high-res gpuspec
# products; no S/R cadence markers in these names)
_GBT_PAT = re.compile(r'guppi_(\d+)_(\d+)_([A-Za-z0-9+\-.]+?)_.*\.h5$')

sweep_state = {'active': False, 'cancel': False, 'total': 0, 'done': 0,
               'errors': 0, 'started_at': None, 'finished_at': None,
               'last_error': None}
_lock = threading.Lock()


def ensure_table(db_path=None):
    conn = get_db(db_path)
    try:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS bl_catalog (
                target       TEXT PRIMARY KEY,
                n_files      INTEGER,
                n_fine       INTEGER,
                n_mid        INTEGER,
                n_time       INTEGER,
                fine_epochs  INTEGER,
                fine_on      INTEGER,
                fine_off     INTEGER,
                fine_bytes   INTEGER,
                total_bytes  INTEGER,
                telescopes   TEXT,
                swept_at     TEXT
            )
        ''')
        conn.commit()
    finally:
        conn.close()


def _base_names():
    req = urllib.request.Request(BL_API + '/list-targets', headers=_UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        names = json.loads(r.read().decode())
    bases = set()
    for n in names:
        n = str(n).strip()
        if n:
            bases.add(re.sub(r'_[SR]$', '', n))
    return sorted(bases)


def _query_target(name):
    url = BL_API + '/query-files?target=' + urllib.parse.quote(name)
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode()).get('data', [])


def _aggregate(name, files):
    agg = dict(target=name, n_files=len(files), n_fine=0, n_mid=0,
               n_time=0, fine_epochs=0, fine_on=0, fine_off=0,
               fine_bytes=0, total_bytes=0, telescopes=set(), _mjds=set())
    for f in files:
        fname = (f.get('url') or '').split('/')[-1]
        sz = f.get('size') or f.get('filesize') or 0
        agg['total_bytes'] += sz
        m = _PARKES_PAT.search(fname)
        if m:
            tel, mjd, seq, tgt, pos, res = m.groups()
            agg['telescopes'].add(tel)
            if res == 'fine':
                agg['n_fine'] += 1
                agg['fine_bytes'] += sz
                agg['_mjds'].add(mjd)
                if pos == 'S':
                    agg['fine_on'] += 1
                elif pos == 'R':
                    agg['fine_off'] += 1
            elif res == 'mid':
                agg['n_mid'] += 1
            elif res == 'time':
                agg['n_time'] += 1
            continue
        m = _GBT_PAT.search(fname)
        if m:  # GBT high-res h5: count as fine, cadence unknown
            mjd = m.group(1)
            agg['telescopes'].add('GBT')
            agg['n_fine'] += 1
            agg['fine_bytes'] += sz
            agg['_mjds'].add(mjd)
    agg['fine_epochs'] = len(agg['_mjds'])
    agg['telescopes'] = ','.join(sorted(agg['telescopes']))
    del agg['_mjds']
    return agg


def _upsert(agg, db_path=None):
    conn = get_db(db_path)
    try:
        conn.execute('''
            INSERT INTO bl_catalog
                (target, n_files, n_fine, n_mid, n_time, fine_epochs,
                 fine_on, fine_off, fine_bytes, total_bytes, telescopes,
                 swept_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(target) DO UPDATE SET
                n_files=excluded.n_files, n_fine=excluded.n_fine,
                n_mid=excluded.n_mid, n_time=excluded.n_time,
                fine_epochs=excluded.fine_epochs,
                fine_on=excluded.fine_on, fine_off=excluded.fine_off,
                fine_bytes=excluded.fine_bytes,
                total_bytes=excluded.total_bytes,
                telescopes=excluded.telescopes,
                swept_at=excluded.swept_at
        ''', (agg['target'], agg['n_files'], agg['n_fine'], agg['n_mid'],
              agg['n_time'], agg['fine_epochs'], agg['fine_on'],
              agg['fine_off'], agg['fine_bytes'], agg['total_bytes'],
              agg['telescopes'],
              datetime.now(timezone.utc).isoformat(timespec='seconds')))
        conn.commit()
    finally:
        conn.close()


def _worker(name):
    try:
        _upsert(_aggregate(name, _query_target(name)))
        return True
    except Exception as e:
        with _lock:
            sweep_state['errors'] += 1
            sweep_state['last_error'] = f'{name}: {e}'
        return False


def start_sweep(force=False, mode=None, db_path=None):
    """Launch the background sweep. Returns (started, info).

    Modes: 'resume' (default; skip already-swept targets), 'all'
    (= force), 'refresh' (re-aggregate already-swept rows first with
    current logic, then sweep the remainder; used after an aggregation
    bug fix)."""
    mode = mode or ('all' if force else 'resume')
    with _lock:
        if sweep_state['active']:
            return False, 'sweep already active'
        sweep_state.update(active=True, cancel=False, errors=0, done=0,
                           started_at=datetime.now(timezone.utc).isoformat(
                               timespec='seconds'),
                           finished_at=None, last_error=None)
    try:
        names = _base_names()
    except Exception as e:
        sweep_state.update(active=False, last_error=f'list-targets: {e}')
        return False, str(e)
    if mode != 'all':
        conn = get_db(db_path)
        try:
            swept = {r[0].upper() for r in conn.execute(
                'SELECT target FROM bl_catalog').fetchall()}
        finally:
            conn.close()
        if mode == 'refresh':
            # re-do swept rows first (fixed aggregation), then the rest
            names = ([n for n in names if n.upper() in swept] +
                     [n for n in names if n.upper() not in swept])
        else:
            names = [n for n in names if n.upper() not in swept]
    with _lock:
        sweep_state['total'] = len(names)

    def run():
        # Chunked submission: cancel takes effect within one chunk
        # (ex.map on the full list would eagerly submit every query)
        CHUNK = 50
        for i0 in range(0, len(names), CHUNK):
            if sweep_state['cancel']:
                break
            with ThreadPoolExecutor(max_workers=4) as ex:
                for _ in ex.map(_worker, names[i0:i0 + CHUNK]):
                    with _lock:
                        sweep_state['done'] += 1
        sweep_state.update(
            active=False,
            finished_at=datetime.now(timezone.utc).isoformat(
                timespec='seconds'))

    threading.Thread(target=run, daemon=True).start()
    return True, len(names)
