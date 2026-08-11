# Stacked Waterfall Feature - Status & TODO

## Feature Description
When clicking a peak in the Incoherent Stack Top Peaks table, show a side-by-side waterfall comparison:
- **Left panel**: Single epoch raw ON-OFF residual (noisy)
- **Right panel**: Stacked average across all epochs (SNR boosted by √N)
- Optional "All Epochs" grid view (3+ epochs only)

This lets you visually confirm whether a peak is a real signal (appears consistently across epochs, brightens in the stacked view) or RFI/transient noise.

## What's Done (committed)

### Backend (app.py, ~line 3395)
- Endpoint `/api/stack/peaks/<job_id>/stacked_waterfall` implemented
- Loads ON/OFF pairs per epoch, subtracts OFF from ON
- Applies barycentric correction per epoch (using TARGET_COORDS tuple fix)
- Interpolates onto common barycentric frequency grid
- Returns: single_epoch, stacked, all_epochs, freqs, times
- Fixed to use only 1 ON/OFF pair per epoch (not all 3)
- Fixed to compute freqs from header instead of `wf.container.sf_freqs` (doesn't exist)
- Uses float32 + explicit `del` + `gc.collect()` between files

### Frontend (stack.js)
- `showStackWaterfall()` detects stack job context, routes to stacked endpoint
- `showStackedWaterfall()` calls the endpoint, shows loading state
- `renderStackedWaterfallPlot()` builds the side-by-side Plotly layout
- Mode toggle: "Single vs Stacked" (default) and "All Epochs" grid
- `renderWaterfallHeatmap()` helper for individual panels
- Shared colorscale, yellow dotted center frequency marker

### CSS (stack.css)
- `.stacked-wf-controls` mode toggle buttons
- `.wf-pair` side-by-side flex layout
- `.wf-grid` responsive grid for all-epochs view
- `.wf-panel-label` styling
- Badge styles for classification + cross-epoch features

### Commits
- `836bba8` - Add stacked waterfall: single epoch vs stacked comparison
- `3a88ba1` - Fix stacked waterfall: tuple coords + sf_freqs crash

## What's Broken (the blocker)

**OOM Kill.** Each Parkes fine-res HDF5 file is 12.44 GB with 207,618,048 channels. Neither blimpy's `Waterfall(f_start, f_stop)` nor direct `h5py` channel slicing can extract a narrow frequency slice without loading excessive data.

### Technical details
- HDF5 chunk size: 1,048,576 channels (1 MB per chunk per timestep)
- Target channel index for PROXCEN at ~3000 MHz: ~91,674,873 (channel 91 million)
- `blimpy Waterfall(f_start, f_stop)`: still loads the full file, SIGKILL'd
- `h5py dset[:, :, chan_start:chan_stop]`: "OSError: Can't synchronously read data (can't open directory)" - likely HDF5 btree traversal failure at extreme channel indices
- Reading one timestep at a time (`dset[t, 0, chan_start:chan_stop]`): same h5py error
- Reading full chunk containing target: same h5py error
- The existing `/api/waterfall` endpoint works because it operates on different files/frequencies where the channel index is much lower

## TODO / Next Approaches to Try

### 1. Verify the existing waterfall endpoint on these exact files
The `/api/waterfall` endpoint works on scan hits, but those might be at different frequencies (lower channel indices). Test whether it actually works on the same PROXCEN fine-res files at ~3000 MHz. If it doesn't, the problem is universal to these files, not specific to the stacked endpoint.

### 2. Blimpy `read_data` with channel range instead of frequency range
Blimpy has alternative data loading methods that might handle channel-based slicing differently:
```python
wf = Waterfall(file_path, load_data=False)
wf.read_data(f_start=..., f_stop=...)  # might use different code path
```
Or:
```python
from blimpy.io import reconfigure
# Direct reconfigure with channel indices
```

### 3. Subprocess isolation per epoch
Run each epoch's file load in a separate Python subprocess that extracts the slice and writes it to a temp .npy file, then the main Flask process collects the results. This isolates memory completely:
```python
# For each epoch: subprocess reads HDF5, writes narrow slice to /tmp/epoch_N.npy
# Main process: load all .npy files (tiny), combine, return
```

### 4. Pre-compute waterfall slices during stack run
During the stack pipeline (which already loads all files successfully in a background thread), extract and save narrow waterfall slices for each peak detected. Store as `.npz` alongside the stack output. The waterfall endpoint then just loads pre-computed data, no HDF5 reads needed.
- Pros: instant response, no memory issues
- Cons: only available for peaks found during the stack run, not arbitrary frequencies

### 5. HDF5 file repair / rechunk
The "can't open directory" error might indicate the HDF5 btree is corrupted or inefficient at high channel indices. Running `h5repack` to optimize chunk layout could fix the h5py read errors. But this requires re-processing 12 GB files.

### 6. Use blimpy's `load_data` with `max_load` parameter
The existing code in `bl_inspect.py` uses `max_load` to limit data size. This might use a different code path that avoids the full load.

## Priority
**Low.** This is a nice-to-have visualization feature. All other stack features work. The stacked waterfall is not needed for scientific analysis, just for visual confirmation of peaks.

## Files Modified
- `G:\seti\dashboard\app.py` - backend endpoint (~line 3395)
- `G:\seti\dashboard\static\js\stack.js` - frontend modal + rendering
- `G:\seti\dashboard\static\css\stack.css` - layout styles
- `G:\seti\dashboard\templates\stack.html` - cache buster versions
- `G:\seti\dashboard\test_h5py.py` - test script (can be deleted)
- `G:\seti\dashboard\debug_wf.py` - debug script (can be deleted)
