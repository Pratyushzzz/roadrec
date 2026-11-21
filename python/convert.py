#!/usr/bin/env python3
"""
roadrec/convert.py - Sensor Logger export (zip or folder) -> classifier CSV.

Uses TotalAcceleration.csv (m/s^2, gravity included), Gyroscope.csv (rad/s)
and Location.csv (speed in m/s). Output: t, ax, ay, az (in g), gx, gy, gz
(deg/s), speed (m/s, blank between GPS fixes).

    python3 convert.py "Test_1-2026-10-03_17-30-22.zip" test1.csv
    python3 classifier.py test1.csv --out out_test1
"""
import sys, os, zipfile, tempfile, warnings
import numpy as np

G = 9.80665


def read(folder, name):
    path = os.path.join(folder, name)
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            arr = np.genfromtxt(path, delimiter=",", names=True, dtype=float)
    except (IndexError, ValueError):
        return None
    return arr if np.atleast_1d(arr).size > 0 else None


def find_folder(src):
    if os.path.isdir(src):
        root = src
    else:
        root = tempfile.mkdtemp()
        zipfile.ZipFile(src).extractall(root)
    for dp, _, files in os.walk(root):
        if "TotalAcceleration.csv" in files and "__MACOSX" not in dp:
            return dp
    sys.exit("TotalAcceleration.csv not found. In Sensor Logger, turn on Accelerometer+Gravity (TotalAcceleration) and Gyroscope.")


def to_dataset(src):
    """Sensor Logger export (zip/folder) -> (t, a_in_g, w_in_dps, speed) arrays."""
    folder = find_folder(src)
    acc = read(folder, "TotalAcceleration.csv")
    gyr = read(folder, "Gyroscope.csv")
    loc = read(folder, "Location.csv")
    if gyr is None:
        sys.exit("Gyroscope.csv missing - turn the gyroscope on in Sensor Logger.")
    t = acc["seconds_elapsed"]
    a = np.column_stack([acc[c] / G for c in ("x", "y", "z")])
    w = np.column_stack([np.degrees(np.interp(t, gyr["seconds_elapsed"], gyr[c])) for c in ("x", "y", "z")])
    speed = np.full(len(t), np.nan)
    if loc is not None and len(np.atleast_1d(loc)) > 0:
        loc = np.atleast_1d(loc)
        idx = np.searchsorted(t, loc["seconds_elapsed"]).clip(0, len(t) - 1)
        speed[idx] = loc["speed"]
    return t, a, w, speed


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    t, a, w, speed = to_dataset(sys.argv[1])
    if np.all(np.isnan(speed)):
        print("warning: no GPS fixes in this recording - braking/near-miss alerts need GPS speed")
    (ax, ay, az), (gx, gy, gz) = a.T, w.T
    out = np.column_stack([t, ax, ay, az, gx, gy, gz, speed])
    np.savetxt(sys.argv[2], out, delimiter=",", fmt="%.5f",
               header="t,ax,ay,az,gx,gy,gz,speed", comments="")
    dur = t[-1] - t[0]
    print(f"wrote {sys.argv[2]}: {len(t)} samples, {dur:.1f} s, ~{len(t)/dur:.0f} Hz, "
          f"{int(np.sum(~np.isnan(speed)))} GPS fixes")
    print(f"|a| median {np.median(np.linalg.norm(a, axis=1)):.2f} g "
          f"(should be ~1.0 when not moving), peak gyro {np.abs(w).max():.0f} deg/s")


if __name__ == "__main__":
    main()
