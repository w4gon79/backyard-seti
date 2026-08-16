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
- **Primary targets:** Any BL target with fine-res cadence data, discovered via the BL Catalog (PROXCEN scanned; survey shortlist building). Proxima Centauri remains the validation testbed with 6 epochs archived.

## Project Structure

```
backyard-seti/
├── src/                    # Analysis scripts + SQLite database layer (db.py)
│   ├── db.py               # SQLite schema, hit CRUD, cross-epoch, stack jobs
│   ├── barycentric_correct.py  # Barycentric velocity correction + cross-epoch matching
│   ├── fine_res_pipeline.py    # turboSETI batch runner with sub-band chunking
│   ├── target_registry.py     # Phase 3A: SQLite target registry (SIMBAD, BL availability)
│   ├── bl_catalog.py          # BL catalog sweep/cache (open browse of every target)
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

### Phase 3: Multi-Target Survey (COMPLETE 2026-08-15, 3F pending)

The pipeline is target-agnostic end to end. The SQLite target registry is the
single source of truth for target identity, coordinates, and selection:

6. **Target Registry (3A)** - `src/target_registry.py`: SQLite `targets` table
   with SIMBAD-resolved coordinates, BL availability (variant + cross-ID query
   cascade: KIC_8462852, GJ699, GJ71...), aliases, priority. Dashboard panel
   for add/list/delete. Legacy TARGET_COORDS dict deleted; registry-only.
7. **Per-Target Data Organization (3B)** - SSD staging (`data/fine`, fast
   scans) + per-target archive (`D:\seti_data\{TARGET}\fine`). Discovery
   cascades staging -> archive -> legacy. "Archive Epoch to D:" button moves
   scanned epochs with size verification.
8. **Dynamic Epoch Discovery (3C)** - Epochs auto-discovered from filenames
   (Parkes + GBT grammars) with cadence validation (3 ON + 3 OFF flags) and
   per-epoch scan status cross-referenced from the scans table.
9. **Generalized Naming (3D)** - Scan ids born `TARGET_MJD_DATE_TIME`;
   epoch labels on every scan surface.
10. **Dashboard Multi-Target UI (3E-lite)** - Registry-driven dropdowns
    everywhere (stack, two-layer, barycentric), registry sky view colored by
    scan status, BL Catalog browser for survey planning.
11. **Automated Pipeline Per Target (3F)** - ON HOLD, design pending. One-click
    download -> scan -> reject -> correct -> cross-epoch -> stack -> report.

### Phase 4: ML Signal Classification (DEPRIORITIZED)

Two-layer pipeline with Layer 2.5 RFI scorecard handles current classification
needs. CNN classifier and unsupervised anomaly detection remain future work.

See `docs/ROADMAP.md` for the full approved roadmap.

## Data Storage

**SQLite is the primary data store.** The database at `data/seti_hits.db`
contains four main tables:

- **hits** - All turboSETI hits with indexes on scan_id, SNR, ON/OFF, and
  barycentric frequency. Supports millisecond queries across millions of rows.
- **scans** - Scan metadata (target, MJD, status, barycentric correction state).
- **cross_epoch_results** - Cached cross-epoch search results.
- **stack_jobs** - Incoherent stack job history (params, status, peaks, plot path).
- **targets** - Phase 3A target registry: names, SIMBAD/manual coordinates,
  aliases, BL fine-res availability (files, epochs, winning query form), priority.
  The single source of truth for target coordinates; no static fallbacks remain.
- **bl_catalog** - Cached BL-catalog sweep results: per-target fine/mid/time file
  counts, fine epochs, ON/OFF cadence, bytes, coordinates, telescopes.

**Secondary data storage:** Two-tier layout. `G:\seti\data\fine` is SSD
staging: downloads land here and scans run at full speed; it drains after each
epoch via the dashboard's Archive button. `D:\seti_data\{TARGET}\fine` is the
per-target archive (e.g. 36 PROXCEN fine files, ~405 GB). All discovery paths
check staging, the per-target archive, and the legacy flat dir automatically.

## Dashboard

The Flask dashboard at `http://localhost:8070` provides:

- **Main page** (`/`) - Sky map, target search, scan controls, hit browser with
  waterfall inspection, ON/OFF rejection, barycentric correction, cross-epoch search
