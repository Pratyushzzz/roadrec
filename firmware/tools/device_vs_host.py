#!/usr/bin/env python3
"""Compare what the BOARD decided during a replay test with what the laptop decides.

  1. On the board, REPLAY_TEST ran; copy /replay_results.txt from the SD card to the Mac.
  2. Run this with that file and the folder holding the same 100 Hz CSVs you put on the card:

       python3 tools/device_vs_host.py ~/Desktop/replay_results.txt ~/Desktop/replay

  (build host/replay first:  g++ -O2 -std=c++17 -Ilib/roadcore host/replay.cpp lib/roadcore/roadcore.cpp -o host/replay)
"""
import os, subprocess, sys

here = os.path.dirname(os.path.abspath(__file__))
replay = os.environ.get("REPLAY", os.path.join(here, "..", "host", "replay"))
if len(sys.argv) < 3:
    sys.exit(__doc__)
results, folder = sys.argv[1], sys.argv[2]

device = {}
for line in open(results):
    line = line.rstrip("\n")
    if ": " in line or line.endswith(":"):
        name, _, labels = line.partition(":")
        device[name.strip()] = [l for l in labels.strip().split(",") if l]

same = total = 0
for name in sorted(device):
    path = os.path.join(folder, name)
    if not os.path.exists(path):
        print(f"{name:<28} (no CSV with that name in {folder})")
        continue
    out = subprocess.run([replay, path], capture_output=True, text=True).stdout.strip().splitlines()
    host = [l.split()[1] for l in out]
    ok = host == device[name]
    same += ok
    total += 1
    print(f"{name:<28} board={','.join(device[name]) or '-':<34} laptop={','.join(host) or '-':<34} {'match' if ok else 'DIFF'}")
print(f"\n{same}/{total} files: board and laptop decided exactly the same")
