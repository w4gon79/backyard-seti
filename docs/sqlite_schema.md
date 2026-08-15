# SETI SQLite Schema Design

## Database: `G:\seti\data\seti_hits.db` (single shared file)

## Tables

### `scans` (scan metadata, one row per scan)
```sql
CREATE TABLE scans (
    scan_id         TEXT PRIMARY KEY,      -- e.g. "PROXCEN_2026-08-08_2333"
    target          TEXT,                   -- "PROXCEN"
    timestamp       TEXT,                   -- "2026-08-08T23:33:08"
    status          TEXT,                   -- "complete"
    mjd_start       REAL,                   -- from first file
    sub_band_chans  INTEGER,
    overlap         INTEGER,
    max_drift       REAL,
    snr_threshold   REAL,                   -- SNR threshold used in turboSETI
    f_start         REAL,
    f_stop          REAL,
    total_hits      INTEGER,
    on_hits         INTEGER,
    off_hits        INTEGER,
    duration_s      REAL,
    bary_corrected  INTEGER DEFAULT 0,      -- 0=no, 1=yes
    bary_velocity   REAL,                   -- m/s
    bary_mjd        REAL,
    ra_hours        REAL,
    dec_deg         REAL,
    telescope       TEXT,
    created_at      TEXT DEFAULT (datetime('now'))
);
```

### `hits` (the big one, one row per detection)
```sql
CREATE TABLE hits (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id         TEXT NOT NULL,
    source_file     TEXT,                   -- "Parkes_58020_21048_PROXCEN_S_fine.h5"
    on_off          TEXT,                   -- "ON" or "OFF"
    freq            REAL,                   -- observed frequency MHz
    barycentric_freq REAL,                  -- barycentric-corrected freq MHz (NULL if not corrected)
    drift_rate      REAL,                   -- Hz/s
    snr             REAL,
    channel         INTEGER,
    sub_band        INTEGER,
    mjd             REAL,                   -- MJD of observation
    FOREIGN KEY (scan_id) REFERENCES scans(scan_id)
);

-- Critical indexes for performance
CREATE INDEX idx_hits_scan ON hits(scan_id);
CREATE INDEX idx_hits_snr ON hits(snr);
CREATE INDEX idx_hits_on_off ON hits(on_off);
CREATE INDEX idx_hits_scan_snr ON hits(scan_id, snr);
CREATE INDEX idx_hits_scan_onoff_snr ON hits(scan_id, on_off, snr);
CREATE INDEX idx_hits_bary ON hits(barycentric_freq);
CREATE INDEX idx_hits_scan_bary ON hits(scan_id, barycentric_freq);
```

### `cross_epoch_results` (cached cross-epoch search results)
```sql
CREATE TABLE cross_epoch_results (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_ids        TEXT,                   -- JSON array of scan_ids
    min_snr         REAL,
    tolerance_hz    REAL,
    min_epochs      INTEGER,
    candidate_count INTEGER,
    result_json     TEXT,                   -- full result as JSON
    created_at      TEXT DEFAULT (datetime('now'))
);
```

## Status: LIVE (2026-08-10)

All implemented and deployed. Database is the primary data store.

- **DB path:** `G:\seti\data\seti_hits.db` (506 MB, WAL mode)
- **Migration:** `src/migrate_to_sqlite.py` (idempotent, already run)
- **DB layer:** `src/db.py` (all CRUD + query functions)
- **Dashboard:** 8 new `/api/db/` Flask endpoints, JS uses SQLite with JSON fallback
- **1,361,383 hits** across 3 scans, 7 indexes, millisecond query times

**Rule: use SQLite wherever possible.** JSON kept for small metadata only
(scan_meta.json, cross-epoch cache files). Hit data and search results
belong in the database.

## Performance Comparison

| Operation | Before (JSON) | After (SQLite) | Speedup |
|-----------|-------------|----------------|--------|
| Scan list | 10-30s | <100ms | 100x+ |
| Hit query (SNR>=10) | 5-10s | 2ms | 2500x |
| Cross-epoch (SNR=10) | 9s | 0.25s | 36x |
| Paginated hits | N/A | instant | new |

## New Tables (2026-08-15, Phase 3A + BL Catalog)

### `targets` (target registry, single coordinate authority)
```sql
CREATE TABLE targets (
    name            TEXT PRIMARY KEY,   -- canonical UPPER_UNDERSCORE
    display_name    TEXT,
    aliases         TEXT DEFAULT '[]',   -- JSON list
    ra_hours        REAL,                -- manual or SIMBAD-resolved
    dec_deg         REAL,
    coord_source    TEXT,                -- 'seed'|'simbad'|'manual'
    bl_fine_files   INTEGER,             -- BL fine-res availability
    bl_fine_epochs  INTEGER,
    bl_query_name   TEXT,                -- winning BL query form (e.g. GJ699)
    bl_total_files  INTEGER,
    bl_checked_at   TEXT,
    priority        INTEGER DEFAULT 0,
    notes           TEXT,
    created_at      TEXT DEFAULT (datetime('now'))
);
```

`resolve_target_coords()` in barycentric_correct.py resolves manual coords >
this table; unknown targets return None (no static fallback; the legacy
TARGET_COORDS dict was deleted 2026-08-15).

### `bl_catalog` (cached open-browse sweep of every BL target)
```sql
CREATE TABLE bl_catalog (
    target       TEXT PRIMARY KEY,   -- base BL target name (_S/_R collapsed)
    n_files      INTEGER,
    n_fine       INTEGER,
    n_mid        INTEGER,
    n_time       INTEGER,
    fine_epochs  INTEGER,            -- unique MJDs among fine files
    fine_on      INTEGER,            -- _S count (Parkes grammar)
    fine_off     INTEGER,            -- _R count
    fine_bytes   INTEGER,            -- sum of BL 'size' fields
    total_bytes  INTEGER,
    telescopes   TEXT,               -- CSV
    ra_hours     REAL,               -- from BL file metadata (ON fine preferred)
    dec          REAL,
    swept_at     TEXT
);
```

Populated by `src/bl_catalog.py`'s background sweep (4 workers, resumable,
cancel-effective, modes: resume/all/refresh/fine). ~11.7k base names at
~35/min. The dashboard's BL Catalog browses this table with epoch/cadence
filters for survey target selection.