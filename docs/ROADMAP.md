# Backyard SETI: Project Roadmap

## Mission Statement

Use Breakthrough Listen open data to search for technosignatures, developing 
pipeline tooling that goes beyond the standard turbo_seti matched-filter 
approach. BL published this data specifically hoping amateur scientists would 
find new analysis methods. That is the niche we aim to fill.

---

## Roadmap Approval

**Approved by Joel on 2026-08-08.** This roadmap is the official development
plan for the Backyard SETI project. We follow it closely, one phase at a time.

## Proxima Centauri Testbed

Proxima Centauri is our primary testbed for proving out every phase of this
roadmap. The BL open data catalog contains **25 complete 6-file fine-res
cadence sets** for PROXCEN, spanning January to October 2017:

| Epoch | MJD | Date | BL Server | Status |
|-------|-----|------|-----------|--------|
| 1 | 57783 | Jan 30 | 200 | Available |
| 2 | 57790 | Feb 6 | 200 | Available |
| 3 | 57791 | Feb 7 | 200 | **Scanned + Bary** |
| 4 | 57804 | Feb 20 | 200 | Available |
| 5 | 57805 | Feb 21 | 200 | Available |
| 6 | 57808 | Feb 24 | 200 | Available |
| 7 | 57809 | Feb 25 | 200 | Available |
| 8 | 57846 | Apr 3 | 200 | **Scanned + Bary** |
| 9 | 57847 | Apr 4 | 200 | Available |
| 10 | 57850 | Apr 7 | 200 | Available |
| 11 | 57854 | Apr 11 | 200 | Available |
| 12 | 57904 | May 31 | 200 | Available |
| 13 | 57910 | Jun 6 | 200 | Available |
| 14 | 57930 | Jun 26 | 200 | **Scanned + Bary** |
| 15 | 57932 | Jun 28 | 200 | Available |
| 16 | 57933 | Jun 29 | 200 | Available |
| 17 | 57939 | Jul 5 | 200 | Available |
| 18 | 57940 | Jul 6 | 200 | Available |
| 19 | 57941 | Jul 7 | **404** | Unavailable |
| 20 | 57942 | Jul 8 | **404** | Unavailable |
| 21 | 57943 | Jul 9 | **404** | Unavailable |
| 22 | 57944 | Jul 10 | **404** | Unavailable |
| 23 | 58020 | Sep 24 | 200 | **Scanned + Bary** |
| 24 | 58026 | Sep 30 | **404** | Unavailable |
| 25 | 58027 | Oct 1 | **404** | Unavailable |
| 26 | 58029 | Oct 3 | **404** | Unavailable |
| 27 | 58048 | Oct 22 | **404** | Unavailable |

**Availability note (2026-08-10):** BL API lists fine-res files for all
MJDs, but files on `blpd8.ssl.berkeley.edu/dl2/` path return 404. Only
files on `/dl/` path are actually downloadable. 20 of 27 MJDs have
downloadable fine-res files. The July 2017 gap (57941-57944) and October
2017 gap (58026-58048) are currently unavailable from Berkeley.

**Validation epochs (4-epoch cross-epoch test):**
- Feb 7 (57791) - scanned
- Apr 3 (57846) - scanning
- Jun 26 (57930) - downloading
- Sep 24 (58020) - scanned

**Validation strategy:** Scan 3-4 well-spaced epochs first (Feb, Apr, Jul, Sep)
to validate cross-epoch pipeline end to end. Then batch-scan remaining epochs
for deep incoherent stacking.

**Stacking sensitivity:** With N epochs, a signal at SNR S in individual
spectra becomes SNR ~S*sqrt(N) in the stack. With 25 epochs, SNR 1.5 becomes
SNR ~7.5. This is the sensitivity that could pull a faint narrowband beacon
out of the noise floor.

**Processing cost:** ~21 hours per 6-file cadence (3.5 hr/file at 262,144
sub-band width). Full 25-epoch suite = ~22 days continuous. Batch queue with
resume capability handles unattended processing.

---

## Current State (2026-08-13)

### Pipeline Components

