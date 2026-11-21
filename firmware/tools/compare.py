#!/usr/bin/env python3
"""Check that the C++ firmware logic agrees with classifier.py on real recordings.

    python3 tools/compare.py path/to/folder_of_zips
(build host/replay first:  g++ -O2 -std=c++17 -Ilib/roadcore host/replay.cpp lib/roadcore/roadcore.cpp -o host/replay)
"""
import glob, os, subprocess, sys, tempfile
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

here = os.path.dirname(os.path.abspath(__file__))
replay = os.environ.get("REPLAY", os.path.join(here, "..", "host", "replay"))
folder = sys.argv[1] if len(sys.argv) > 1 else "."
files = sorted(glob.glob(os.path.join(folder, "*.zip")))
same = 0
for f in files:
    t, a, w, sp = V.to_dataset(f)
    d = C.resample(t, a, w, sp)
    py = [r[1] for r in C.analyse(d)]
    tmp = tempfile.mktemp(suffix=".csv")
    subprocess.run([sys.executable, os.path.join(here, "export_resampled.py"), f, tmp], check=True)
    out = subprocess.run([replay, tmp], capture_output=True, text=True).stdout.strip().splitlines()
    cpp = [l.split()[1] for l in out]
    ok = py == cpp
    same += ok
    print(f"{os.path.basename(f)[:34]:<36} python={','.join(py) or '-':<26} firmware={','.join(cpp) or '-':<26} {'match' if ok else 'DIFF'}")
    os.remove(tmp)
print(f"\n{same}/{len(files)} recordings give identical event labels")
