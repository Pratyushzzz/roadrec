# Python reference implementation

The laptop-side reference for the physics-rule classifier. The C++ port in `firmware/lib/roadcore` is checked against it.

    pip install -r requirements.txt

| File | Purpose |
|---|---|
| `classifier.py` | The detector and rules. All thresholds are in `CFG`. `python3 classifier.py event.csv --out out/` writes a CSV, JSON, text summary and plot per event |
| `convert.py` | Sensor Logger export (zip or folder) to classifier CSV. Needs `TotalAcceleration.csv`, `Gyroscope.csv`, optional `Location.csv` |
| `evaluate.py` | Scores a folder of recordings. The true label comes from the file name prefix (`drop…`, `crash…`, `tip…`, `brake…`, `N_…` for no alert, etc.) |
| `results.py` | Builds `results.md`, keeping the tuning set and the frozen test set separate, with 95% intervals and an optional false-alarm rate from ride logs |
| `synth.py` | Synthetic data and a self-test (`python3 synth.py --test`). It shows the code runs. It does **not** show the thresholds are right |

Phone recordings are not included in this repo; put them in a local `recordings/` folder (git-ignored).

Typical use:

    python3 convert.py "Test_1-2026-10-03_17-30-22.zip" test1.csv
    python3 classifier.py test1.csv --out out_test1
    python3 results.py --tuning recordings --test recordings_test2