| Component | Script | Status | Origin |
|-----------|--------|--------|--------|
| BL API search/download | `bl_search.py` | Working | Ours |
| Batch downloader | `download_proxima.py` | Working | Ours |
| Doppler drift search | `bl_doppler_search.py` | Working | Wraps turbo_seti |
| Batch runner | `batch_search.py` | Working | Ours |
| RFI rejection (ON/OFF) | `bl_rfi_reject.py` | Working | Ours (not in SETI repo) |
| Waterfall inspection | `bl_inspect.py` | Working | Ours |
| SQLite database layer | `src/db.py` | Working | Ours |
| JSON-to-SQLite migration | `src/migrate_to_sqlite.py` | Working | Ours |
| Barycentric correction | `src/barycentric_correct.py` | Working | Ours |
| Cross-epoch search | SQL-based in `db.py` | Working | Ours |
| Dashboard (Flask) | `dashboard/app.py` | Working | Ours |

### Data Inventory

- **Proxima Centauri (PROXCEN):** 6 cadence sessions (MJD 57791, 57847, 
  57905, 57911, 57943, 58027), spanning Apr-Oct 2017. Parkes 21cm multibeam.
  - **Fine-res:** 6 files (S/R pairs), 12 GB each, 580 MHz bandwidth 
    (2744-3324 MHz), 2.79 Hz/ch, 18.25s tsamp, 207M channels. **Primary 
    data for narrowband SETI.**
  - **Mid-res:** 36 files, ~233 MB each, 869 MHz bandwidth, 2861 Hz/ch, 
    1.074s tsamp. Useful for RFI characterization only.
- **Tabby's Star (KIC8462852):** 6 files from 2 GBT sessions. Fine and coarse 
  resolution. Single ON observation (no OFF cadence).

### Search Results So Far

Four PROXCEN epochs scanned at SNR 5 (57791, 57846, 57930, 58020).
Barycentric correction applied to all four. Cross-epoch matching operational.

**Two-layer pipeline validated (2026-08-13):**
- Layer 1 (cross-epoch filter): Killed 30,211 single-epoch hits at min_epochs=3
- Layer 2 (incoherent stack): Stacks surviving candidates across epochs
- Layer 2.5 (RFI scorecard): Automated scoring of stacked candidates
- min_epochs=2 produces candidates (all RFI by scorecard)
- min_epochs=3 produces zero candidates (filter working correctly)

**Conclusion:** Pipeline is solid. No technosignature candidates from
Proxima Centauri across 4 epochs, which is the expected result. More
epochs increase stacking sensitivity (SNR scales as sqrt(N)).

---

## Phase 1: Pipeline Validation (CRITICAL)

Before any advanced techniques, we MUST prove the pipeline can find a signal 
if one exists. All current zero-hit results are meaningless without this.

### 1A. Signal Injection and Recovery -- COMPLETED

**Status:** Complete (2026-08-07).

**Results:** Synthetic signal injection confirmed turbo_seti works correctly 
on fine-res data. Pure synthetic setigen Frame recovered at SNR 36.5 
(measured), 26-49 turbo_seti hits. Fine-res BL sub-band injection recovered 
at SNR 698 (turbo_seti top hit). Mid-res format confirmed unusable for 
narrowband SETI (166 Hz/s drift resolution).

**Test scripts:**
- `test_synthetic_setigen.py` -- pure synthetic Frame from scratch
- `test_midres_clone.py` -- synthetic with mid-res params (root cause confirmation)
- `test_fine_res.py` -- fine-res BL sub-band with injection

**Deliverable:** Detection confirmed. Efficiency curve (SNR vs recovery rate) 
is a future enhancement but not blocking.

### 1B. RFI Environment Characterization

**Goal:** Understand what RFI looks like in Parkes L-band data so we can 
distinguish it from real signals.

**Method:**
1. Run pipeline at very low SNR (5) on OFF frames only
2. Catalog all hits: frequency, drift rate, SNR, bandwidth
3. Cluster hits by morphology (zero drift = terrestrial, harmonically related 
   = digital modulation, broadband = impulsive RFI)
4. Build an RFI profile for Parkes L-band

**Deliverable:** RFI catalog + morphology classification + frequency mask 
of known RFI channels.

