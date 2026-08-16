# Breakthrough Listen Open Data API

## Base URL
```
http://seti.berkeley.edu/opendata/api
```

## Endpoints

### List Telescopes
```
GET /api/list-telescopes
```
Returns: `[["GBT"], ["Parkes"], ["APF"]]`

### List File Types
```
GET /api/list-file-types
```
Returns: `[["baseband data"], ["filterbank"], ["HDF5"], ["data"]]`

### List Grades (Resolutions)
```
GET /api/list-grades
```
Returns: `["time", "fine", "mid"]`

### List Targets
```
GET /api/list-targets
GET /api/list-targets?telescope=Parkes
```
Returns array of 12,087 target names. Note: the telescope filter doesn't actually filter, it returns the same list regardless.

### Query Files (Main Search)
```
GET /api/query-files?target=PROXCEN&telescope=Parkes&file-type=filterbank
```

**Parameters:**
| Param | Required | Example | Notes |
|-------|----------|---------|-------|
| `target` | Yes | `PROXCEN` | Must match BL's internal name exactly (see Target Names below) |
| `telescope` | No | `Parkes` | `GBT`, `Parkes`, or `APF` |
| `file-type` | No | `filterbank` | `filterbank`, `HDF5`, `data`, `baseband data` |

**Response:** Array of file objects, each containing:
- `url` - Direct download URL (e.g., `http://blpd8.ssl.berkeley.edu/dl/Parkes_57941_36956_PROXCEN_S_fine.h5`)
- `filesize` - Size in bytes

**Note:** Results are capped at 500 files per query.

### Target Names

BL uses internal target names that often differ from common catalog names. Always search `/api/list-targets` first if unsure.

| Common Name | BL Target Name |
|-------------|---------------|
| Proxima Centauri | `PROXCEN` |
| Tabby's Star | `KIC8462852` |
| Barnard's Star | Search list-targets for "BARNARD" |
| Wolf 359 | Search list-targets for "WOLF" |

To find a target: query `/api/list-targets` and grep for partial matches.

## Filename Convention

**Two different grammars exist.** Parkes files encode cadence position
explicitly; GBT files encode a product pipeline instead and carry no
ON/OFF markers at all.

### Parkes grammar

```
{Telescope}_{MJD}_{Sequence}_{TARGET}_{POSITION}_{RESOLUTION}.h5
Parkes_57791_72989_PROXCEN_S_fine.h5
```

| Field | Meaning | Example |
|-------|---------|---------|
| Telescope | Observing instrument | `Parkes`, `GBT`, `APF` |
| MJD | Modified Julian Date of session | `57941` |
| Sequence | Sequence number within session | `36956` |
| TARGET | BL internal target name | `PROXCEN` |
| POSITION | On/off position, **optional** | `S` = on, `R` = off, absent = bare form |
| RESOLUTION | Frequency resolution | `fine`, `mid`, `time` |

Cadence: ABABAB with a fixed blank-sky offset (0.5 deg dec at Parkes).
Consecutive-seq S/R pairs from one MJD form a cadence set. A missing
position marker must be resolved from sequence order or session context.

### GBT grammar

```
(spliced_)?blc{NN}_guppi_{MJD}_{SEQ}_{TARGET}_{SCAN}.{PRODUCT}.{TIER}.h5
blc00_guppi_59433_42615_HIP29806_0041.rawspec.0000.h5
spliced_blc0001020304050607_guppi_57620_41924_Hip20917_0006.gpuspec.0002.h5
```

| Field | Meaning | Notes |
|-------|---------|-------|
| blc{NN} | Compute node / BL compute board | `spliced_` prefix = multi-node combined file |
| guppi | GBT pulsar backend instrument marker | constant |
| MJD | Session date | group sessions by this |
| SEQ | Sequence number within session | sort by this to see cadence order |
| TARGET | BL target token | **exact-match this** (see prefix trap) |
| SCAN | Scan number for that target | `0041` etc. |
| PRODUCT | `rawspec` = Berkeley high-res, `gpuspec` = other instruments | rawspec is the standard product |
| TIER | Resolution tier | `0000` = fine-res; higher tiers are coarser |

