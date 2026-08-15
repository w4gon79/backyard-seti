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

BL filenames follow this pattern:
```
{Telescope}_{MJD}_{Sequence}_{TARGET}_{POSITION}_{RESOLUTION}.h5
```

| Field | Meaning | Example |
|-------|---------|---------|
| Telescope | Observing instrument | `Parkes`, `GBT` |
| MJD | Modified Julian Date of session | `57941` |
| Sequence | Sequence number within session | `36956` |
| TARGET | BL internal target name | `PROXCEN` |
| POSITION | On/off position | `S` = on target, `R` = reference (off) |
| RESOLUTION | Frequency resolution | `fine`, `mid`, `time` |

### Cadence Sets

BL observes targets in **ABABAB** or **ABABA** cadences:
- **_S** = Source (on target)
- **_R** = Reference (off target)

Files with consecutive sequence numbers from the same MJD, alternating S/R, form a cadence set. These are essential for RFI rejection (comparing on vs off to identify terrestrial interference).

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

## Field & Behavior Notes (probed 2026-08-15)

- **File size field is `size`** (bytes), not `filesize`. Some targets return
  it null for all files.
- **`?target=` matching is literal and case-insensitive against
  separator-free tokens.** `KIC8462852` returns 321 files; `KIC_8462852`,
  `KIC 8462852` return zero. Cross-ID catalog forms unlock hidden data:
  Barnard's Star = `GJ699`, Tau Ceti = `GJ71`, HD 95735 = `GJ411`,
  Tabby's Star = `KIC8462852`, Proxima = `PROXCEN`.
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