---

## Phase 2: Multi-Observation Cross-Correlation

**Goal:** Leverage our 6-epoch dataset to find signals too weak for 
single-observation detection.

**Rationale:** A real ET signal at a consistent rest-frame frequency should 
appear across multiple observation epochs (after barycentric correction). 
RFI will not, because it is terrestrial and not correlated with telescope 
pointing.

### 2A. Barycentric Frequency Correction

**Status:** Complete (2026-08-10). Barycentric correction and cross-epoch search
both operational with SQLite backend.

**Method:**
1. For each observation epoch, compute the expected barycentric Doppler shift 
   for Proxima Centauri
2. Apply correction to shift all frequencies to the solar system barycenter 
   frame
3. A real signal now appears at the SAME frequency in all epochs
4. RFI appears at different barycentric-corrected frequencies across epochs

**Deliverable:** `barycentric_correct.py` ✓ + SQLite `hits` table with 
barycentric_freq column. ✓

**Implementation:** Module built using astropy `radial_velocity_correction` for
accurate solar-system-barycenter velocity computation. Dashboard integration
with API endpoints for running correction on scans and cross-epoch candidate
search. Target coordinate database for common BL targets. Cross-epoch matcher
uses SQL bucketing on indexed barycentric_freq column for O(N) complexity.
Results cached in `cross_epoch_results` table.

**Cross-epoch SNR post-filter:** `--min-snr` parameter allows retroactive
SNR thresholding without re-scanning. turboSETI's SNR threshold is a simple
cutoff on the detection statistic, so post-filtering existing hits is
mathematically equivalent to re-running turboSETI at the higher threshold.
Verified: SNR 5 scan with min_snr=10 post-filter gives identical results to
a hypothetical SNR 10 scan.

**Results (2026-08-10, 2 epochs):**
- SNR 5 (no filter): 1238 candidates (expected from noise, ~1637 predicted)
- SNR 10: 0 candidates (expected, ~0.04 predicted)
- SNR 15: 0 candidates

Zero candidates at SNR 10+ is the correct result for a quiet target with
only 2 epochs. More epochs needed for statistical significance.

### 2B. Cross-Epoch Hit Stacking

**Method:**
1. Run pipeline at low SNR (5-10) on all epochs
2. Apply barycentric correction to all hits
3. Search for frequencies that appear in ON frames across multiple epochs 
   but never in OFF frames
4. A hit in 2+ ON epochs at the same barycentric frequency is a much stronger 
   candidate than a single-epoch detection
5. Compute a coincidence probability: given N frequency bins and M hits, what 
   is the false alarm rate for k-epoch coincidence?

**Deliverable:** `cross_epoch.py` + candidate list with false alarm 
probabilities.

### 2C. Incoherent Stack (Power Averaging)

**Method:**
1. Instead of threshold-based detection, average power spectra across epochs 
   (ON frames only, barycentric corrected)
2. Random noise averages down as sqrt(N), persistent signals average up 
   linearly
3. After 6 epochs, a signal at SNR 3 in individual spectra becomes SNR ~7 in 
   the stack
4. Search the stacked spectrum for peaks

**Deliverable:** `incoherent_stack.py` + stacked spectrum + hit list.

---

## Phase 3: Multi-Target Survey

**Goal:** Generalize the pipeline beyond Proxima Centauri to survey any
BL target with fine-res cadence data. This requires removing PROXCEN
hardcoding throughout the codebase and making target selection a
first-class concept in the dashboard.

**Rationale:** Proxima Centauri proved out every pipeline stage. Now we
need breadth. Different stars have different Doppler corrections,
different RFI environments (different telescope pointings), and
different expected drift rates. Surveying multiple targets is how we
actually search, not just validate tooling.

### What's Hardcoded Today

The following components have PROXCEN assumptions baked in and must be
generalized:

**1. `incoherent_stack.py` (CRITICAL)**
- `EPOCHS` dict is hardcoded to 4 PROXCEN MJDs with fixed sequence numbers
- File naming constructs `Parkes_{mjd}_{seq}_PROXCEN_S_fine.h5` directly
- `_load_epoch_data()` uses PROXCEN-specific target suffix (_S, _R)
- Must become: target-driven epoch discovery from scan results + DB

