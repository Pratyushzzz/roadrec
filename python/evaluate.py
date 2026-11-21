#!/usr/bin/env python3
"""
roadrec/evaluate.py - score the classifier on ALL your recordings at once.

Put every Sensor Logger ZIP (or converted .csv) in one folder and name each
file so it STARTS with what you did. The true label is read from the name:

    drop...            -> DROP          tip... / fall...   -> FALL
    crash...           -> CRASH         brake...           -> HARD_BRAKE
    swerve... / near.. -> NEAR_MISS     pothole...         -> POTHOLE
    bump... / normal.. / ride... / N_... -> NO_ALERT (nothing alarming should fire)
    T_O_...            -> FALL (tip-over)

    python3 evaluate.py            # scans the current folder
    python3 evaluate.py path/to/folder

A file is CORRECT if the expected event is found and no other alert-class
event (CRASH/FALL/NEAR_MISS/DROP) fires. For NO_ALERT files, correct means
zero alert-class events. Reasons are printed for every miss.
"""
import os, sys, glob
import numpy as np
import classifier as C
import convert as V

KEYS = [("drop", "DROP"), ("tip", "FALL"), ("fall", "FALL"), ("crash", "CRASH"),
        ("brake", "HARD_BRAKE"), ("swerve", "NEAR_MISS"), ("near", "NEAR_MISS"),
        ("pothole", "POTHOLE"), ("bump", "NO_ALERT"), ("normal", "NO_ALERT"), ("ride", "NO_ALERT"),
        ("n_", "NO_ALERT"), ("t_o", "FALL")]
ALERTS = {"CRASH", "FALL", "NEAR_MISS", "DROP"}


def true_label(name):
    n = name.lower()
    for k, lab in KEYS:
        if n.startswith(k):
            return lab
    return None


def load(path):
    if path.endswith(".zip"):
        t, a, w, sp = V.to_dataset(path)
        return C.resample(t, a, w, sp)
    return C.load_csv(path)


def main():
    folder = sys.argv[1] if len(sys.argv) > 1 else "."
    files = sorted(glob.glob(os.path.join(folder, "*.zip")) + glob.glob(os.path.join(folder, "*.csv")))
    rows, hours, false_alerts = [], 0.0, 0
    for f in files:
        name = os.path.basename(f)
        truth = true_label(name)
        if truth is None:
            continue
        try:
            d = load(f)
        except Exception as e:
            print(f"skipped {name}: {e}")
            continue

        res = C.analyse(d)
        labels = [r[1] for r in res]
        found = [l for l in labels if l in ALERTS | {"HARD_BRAKE", "POTHOLE"}]
        alerts = [l for l in labels if l in ALERTS]
        if truth == "NO_ALERT":
            ok = not alerts
            false_alerts += len(alerts)
        else:
            extra = [l for l in alerts if l != truth]
            ok = truth in labels and not extra
        hours += (d["t"][-1] - d["t"][0]) / 3600
        rows.append((name, truth, labels, ok, res))
    if not rows:
        sys.exit("No recordings with recognisable names found (see the label rules at the top of evaluate.py).")

    print(f"{'file':<44}{'expected':<12}{'got':<28}ok")
    for name, truth, labels, ok, _ in rows:
        got = ",".join(labels) if labels else "(no event)"
        print(f"{name[:42]:<44}{truth:<12}{got[:26]:<28}{'yes' if ok else 'NO'}")
    n_ok = sum(r[3] for r in rows)
    print(f"\n{n_ok}/{len(rows)} recordings correct ({n_ok/len(rows)*100:.0f}%)")
    print(f"false alert-class events in NO_ALERT files: {false_alerts} over {hours*60:.1f} min of data")
    for name, truth, labels, ok, res in rows:
        if not ok:
            print(f"\nMISS {name} (expected {truth}):")
            for f, lab, why in res:
                print(f"   {lab}: {'; '.join(why)}")


if __name__ == "__main__":
    main()
