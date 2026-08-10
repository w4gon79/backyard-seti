# SETI Dashboard Performance Optimization Roadmap

## Completed (2026-08-10): SQLite Migration

**The big one.** Replaced JSON hit storage with indexed SQLite database.

| Operation | Before (JSON) | After (SQLite) | Speedup |
|-----------|-------------|----------------|---------|
| Scan list | 10-30s | <100ms | 100x+ |
| Hit query (SNR>=10) | 5-10s | <100ms | 50x+ |
| Cross-epoch (SNR=10) | 9s | 0.25s | 36x |
| Paginated hits | N/A (load all) | instant | new feature |

- Database: `data/seti_hits.db` (506 MB, WAL mode)
- 1.36M hits across 3 scans, 7 indexes
- 8 new `/api/db/` Flask endpoints with pagination and filtering
- Cross-epoch results cached in DB, auto-loadable from history
- Migration is idempotent (`src/migrate_to_sqlite.py`)

**Rule: use SQLite wherever possible.** JSON is kept for small metadata only.

## Completed (2026-08-08): Low-Hanging Fruit

1. ✅ Canvas loops throttled (starfield, star map, freq map from 60fps to 10-15fps)
2. ✅ Spectrum redraw only on new data arrival
3. ✅ Log rebuilds skipped when content unchanged (signature check)
4. ✅ Filter inputs debounced 300ms (was re-sorting 30k hits per keystroke)
5. ✅ Mission Control POLL_INTERVAL 2000ms to 3000ms
6. ✅ Cached filtered/sorted hits array (only recompute on param change)

## Remaining: Medium Effort Optimizations

### 1. Waterfall pixel rendering: ImageData buffer
**Problem:** `drawSpectrum` renders pixel-by-pixel with `fillRect(x, y, 1, rowH)`. With 200+ rows and ~300px width, that's 60,000+ `fillRect` calls per redraw.

**Fix:** Use `ImageData` buffer. Write pixels to a flat `Uint8ClampedArray`, then one `putImageData` call. 10-50x faster.

**File:** `mission.js` `drawSpectrum()` function

### 2. Frequency map: offscreen canvas caching
**Problem:** `drawFreqMap` redraws all 794 segments every frame even though only the current sub-band marker changes.

**Fix:** Draw the completed/remaining segments to an offscreen canvas once (only when sub-bands_done changes). Each frame, blit the cached image + draw only the current marker + border on top. Nearly zero per-frame cost.

**File:** `mission.js` `drawFreqMap()` function

### 3. Log panel: incremental DOM appends
**Problem:** When log content changes, `updateLog` still creates 500 DOM nodes from scratch (createElement + appendChild for each line).

**Fix:** Track `lastRenderedLineCount`. On update, only append the new lines beyond that count. Remove old lines from the top if exceeding the max. Use a DocumentFragment for batch insertion.

**Files:** `mission.js` `updateLog()`, `dashboard.js` `pollScanStatus()` log section

### 4. Scan completion: debounced list refresh
**Problem:** `loadScansList` is called on every scan completion detection, triggering a full API call + dropdown rebuild + results reload.

**Fix:** Debounce the completion handler. Only call `loadScansList()` once 5 seconds after the scan transitions from active to idle, not immediately. Or only refresh the scan selector without reloading results.

**File:** `dashboard.js` `pollScanStatus()` completion handler

### 5. Hit table: global array instead of data attributes
**Problem:** `renderHitTable` builds `encodeURIComponent(JSON.stringify(h))` per row and embeds it as a data attribute. 100 rows of full JSON encoding per page render.

**Fix:** Store the current page's hits in a global array `_currentPageHits[rowNum] = hitObject`. Row click handler reads from the array by index instead of parsing JSON from a data attribute. Eliminates all JSON encode/decode.

**File:** `dashboard.js` `renderHitTable()` and `showWaterfall()` functions

## Priority Order
1. Item 1 (ImageData waterfall) - biggest single-operation speedup
2. Item 2 (offscreen freq map) - eliminates near-constant redraw cost
3. Item 3 (incremental logs) - reduces DOM churn on both pages
4. Item 5 (hit table array) - speeds up pagination significantly
5. Item 4 (debounced scan list) - minor, only fires on scan completion

*Created: 2026-08-08*
