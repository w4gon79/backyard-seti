# Backyard SETI: Project Roadmap

## Mission Statement

Use Breakthrough Listen open data to search for technosignatures, developing 
pipeline tooling that goes beyond the standard turbo_seti matched-filter 
approach. BL published this data specifically hoping amateur scientists would 
find new analysis methods. That is the niche we aim to fill.

---

## Current State (2026-08-06)

### Pipeline Components

| Component | Script | Status | Origin |
|-----------|--------|--------|--------|
| BL API search/download | `bl_search.py` | Working | Ours |
| Batch downloader | `download_proxima.py` | Working | Ours |
| Doppler drift search | `bl_doppler_search.py` | Working | Wraps turbo_seti |
| Batch runner | `batch_search.py` | Working | Ours |
| RFI rejection (ON/OFF) | `bl_rfi_reject.py` | Working | Ours (not in SETI repo) |
| Waterfall inspection | `bl_inspect.py` | Working | Ours |

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

All 36 PROXCEN **mid-res** files processed at SNR 25 and SNR 10. Drift range: 
0.00001 to 5 Hz/s, both polarities.

**Result: Zero hits across all 36 mid-res files at both thresholds.**

**Root cause (confirmed 2026-08-07): drift rate resolution mismatch.**
turbo_seti min drift step = df / (n_timesteps * tsamp). Mid-res data has 
df=2861 Hz and tsamp=1.074s, giving 166 Hz/s drift resolution. Any signal 
drifting slower than 166 Hz/s is invisible. This is a fundamental limit of 
the mid-res format, not a bug.

Fine-res data (df=2.79 Hz, tsamp=18.25s) gives 0.0076 Hz/s drift resolution. 
Fine-res pipeline validated: synthetic injection recovered at SNR 698, real 
data produces legitimate hits across 580 MHz band.

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

**Method:**
1. For each observation epoch, compute the expected barycentric Doppler shift 
   for Proxima Centauri
2. Apply correction to shift all frequencies to the solar system barycenter 
   frame
3. A real signal now appears at the SAME frequency in all epochs
4. RFI appears at different barycentric-corrected frequencies across epochs

**Deliverable:** `barycentric_correct.py` + corrected frequency tables.

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

## Phase 3: ML Signal Classification

**Goal:** Train a model to distinguish interesting signals from RFI based on 
morphology, not just frequency coincidence.

### 3A. Training Set Generation

**Goal:** Create labeled examples for a classifier.

**Sources:**
1. **RFI examples:** Harvest from OFF frames and known RFI frequencies in our 
   data. Categories: constant (zero drift), chirped (linear drift but present 
   in both ON/OFF), intermittent (appears in some epochs only), broadband 
   (wide bandwidth), multi-tone (harmonically related narrowband signals).
2. **Synthetic signal examples:** Generate waterfall plots with injected 
   signals at various SNR (5-30), drift rates (0.01-5 Hz/s), and bandwidths 
   (narrowband to ~100 Hz).
3. **Real astronomy signals:** Known pulsars, satellites, and aircraft 
   transponder signals from other BL observations (for variety).

**Deliverable:** Labeled waterfall image dataset, ~10,000 examples minimum.

### 3B. Waterfall CNN Classifier

**Architecture:** Convolutional Neural Network on waterfall plot tiles.

**Input:** 256x256 or 512x512 waterfall tiles (frequency x time), normalized 
to dB above local noise floor.

**Output:** Per-tile classification: noise, RFI constant, RFI chirped, RFI 
broadband, candidate signal.

**Training:**
1. Start with a pretrained image model (ResNet, EfficientNet) and fine-tune 
   on waterfall tiles
2. Alternative: train from scratch if the domain is different enough from 
   natural images
3. Data augmentation: random frequency/time shifts, noise injection, 
   bandwidth variation

**Reference:** Zhang et al. 2019 (Breakthrough Listen) used a similar 
approach. We can use their architecture as a starting point.

**Deliverable:** Trained model + inference script + classification results 
on PROXCEN data.

### 3C. Unsupervised Anomaly Detection

**Goal:** Find signals that do not fit any known category.

**Method:**
1. Extract all hit features from low-SNR pipeline runs: drift rate, SNR, 
   bandwidth, duty cycle, persistence across ON/OFF, frequency spacing to 
   nearest other hit
2. Apply UMAP or t-SNE dimensionality reduction
3. Cluster with DBSCAN or HDBSCAN
4. Outliers (points far from any cluster) are potential novel signals
5. RFI forms tight clusters due to predictable morphology. Real signals may 
   look subtly different.

**Deliverable:** `anomaly_detect.py` + outlier list + visualization.

---

## Phase 4: Automated Target Survey

**Goal:** Scale the pipeline to many targets with minimal manual intervention.

### 4A. Target Selection

**Sources:**
- BL priority targets list (nearby stars, exoplanet hosts)
- TESS objects of interest (confirmed exoplanets within 100 ly)
- Anomalous stars (Tabby's Star, Boyajian variables)
- SETI candidate revisit list

**Selection criteria:**
- Has ON/OFF cadence data available through BL API
- Mid-res files available (manageable download size)
- At least 2 observation epochs for cross-correlation

### 4B. Fully Automated Pipeline

**Workflow:**
1. Query BL API for target
2. Download all available mid-res cadence files
3. Run turbo_seti batch search at SNR 10
4. Run ON/OFF RFI rejection
5. Apply barycentric correction
6. Cross-correlate across epochs
7. Run ML classifier on any candidates
8. Generate report: hits, candidates, RFI catalog, detection efficiency

**Output:** Per-target summary report with traffic-light scoring:
- RED: No hits, pipeline validated
- YELLOW: Hits found but explained by RFI
- GREEN: Unexplained ON-only candidates across multiple epochs

### 4C. Scheduled Runs

**Option:** Set up as a cron job or scheduled task. Download and process 
new BL data as it becomes available. BL releases data roughly 6-12 months 
after observation.

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

## Priority Order

1. ~~Signal injection test~~ (Phase 1A) -- COMPLETE
2. **Fine-res full pipeline run** -- Run all 6 PROXCEN fine-res files 
   through sub-band pipeline. Targeted frequency windows first, then 
   expand to full-band overnight.
3. **Sub-band edge deduplication** -- Feature add: when overlapping 
   sub-bands produce duplicate hits for the same signal, deduplicate 
   by frequency proximity. Edge filter to suppress boundary artifacts 
   at sub-band edges (planned, not blocking).
4. **RFI characterization** (Phase 1B) -- Catalog hits from OFF frames
5. **Barycentric correction + cross-epoch** (Phase 2A-2B) -- Real signal hunting
6. **Incoherent stack** (Phase 2C) -- Sensitivity boost from multiple epochs
7. **ML training set** (Phase 3A) -- Foundation for classifier
8. **CNN classifier** (Phase 3B) -- Novel contribution
9. **Automated survey** (Phase 4) -- Scale up

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
*Last updated: 2026-08-07*
*Authors: Carl & Joel*