Cadence: **ABACAD companion targets, no OFF files.** GBT interleaves the
primary target (A) with other nearby targets (B, C, D). Companions are
discovered by finding other targets' scans at adjacent SEQ numbers in the
same session MJD (see `src/download_gbt.py --companions`). Each scan spans
~26 nodes x 187.5 MHz covering 1.1-11.9 GHz; cherry-pick sub-bands via the
API `center_freq` field.

### Cadence Sets

- **Parkes:** ABABAB, `_S`/`_R` markers, blank-sky offset OFFs.
- **GBT:** ABACAD (post-2016) or ABABAB with 2-dec offset (pre-2016),
  OFFs are companion target scans, nothing in the filename marks them.

## Download URLs

Files are hosted at:
```
http://blpd8.ssl.berkeley.edu/dl/{filename}      (newer files)
http://blpd8.ssl.berkeley.edu/dl2/{filename}     (newer files, alternate)
```

Direct HTTP download, no authentication required.

## Rate Limits

- The API has no documented rate limits but be reasonable
- Query results are capped at 500 files
- File downloads are large (12-15 GB for fine resolution)

## Python Example

```python
import urllib.request, json

API = "http://seti.berkeley.edu/opendata/api"

def find_target(name_partial):
    """Search for a target by partial name."""
    url = f"{API}/list-targets"
    with urllib.request.urlopen(url) as resp:
        targets = json.loads(resp.read().decode())
    matches = [t[0] if isinstance(t, list) else t for t in targets
               if name_partial.lower() in str(t).lower()]
    return matches

def get_files(target, telescope=None, filetype=None):
    """Query files for a target."""
    params = f"target={target}"
    if telescope:
        params += f"&telescope={telescope}"
    if filetype:
        params += f"&file-type={filetype}"
    url = f"{API}/query-files?{params}"
    with urllib.request.urlopen(url) as resp:
        data = json.loads(resp.read().decode())
    return data if isinstance(data, list) else data.get("data", [])
```

## Field & Behavior Notes (probed 2026-08-15/16)

- **File size field is `size`** (bytes), not `filesize`. Some targets return
  it null for all files.
- **`?target=` matching is literal and case-insensitive against
  separator-free tokens.** `KIC8462852` returns 321 files; `KIC_8462852`,
  `KIC 8462852` return zero. Cross-ID catalog forms unlock hidden data:
  Barnard's Star = `GJ699`, Tau Ceti = `GJ71`, HD 95735 = `GJ411`,
  Tabby's Star = `KIC8462852`, Proxima = `PROXCEN`.
- **`?target=` PREFIX-MATCHES (2026-08-16 trap).** Querying `HIP2` returns
  9,525 files of which exactly **10** are HIP2; the rest belong to HIP225,
  HIP26, HIP29806 and 608 other targets. Querying `GJ1` returns zero GJ1
  fine files (1,367 files, all other GJ* targets). Short names collide;
  long unique names (PROXCEN, NGC1426) are safe. **Always filter query
  results by the exact target token parsed from the filename.** The tools
  in this repo (`bl_search.py`, `download_gbt.py`, `bl_catalog.py`) do this
  automatically.
- **Base-name queries return all positional variants** (`0003-066` returns
  the `_S`, `_R`, and bare files; 82 total). Query base names, not variants.
- **Bare-form filenames exist** (`Parkes_57770_78921_ALPHACEN_fine.h5`, no
  `_S`/`_R` marker); parsers must treat the position marker as optional.
- **No open endpoint exists for h5 products.** Empty `target=` returns a
  10,000-capped dump of GBT raw voltage `.raw` files only; `file-type` is
  ignored on that path. Open browsing requires the per-target sweep
  (see `src/bl_catalog.py`).
- **Response cap:** 500 files per target query is the documented cap
  (PROXCEN returns 8,380 in practice, so caps vary; treat 10,000 as the
  hard list cap observed on the unfiltered dump).
