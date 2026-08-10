# Backyard SETI

Amateur analysis of Breakthrough Listen open radio telescope data for SETI signal detection.

## Overview

This project works with the Breakthrough Listen (BL) open data archive to search for narrowband signals of potential intelligent origin. We use Berkeley's open-source tools (blimpy, turbo_seti) alongside custom analysis pipelines for:

- **Targeted star searches** - Doppler drift analysis on specific stars (Proxima Centauri, TRAPPIST-1, etc.)
- **RFI rejection** - On/off cadence comparison to reject terrestrial interference
- **Cross-epoch correlation** - Barycentric frequency matching across multiple observation epochs
- **Incoherent stacking** - Power spectrum averaging across epochs to detect sub-threshold signals
- **Anomaly detection** - Spectral outlier identification in archived observations
- **Bulk analysis** - Systematic processing of archived data for unusual signals

## Tools

| Tool | Purpose |
|------|---------|
| [blimpy](https://github.com/UCBerkeleySETI/blimpy) | Load and visualize filterbank/HDF5 files |
| [turbo_seti](https://github.com/UCBerkeleySETI/turbo_seti) | Narrowband Doppler drift search |
| [astropy](https://www.astropy.org/) | Barycentric velocity correction |
| Custom scripts | RFI rejection, batch processing, cross-epoch search, incoherent stacking |
| SQLite | Indexed hit storage, cross-epoch search, stack job persistence, result caching |
| Flask | Dashboard web UI |

## Data Sources

- **Portal:** [Breakthrough Listen Open Data Archive](https://breakthroughinitiatives.org/opendatasearch)
- **Formats:** HDF5 (.h5) fine-res and mid-res
- **Telescopes:** Parkes (Australia), GBT (Green Bank, WV)
- **Sizes:** ~12 GB per fine-res file, ~233 MB per mid-res file
- **Primary target:** Proxima Centauri (PROXCEN), 20 epochs available (Jan-Oct 2017)

## Project Structure

```
backyard-seti/
├── src/                    # Analysis scripts + SQLite database layer (db.py)
│   ├── db.py               # SQLite schema, hit CRUD, cross-epoch, stack jobs
│   ├── barycentric_correct.py  # Barycentric velocity correction + cross-epoch matching
│   ├── fine_res_pipeline.py    # turboSETI batch runner with sub-band chunking
│   ├── bl_search.py        # BL API search/download
│   └── download_proxima.py # Batch downloader for PROXCEN cadences
├── data/                   # Downloaded BL files + seti_hits.db (gitignored)
│   ├── fine/               # Fine-res HDF5 files (12 GB each)
│   ├── stack_results/      # Incoherent stack output (plots, JSON, SQLite)
│   └── seti_hits.db        # SQLite: hits, scans, cross_epoch_results, stack_jobs
├── results/                # Scan outputs, barycentric corrections, cross-epoch cache
├── dashboard/              # Flask web UI
│   ├── app.py              # Backend: scan control, hit API, cross-epoch, stack endpoints
│   ├── templates/          # index.html (main), stack.html (incoherent stack), mission.html
│   ├── static/js/          # dashboard.js, stack.js, mission.js, skymap.js
│   └── static/css/         # style.css, stack.css, mission.css
├── incoherent_stack.py     # Phase 2C: incoherent power spectrum stacking
├── docs/                   # Documentation and roadmap
│   └── ROADMAP.md          # Full project roadmap with phase progression
├── README.md
└── .gitignore
```

## Pipeline Architecture

### Phase 1: Pipeline Validation (COMPLETE)

1. **Signal Injection and Recovery** - Verified turboSETI detects synthetic signals
   on fine-res data. Confirmed mid-res format is unusable for narrowband SETI
   (166 Hz/s drift resolution). Test scripts in `src/test_*.py`.

2. **RFI Environment Characterization** - ON/OFF cadence rejection identifies
   terrestrial interference. RFI present in both ON and OFF frames at the same
   observed frequency is rejected. Only ON-only signals pass through.

### Phase 2: Multi-Observation Cross-Correlation

3. **Barycentric Correction** (COMPLETE) - `src/barycentric_correct.py` uses astropy
   to compute Earth's line-of-sight velocity to the target at each observation time,
   then corrects observed frequencies to the solar-system-barycenter frame. A real
   signal from a distant source appears at the same barycentric frequency in every
   epoch. RFI does not.

4. **Cross-Epoch Search** (COMPLETE) - SQL-based frequency bucketing on indexed
   barycentric_freq column. Finds signals present in ON frames across multiple
   epochs but absent from OFF frames. SNR post-filter (`--min-snr`) allows
   retroactive thresholding without re-scanning. Results cached in
   `cross_epoch_results` table.

5. **Incoherent Stack** (COMPLETE) - `incoherent_stack.py` averages power spectra
   across epochs after ON/OFF subtraction and barycentric correction. Noise
   averages down as sqrt(N), persistent signals average up linearly. With 4
   epochs, SNR improvement is 2x. With 20 epochs, SNR 1.5 becomes SNR ~6.7.
   Dashboard page at `/stack` with background job runner, progress tracking,
   peak detection, and waterfall inspection.

### Phase 3: ML Signal Classification (PLANNED)

6. **Training Set Generation** - Harvest RFI examples from OFF frames, generate
   synthetic signal injections at various SNR/drift/bandwidth.
7. **CNN Classifier** - Waterfall tile classification (noise, RFI, candidate).
8. **Unsupervised Anomaly Detection** - UMAP/t-SNE dimensionality reduction +
   DBSCAN clustering to find signals that don't fit any known category.

### Phase 4: Automated Target Survey (PLANNED)

9. **Multi-target scaling** - BL priority targets, TESS exoplanets, anomalous stars.
10. **Fully automated pipeline** - Download -> scan -> reject -> correct -> cross-epoch -> stack -> classify -> report.

See `docs/ROADMAP.md` for the full approved roadmap.

## Data Storage

**SQLite is the primary data store.** The database at `data/seti_hits.db`
contains four main tables:

- **hits** - All turboSETI hits with indexes on scan_id, SNR, ON/OFF, and
  barycentric frequency. Supports millisecond queries across millions of rows.
- **scans** - Scan metadata (target, MJD, status, barycentric correction state).
- **cross_epoch_results** - Cached cross-epoch search results.
- **stack_jobs** - Incoherent stack job history (params, status, peaks, plot path).

**Secondary data storage:** Large HDF5 files are stored on a secondary drive
(`D:\seti_data\fine`, configured via `.env` SETI_DATA_SECONDARY). The dashboard
and stacking pipeline check both primary and secondary locations automatically.

## Dashboard

The Flask dashboard at `http://localhost:8070` provides:

- **Main page** (`/`) - Sky map, target search, scan controls, hit browser with
  waterfall inspection, ON/OFF rejection, barycentric correction, cross-epoch search
- **Incoherent Stack** (`/stack`) - Phase 2C power spectrum stacking with
  frequency window selection, epoch multi-select, background job runner, progress
  tracking, peak detection table, and waterfall follow-up
- **Mission Control** (`/mission`) - Real-time scan monitoring with hit ticker

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
pip install blimpy turbo_seti astropy

# For the dashboard
pip install flask plotly numpy

# For incoherent stacking
pip install matplotlib scipy
```

### Python Path

On this machine, use Python 3.11 explicitly:
```
C:\Users\w4gon\AppData\Local\Programs\Python\Python311\python.exe
```

### Environment

Create a `.env` file in the project root:
```
SETI_DATA_SECONDARY=D:\seti_data\fine
```

## Usage

### Database

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
python dashboard/app.py
# Open http://localhost:8070
```

### Scanning

```bash
# Download BL data
python src/download_proxima.py

# Run fine-res pipeline (SNR 5, 262k sub-bands)
python src/fine_res_pipeline.py data/fine/Parkes_58020_21048_PROXCEN_S_fine.h5
```

### Barycentric Correction

```bash
# Correct a single scan
python src/barycentric_correct.py scan results/PROXCEN_2026-08-08_2333 --target PROXCEN

# Cross-epoch search across multiple scans
python src/barycentric_correct.py cross-epoch \
  results/PROXCEN_2026-08-07_1911 results/PROXCEN_2026-08-08_2333 \
  --min-snr 10 --min-epochs 2
```

### Incoherent Stacking

```bash
# CLI (targeted window)
python incoherent_stack.py --target PROXCEN --freq-center 3000 --width 10 --plot

# Dashboard (interactive)
# Open http://localhost:8070/stack
```

## Proxima Centauri Testbed

Proxima Centauri is the primary testbed for all pipeline phases. The BL open
data catalog contains 20 complete 6-file fine-res cadence sets for PROXCEN,
spanning January to October 2017. Each cadence consists of 3 ON/OFF pairs
(S/R) observed minutes apart.

**Processing cost:** ~21 hours per 6-file cadence at 262,144 sub-band width.
Full 20-epoch suite = ~22 days continuous. Batch queue with resume capability
handles unattended processing.

**Stacking sensitivity:** With 20 epochs, a signal at SNR 1.5 in individual
spectra becomes SNR ~6.7 in the incoherent stack. This is the sensitivity
that could pull a faint narrowband beacon out of the noise floor.

## Current Status (2026-08-10)

- **Epochs scanned:** 3 complete (57791, 57846, 58020), 1 in progress (57930)
- **Total hits in DB:** 300K+ across all scans
- **Cross-epoch search:** Operational, validated with 2-epoch test (0 candidates
  at SNR 10+, as expected for a quiet target with only 2 epochs)
- **Incoherent stack:** Prototype validated on 10 MHz window with 4 epochs.
  59 peaks detected at 5-sigma. Noise reduction matched theoretical sqrt(N).
  Dashboard integration complete with background job runner.
- **Next:** Scan remaining epochs, run full-band incoherent stack, begin
  Phase 3 ML training set generation.

## License

MIT

## Links

- [Breakthrough Listen Open Data](https://breakthroughinitiatives.org/opendatasearch)
- [Berkeley SETI Research Center](https://seti.berkeley.edu)
- [blimpy documentation](https://blimpy.readthedocs.io)
- [turbo_seti documentation](https://turbo-seti.readthedocs.io)
- [Project Roadmap](docs/ROADMAP.md)