- **Incoherent Stack** (`/stack`) - Phase 2C power spectrum stacking with
  frequency window selection, epoch multi-select, background job runner, progress
  tracking, peak detection table, and waterfall follow-up. Registry-driven target
  selection; epochs show cadence validation + scan status.
- **Target Registry + BL Catalog** (main page) - Add targets (SIMBAD-resolved,
  BL availability checked via variant/cross-ID cascade), browse every BL target's
  fine-res availability (one-time background sweep, cached in `bl_catalog`),
  filter by epochs/ON-OFF cadence, click rows onto the sky map, one-click add to
  registry.
- **Mission Control** (`/mission`) - Real-time scan monitoring with a rolling
  hit ticker.
- **Mission Control** (`/mission`) - Real-time scan monitoring with hit ticker

## Screenshots

**Main dashboard** - sky map, target registry, and scan controls:

![Main dashboard](docs/screenshots/SETI_Dashboard.png)

**Mission Control** - live scan progress with the rolling hit ticker:

![Mission Control](docs/screenshots/SETI_Mission_Control.png)

**Incoherent stack waterfall** - six epochs of Proxima Centauri averaged
after barycentric correction:

![Incoherent stack](docs/screenshots/SETI_Stack.png)

## Setup

### Prerequisites

- Python 3.11. The Berkeley tools (blimpy, turbo_seti) do not support newer
  Python releases yet. If your system default is 3.12+, install 3.11 alongside
  it and run every command below with 3.11 explicitly (e.g. `py -3.11` on
  Windows, `python3.11` on Linux).
- git

### Quick Start: clone to running dashboard

Four steps, in order. Running the dashboard is all you need; the CLI commands
further down are optional extras.

**Step 1: Clone the repository**

```bash
git clone https://github.com/w4gon79/backyard-seti.git
cd backyard-seti
```

**Step 2: Install the dependencies**

```bash
pip install -r requirements.txt
```

One command pulls in everything: the Berkeley SETI tools (blimpy, turbo_seti,
astropy), the dashboard stack (Flask, numpy, python-dotenv), HDF5 I/O (h5py,
hdf5plugin), and the stacking tools (matplotlib, scipy). This can take a few
minutes; turbo_seti compiles Cython extensions.

**Step 3 (optional): Configure a second drive for data files**

BL fine-res files are roughly 12 GB each. If you plan to collect several and
would rather keep them on a second drive, create a `.env` file in the project
root:

```
SETI_DATA_SECONDARY=D:\seti_data\fine
```

Skip this to start. The dashboard defaults to storing downloads under `data/`
inside the project, and everything works without the file.

**Step 4: Start the dashboard**

```bash
python dashboard/app.py
```

Then open **http://localhost:8070** in your browser. That is the whole setup.
The SQLite database and its schema are created automatically on first launch;
there is no manual init step.

From the dashboard you can search the Breakthrough Listen archive, add targets
(coordinates resolved automatically via SIMBAD), download data, run scans with
live progress, reject RFI, apply barycentric correction, run cross-epoch
search, and view incoherent stacks. The stack UI lives at
`http://localhost:8070/stack` and live scan monitoring at
`http://localhost:8070/mission`.

## CLI Reference (optional)

**None of these commands are required.** The dashboard from Step 4 covers the
entire workflow interactively. These exist for scripting, automation, and
completeness:

```bash
# Query hits directly from the SQLite database
python src/db.py hits --scan-id PROXCEN_2026-08-08_2333 --min-snr 10 --on-off ON

# Download BL data for Proxima cadences
python src/download_proxima.py

# Run the fine-res pipeline on a single file (SNR 5, 262k sub-bands)
python src/fine_res_pipeline.py data/fine/Parkes_58020_21048_PROXCEN_S_fine.h5

# Barycentric-correct a scan directory
python src/barycentric_correct.py scan results/PROXCEN_2026-08-08_2333 --target PROXCEN

# Cross-epoch search across multiple corrected scans
python src/barycentric_correct.py cross-epoch \
  results/PROXCEN_2026-08-07_1911 results/PROXCEN_2026-08-08_2333 \
  --min-snr 10 --min-epochs 2

# Incoherent stack, targeted window, with plot
python incoherent_stack.py --target PROXCEN --freq-center 3000 --width 10 --plot
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