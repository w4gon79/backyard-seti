"""Unit-verify the new GBT grammar parsers without restarting anything."""
import sys

sys.path.insert(0, r'G:\seti\dashboard')
sys.path.insert(0, r'G:\seti\src')

import app as dash  # noqa: E402
import db as dbmod  # noqa: E402

# 1. Local-data grouping parser
GBT_F = 'spliced_blc0001020304050607_guppi_57532_03953_GJ447_0011.gpuspec.0000.h5'
PARKES_F = 'Parkes_58058_44645_NGC1426_S_fine.h5'
print('gbt  grouping:', dash._local_file_target_mjd(GBT_F))
print('parkes grouping:', dash._local_file_target_mjd(PARKES_F))

# 2. Epoch labeler
print('gbt  epoch:', dash._epoch_label_from_files([f'fine/{GBT_F}']))
print('parkes epoch:', dash._epoch_label_from_files([f'fine/{PARKES_F}']))

# 3. Importer ABACAD classification (regex logic, no DB write)
import os, re  # noqa: E402
pat = dbmod._GBT_FNAME_PAT
meta = {'target': 'GJ447'}
for fn in [GBT_F,
           'spliced_blc0001020304050607_guppi_57532_03613_HIP56677_0010.gpuspec.0000.h5',
           PARKES_F]:
    if '_S_' in fn:
        cls = 'ON (parkes S)'
    elif '_R_' in fn:
        cls = 'OFF (parkes R)'
    elif 'guppi_' in fn:
        m = pat.search(os.path.basename(fn))
        tok = m.group(3).upper() if m else ''
        cls = 'ON (A-scan)' if tok == str(meta['target']).upper() else f'OFF (companion {tok})'
    else:
        cls = 'OFF (default)'
    print(f'classify {fn.split("_guppi_")[-1] if "guppi_" in fn else fn}: {cls}')
