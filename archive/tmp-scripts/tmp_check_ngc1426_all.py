"""Verify integrity of all local NGC1426 fine files (both epochs)."""
import glob
import h5py

for p in sorted(glob.glob(r"G:\seti\data\fine\*NGC1426*.h5")):
    name = p.rsplit("\\", 1)[-1]
    try:
        with h5py.File(p, "r") as f:
            a = f["data"].attrs
            n_int = f["data"].shape[0]
            fmin = min(float(a["fch1"]) + abs(float(a["foff"])) * (int(a["nchans"]) - 1), float(a["fch1"]))
            fmax = max(float(a["fch1"]), fmin)
            print(f"OK   {name}  ints={n_int:2d}  band {fmin:.3f}-{fmax:.3f} MHz")
    except Exception as e:
        print(f"FAIL {name}: {type(e).__name__}: {str(e)[:100]}")
