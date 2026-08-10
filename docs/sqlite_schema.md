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

## Migration plan
1. Create the DB and schema
2. Write import script that reads existing JSON files and populates the DB
3. Modify barycentric_correct.py to read/write from DB instead of JSON
4. Modify dashboard/app.py endpoints to query DB
5. Keep JSON as fallback/export format