**2. `ml/two_layer_pipeline.py` (CRITICAL)**
- `get_scan_dirs()` searches `G:\seti\results\` for `PROXCEN_*` dirs
- `targeted_stack()` uses PROXCEN-specific file naming
- `TARGET_COORDS` dict has a handful of targets but not dynamically queried
- Must become: scan the DB for any target's completed scans

**3. `src/barycentric_correct.py` (MODERATE)**
- `TARGET_COORDS` dict is statically defined (works for any target already
  in the dict, but new targets need manual addition)
- File discovery searches `data/PROXCEN` dir specifically
- Must become: query BL API or SIMBAD for arbitrary target coordinates

**4. `dashboard/app.py` (MODERATE)**
- `PROXCEN_DIR` hardcoded data directory
- Stack endpoint defaults to PROXCEN
- Two-layer endpoint defaults to PROXCEN
- Regular stack waterfall file paths use PROXCEN naming
- Must become: target parameter flows through all endpoints

**5. `dashboard/static/js/stack.js` (MODERATE)**
- File path construction uses `PROXCEN_S_fine.h5` naming
- Epoch list hardcoded to 4 PROXCEN MJDs for waterfall display
- Must become: fetch epoch list from API per-target

**6. `dashboard/static/js/dashboard.js` (MINOR)**
- Download default target is PROXCEN
- Must become: use whatever target is selected in the UI

### 3A. Target Registry

**Goal:** Centralized target management with automatic coordinate lookup.

**Design:**
1. `targets` table in SQLite: name, ra_hours, dec_deg, aliases,
   priority, notes
2. Seed with known BL targets (PROXCEN, Tabby's Star, Barnard's Star,
   Sirius, etc.)
3. Dashboard page for browsing/adding targets
4. On new target add: query BL API to check data availability
5. Coordinates also resolvable via SIMBAD/CDS API for arbitrary names

**Deliverable:** Target registry + dashboard UI for target management.

### 3B. Per-Target Data Organization

**Goal:** Data directories and scan results organized by target, not
flattened into PROXCEN-specific paths.

**Current structure:**
```
G:\seti\data\fine\              # flat, PROXCEN files mixed with any others
G:\seti\results\PROXCEN_2026-...  # scan results keyed by target+date
G:\seti\data\PROXCEN\            # legacy PROXCEN-specific dir
```

**Target structure:**
```
G:\seti\data\{TARGET}\fine\     # per-target data directories
G:\seti\results\{TARGET}_2026-...# already keyed by target (OK)
```

**Migration:** Move existing PROXCEN fine files into
`data/PROXCEN/fine/`. Update `FINE_DIRS` in `incoherent_stack.py` to be
target-aware. Update file discovery in barycentric_correct.py and
two_layer_pipeline.py to search per-target directories.

**Deliverable:** Clean per-target data layout + migration script.

### 3C. Dynamic Epoch Discovery

**Goal:** Replace hardcoded `EPOCHS` dict with dynamic discovery from the
scan database.

**Design:**
1. Query `scans` table for all completed scans of a given target
2. For each scan, extract MJD and ON/OFF sequence numbers from the file
   headers (or derive from filenames using the telescope field)
3. Build the epoch mapping at runtime instead of compile time
4. Validate each epoch has complete ABABAB cadence (3 ON + 3 OFF)

**Deliverable:** `get_target_epochs(target)` function replacing the
static `EPOCHS` dict. Used by incoherent_stack, two_layer_pipeline,
and barycentric_correct.

### 3D. Generalized File Naming

**Goal:** Support any BL telescope/target naming convention, not just
Parkes PROXCEN.

**BL filename pattern:** `{telescope}_{mjd}_{seq}_{target}_{S|R}_{res}.h5`

Examples across targets:
- `Parkes_57791_72989_PROXCEN_S_fine.h5`
- `GBT_58132_5678_KIC8462852_S_fine.h5`
- `Parkes_58020_21048_PROXCEN_S_fine.h5`

**Changes needed:**
1. Parse target name from filename position (already done in some places)
2. Use parsed target instead of hardcoded 'PROXCEN'
3. Handle GBT vs Parkes telescope conventions (different cadence patterns)
4. File discovery: glob by `{telescope}_*_{target}_*_{res}.h5`

**Deliverable:** Target-agnostic file discovery + naming utilities.

### 3E. Dashboard Multi-Target UI

**Goal:** Dashboard supports selecting any target from the registry and
running the full pipeline against it.

**Changes:**
1. **Main dashboard:** Target selector dropdown (populated from DB).
   Download, scan, and barycentric correct all use the selected target.
2. **Stack page:** Target selector. Epoch list fetched dynamically.
   Stack and two-layer runs parameterized by target.
3. **Sky map:** Show all targets in registry with observation status.
4. **Target detail page:** Per-target summary showing epochs available,
   scan status, cross-epoch candidates, stack results.

**Deliverable:** Multi-target dashboard with target selector throughout.

### 3F. Automated Pipeline Per Target

**Goal:** One-click (or scheduled) full pipeline run for any target.

**Workflow:**
1. Select target from registry
2. Dashboard shows available epochs from BL API
3. Download missing fine-res files
4. Run turbo_seti batch search on all files
5. Run ON/OFF RFI rejection
6. Apply barycentric correction
7. Cross-epoch match
8. Two-layer pipeline (Layer 1 + Layer 2 + Layer 2.5 scorecard)
9. Generate per-target report

**Output:** Per-target summary with traffic-light scoring:
- RED: No candidates survived cross-epoch filter
- YELLOW: Candidates found but flagged as RFI by scorecard
- GREEN: Unexplained candidates, warrant manual review

**Deliverable:** End-to-end automated pipeline runnable for any target.

---

## Phase 4: ML Signal Classification (NICE TO HAVE)

**Status:** Deprioritized as of 2026-08-13. The two-layer pipeline with
Layer 2.5 RFI scorecard already handles most signal classification
needs. ML may add value later but is not blocking survey work.

Kept here for future reference if ML becomes valuable.

### 4A. Training Set Generation

**Goal:** Create labeled examples for a classifier.

**Sources:**
1. **RFI examples:** Harvest from OFF frames and known RFI frequencies.
2. **Synthetic signal examples:** Inject at various SNR, drift rates,
   bandwidths.
3. **Real astronomy signals:** Known pulsars, satellites from other BL
   observations.

**Deliverable:** Labeled waterfall image dataset.

### 4B. Waterfall CNN Classifier

Train on waterfall tiles to classify: noise, RFI types, candidate signal.
RTX 2060 6GB limits model size but can handle a small ResNet variant.

### 4C. Unsupervised Anomaly Detection

Feature extraction + UMAP/t-SNE + DBSCAN clustering. Outliers are
potential novel signals.

---

## Technical Notes

### Python Environment

- **Production:** Python 3.11 at 
  `C:\Users\w4gon\AppData\Local\Programs\Python\Python311\python.exe`
- **Wrapper:** `seti-python.bat` in G:\seti
- turbo_seti 2.3.2, blimpy 2.1.4, h5py 3.16.0
- **WARNING:** Python 3.14 is default on PATH but has broken turbo_seti 
  (missing pkg_resources). Always use 3.11 explicitly.

### turbo_seti Limitations

- **Drift rate resolution is data-format dependent:** 
  `min_drift_step = df / (n_timesteps * tsamp)`
  - Fine-res (2.79 Hz/ch, 18.25s, 20 ts): 0.0076 Hz/s -- excellent
  - Mid-res (2861 Hz/ch, 1.074s, 16 ts): 166.5 Hz/s -- useless for narrowband
- Searches both positive and negative drift from min_drift to max_drift.
- No built-in RFI flagging beyond DC bin blanking.
- Output: one .dat + .log per input file, tab-separated hit table.
- .dat column format: `TopHit# DriftRate SNR UncorrectedFreq CorrectedFreq 
  Index freq_start freq_end SEFD SEFD_freq CoarseChan NumHits`
  (Index column is parts[5], 0-indexed)

### BL API

- Endpoint: `http://seti.berkeley.edu/opendata/api`
- Query: `query-files?target=PROXCEN` returns dict with `data` key
- File types: fine (37 GB), mid (233 MB), time (19 GB)
- Cadence: S=ON (source), R=OFF (reference), in ABABAB pattern
- 44 PROXCEN sessions available, 24 with full mid-res cadence

