#!/usr/bin/env python3
"""Export a Sensor Logger ZIP (or classifier CSV) as a 100 Hz CSV for host/replay.

    python3 tools/export_resampled.py recording.zip out.csv
"""
import os, sys
import numpy as np
_here = os.path.dirname(os.path.abspath(__file__))
for _p in (os.environ.get("ROADREC_PY", ""),
           os.path.join(_here, "..", "..", "python"),
           os.path.join(_here, "..", "..", "roadRec"),
           os.path.join(_here, "..", "..", "roadrec")):   # folder holding classifier.py and convert.py
    if not _p:
        continue
    if os.path.exists(os.path.join(_p, "classifier.py")):
        sys.path.insert(0, _p)
        break
import classifier as C
import convert as V

src, dst = sys.argv[1], sys.argv[2]
if src.endswith(".zip"):
    t, a, w, sp = V.to_dataset(src)
    d = C.resample(t, a, w, sp)
else:
    d = C.load_csv(src)
arr = np.column_stack([d["t"], d["a"], d["w"], d["speed"]])
np.savetxt(dst, arr, delimiter=",", fmt="%.5f", header="t,ax,ay,az,gx,gy,gz,speed", comments="")
