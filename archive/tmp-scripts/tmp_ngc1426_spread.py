"""Check barycentric velocity spread across NGC1426's three epochs (2017-11-01, 2017-12-15, 2018-01-17)."""
import sys
sys.path.insert(0, 'G:/seti/src')
from barycentric_correct import compute_barycentric_velocity

# NGC1426: RA 03h40m06.3s, Dec -22d13m34s (J2000)
RA_H, DEC = 3.66842, -22.226
F_MHZ = 1400.0  # mid L-band, Parkes PF1

# Mid-day UTC MJDs for the three epochs
epochs = [
    ('2017-11-01', 58058.5),
    ('2017-12-15', 58102.5),
    ('2018-01-17', 58135.5),
]

vels = []
for label, mjd in epochs:
    v = compute_barycentric_velocity(mjd, RA_H, DEC, telescope='parkes')
    vels.append((label, mjd, v))
    shift_hz = F_MHZ * 1e6 * v / 299792458.0
    print(f"{label}  MJD {mjd:.1f}  v_bary = {v:+8.2f} m/s   "
          f"topo->bary shift @1400MHz = {shift_hz/1000:+8.2f} kHz")

print()
for i in range(len(vels)):
    for j in range(i + 1, len(vels)):
        dv = vels[j][2] - vels[i][2]
        df = F_MHZ * 1e6 * dv / 299792458.0
        print(f"{vels[i][0]} -> {vels[j][0]}: dv = {dv:+8.2f} m/s  "
              f"uncorrected freq offset = {df:+8.0f} Hz  "
              f"vs 10 Hz match tol -> {abs(df)/10:.0f}x tolerance")