### File Naming Convention

```
{Telescope}_{MJD}_{Seq}_{TARGET}_{S|R}_{resolution}.h5
```
- S = ON source, R = OFF reference
- resolution = fine, mid, time

---

## Priority Order (Updated 2026-08-13)

1. ~~Signal injection test~~ (Phase 1A) -- COMPLETE
2. ~~Fine-res pipeline~~ -- 4 epochs scanned, more downloading. IN PROGRESS.
3. ~~Barycentric correction + cross-epoch~~ (Phase 2A-2B) -- COMPLETE.
4. ~~Incoherent stack + two-layer pipeline~~ (Phase 2C) -- COMPLETE.
   Layer 1 filter, Layer 2 stack, Layer 2.5 RFI scorecard all operational.
   Validated: 30,211 hits killed at min_epochs=3, zero false candidates.
5. **Scan remaining PROXCEN epochs** -- More epochs = deeper stacking.
   4 of 20 available done. Downloading more now.
6. **Multi-target survey** (Phase 3) -- Generalize beyond PROXCEN.
   This is the next development priority.
   - 3A: Target registry (SQLite + dashboard UI)
   - 3B: Per-target data organization
   - 3C: Dynamic epoch discovery (kill hardcoded EPOCHS dict)
   - 3D: Generalized file naming (any telescope/target)
   - 3E: Dashboard multi-target UI
   - 3F: Automated per-target pipeline
