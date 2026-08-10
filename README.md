# Backyard SETI

Amateur analysis of Breakthrough Listen open radio telescope data for SETI signal detection.

## Overview

This project works with the Breakthrough Listen (BL) open data archive to search for narrowband signals of potential intelligent origin. We use Berkeley's open-source tools (blimpy, turbo_seti) alongside custom analysis pipelines for:

- **Targeted star searches** - Doppler drift analysis on specific stars (Proxima Centauri, TRAPPIST-1, etc.)
- **RFI rejection** - On/off cadence comparison to reject terrestrial interference
- **Anomaly detection** - Spectral outlier identification in archived observations
- **Bulk analysis** - Systematic processing of archived data for unusual signals

## Tools

| Tool | Purpose |
|------|---------|
| [blimpy](https://github.com/UCBerkeleySETI/blimpy) | Load and visualize filterbank/HDF5 files |
| [turbo_seti](https://github.com/UCBerkeleySETI/turbo_seti) | Narrowband Doppler drift search |
| Custom scripts | RFI rejection, batch processing, visualization |
| SQLite | Indexed hit storage, cross-epoch search, result caching |

## Data Sources

- **Portal:** [Breakthrough Listen Open Data Archive](https://breakthroughinitiatives.org/opendatasearch)
- **Formats:** Filterbank (.fil) and HDF5 (.h5)
- **Telescopes:** GBT (Green Bank, WV), Parkes (Australia), FAST (China)
- **Sizes:** Typically 1-10 GB per observation file

## Project Structure

```
backyard-seti/
├── src/              # Analysis scripts + SQLite database layer (db.py)
├── data/             # Downloaded BL files + seti_hits.db (gitignored)
├── results/          # Scan outputs, barycentric corrections, cross-epoch cache
├── dashboard/        # Flask web UI (app.py, templates, static JS/CSS)
├── docs/             # Documentation and notes
├── README.md
└── .gitignore
```

## Data Storage

**SQLite is the primary data store for hit data.** The database at
`data/seti_hits.db` contains all turboSETI hits with indexes on scan_id,
SNR, ON/OFF, and barycentric frequency. Dashboard API endpoints query
SQLite directly for millisecond response times.

**Rule: use SQLite wherever possible.** JSON files are kept for small
metadata (scan_meta.json) and as a fallback, but hit data and cross-epoch
results belong in the database. See `docs/sqlite_schema.md` for schema.

## Setup

### Prerequisites

- Python 3.11 (not 3.14, incompatible with blimpy)
- git

### Install

```bash
# Clone this repo
git clone https://github.com/w4gon79/backyard-seti.git
cd backyard-seti

# Install Berkeley SETI tools
pip install blimpy turbo_seti
```

### Python Path

On this machine, use Python 3.11 explicitly:
```
C:\Users\w4gon\AppData\Local\Programs\Python\Python311\python.exe
```

## Usage

### Database

The SQLite database (`data/seti_hits.db`) is the central data store:

```bash
# Initialize (creates schema if needed)
python src/db.py init

# Import existing JSON scan results into SQLite
python src/migrate_to_sqlite.py

# Query hits
python src/db.py hits --scan-id PROXCEN_2026-08-08_2333 --min-snr 10 --on-off ON
```

### Dashboard

```bash
cd dashboard
python app.py
# Open http://localhost:8070
```

The dashboard provides:
- Scan management and pipeline control
- Hit browsing with pagination and SNR filtering
- Barycentric correction with cross-epoch candidate search
- Mission Control real-time scan monitoring

### Scanning

```bash
# Download BL data
python src/download_proxima.py

# Run fine-res pipeline (SNR 5, 262k sub-bands)
python src/fine_res_pipeline.py data/fine/Parkes_58020_21048_PROXCEN_S_fine.h5
```

## License

MIT

## Links

- [Breakthrough Listen Open Data](https://breakthroughinitiatives.org/opendatasearch)
- [Berkeley SETI Research Center](https://seti.berkeley.edu)
- [blimpy documentation](https://blimpy.readthedocs.io)
- [turbo_seti documentation](https://turbo-seti.readthedocs.io)
