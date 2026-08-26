"""Read-only: verify the first GBT file's HDF5 header against what the
pipeline needs (fch1/foff/nchans/tstart/source_name)."""
import h5py

P = r"G:\seti\data\fine\spliced_blc0001020304050607_guppi_57532_03953_GJ447_0011.gpuspec.0000.h5"
with h5py.File(P, "r") as f:
    a = f["data"].attrs
    for k in ("fch1", "foff", "nchans", "tstart", "tsamp", "source_name",
              "nints", " telescope".strip()):
        if k in a:
            print(f"{k:12s} = {a[k]}")
        else:
            print(f"{k:12s} = <missing>")
    print("data shape  =", f["data"].shape)
    fmin = float(a["fch1"]) + abs(float(a["foff"])) * (int(a["nchans"]) - 1)
    print(f"band        = {min(float(a['fch1']), fmin):.3f} - "
          f"{max(float(a['fch1']), fmin):.3f} MHz")