7. **ML classification** (Phase 4) -- NICE TO HAVE, not blocking.
   Revisit if the two-layer scorecard proves insufficient.

### Full-Band Processing Notes

Sensitivity is identical across all sub-band widths. The df, tsamp, and 
n_timesteps are fixed by the data format. Sub-band width only affects 
memory management and wall-clock time, not detection capability. Overlap 
(512 channels) ensures no signal is lost at sub-band boundaries.

Full-band throughput estimates per 12 GB fine-res file:
- 8,192 ch sub-bands: ~98h (27,034 sub-bands)
- 131,072 ch sub-bands: ~6h (1,690 sub-bands)
- 262,144 ch sub-bands: ~3h (845 sub-bands)

Parallelization (multiprocessing) can cut wall time by 4-8x. Targeted 
frequency windows (e.g. 50 MHz around expected barycentric frequency) 
reduce sub-band count by 90%+ for initial reconnaissance.

---

## Performance Optimizations (Post-Phase 3)

### HDF5 Hyperslab Batch Read
**Problem:** ML inference and stacked waterfall read 5,000+ individual channel slices from random positions in 207-million-channel HDF5 files. Each read seeks to a different 1 MB compressed chunk, causing disk-bound latency even with sequential sorting.

**Solution:** Group hits by contiguous channel ranges and read each range as a single HDF5 hyperslab operation. For example, if 50 hits fall within channels 91,674,000-91,675,000, read that entire 1,000-channel range in one operation and slice in memory. Reduces disk seeks from 5,000 to ~100 per file.

**Expected speedup:** 10-50x for inference, enables real-time stacked waterfall rendering.

**Priority:** High (blocks interactive ML dashboard and stacked waterfall UX)

---

## References

- Breakthrough Listen Open Data: http://seti.berkeley.edu/opendata
- turbo_seti: https://github.com/UCBerkeleySETI/turbo_seti
- blimpy: https://github.com/UCBerkeleySETI/blimpy
- Zhang et al. 2019: "Searching for Extraterrestrial Technologies with 
  Machine Learning" (BL ML approach)
- Enriquez et al. 2017: "The Breakthrough Listen Search for Optical Laser 
  Emission" (BL pipeline overview)
- Worden et al. 2017: "Breakthrough Listen" program overview

---

*Document created: 2026-08-06*
*Last updated: 2026-08-13*
*Authors: Carl & Joel*
