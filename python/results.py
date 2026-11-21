#!/usr/bin/env python3
"""
roadrec/results.py - the results table for the submission.

Scores a TUNING folder and a TEST folder separately, using exactly the same rules as
evaluate.py (file name = true label), and writes one table that keeps them apart.

    python3 results.py --tuning recordings --test recordings_test2
    python3 results.py --tuning recordings --test recordings_test2 --ride ride_logs
    python3 results.py --tuning recordings            # test set not recorded yet

  --tuning  the recordings you looked at / tuned thresholds on
  --test    recordings made AFTER the code was frozen. This is the honest number.
  --ride    optional: folder of long, ordinary-riding logs. Every alert-class event in
            them is a false alarm; reported as events per hour.

Writes results.md next to this script (or --out FILE) and prints it.
Put results.py in the same folder as evaluate.py, classifier.py and convert.py.
"""
import argparse, glob, math, os, sys
from collections import OrderedDict

import classifier as C
import evaluate as E

ALERTS = E.ALERTS
ORDER = ["DROP", "FALL", "CRASH", "HARD_BRAKE", "NEAR_MISS", "POTHOLE", "NO_ALERT"]


def wilson(k, n, z=1.96):
    """95% interval for k correct out of n. Honest about small sample sizes."""
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def files_in(folder):
    return sorted(glob.glob(os.path.join(folder, "*.zip")) + glob.glob(os.path.join(folder, "*.csv")))


def score(folder):
    """Returns (rows, skipped, ignored). row = dict(name, truth, labels, ok, minutes, false_alerts)."""
    rows, skipped, ignored = [], [], []
    for f in files_in(folder):
        name = os.path.basename(f)
        truth = E.true_label(name)
        if truth is None:
            ignored.append(name)
            continue
        try:
            d = E.load(f)
            res = C.analyse(d)
        except Exception as e:  # one bad file must not stop the table
            skipped.append((name, str(e)))
            continue
        labels = [r[1] for r in res]
        alerts = [l for l in labels if l in ALERTS]
        if truth == "NO_ALERT":
            ok = not alerts
            false_alerts = len(alerts)
        else:
            ok = truth in labels and not [l for l in alerts if l != truth]
            false_alerts = len([l for l in alerts if l != truth])   # wrong alert-class events
        minutes = (d["t"][-1] - d["t"][0]) / 60.0
        rows.append(dict(name=name, truth=truth, labels=labels, ok=ok, minutes=minutes,
                         false_alerts=false_alerts))
    return rows, skipped, ignored


def ride_score(folder):
    """Long ordinary-riding logs: every alert-class event is a false alarm."""
    minutes, alerts, per_file, skipped = 0.0, 0, [], []
    for f in files_in(folder):
        name = os.path.basename(f)
        try:
            d = E.load(f)
            res = C.analyse(d)
        except Exception as e:
            skipped.append((name, str(e)))
            continue
        m = (d["t"][-1] - d["t"][0]) / 60.0
        a = [(r[1]) for r in res if r[1] in ALERTS]
        minutes += m
        alerts += len(a)
        per_file.append((name, m, a))
    return minutes, alerts, per_file, skipped


def table_for(rows):
    by = OrderedDict((k, [0, 0]) for k in ORDER)   # truth -> [correct, total]
    for r in rows:
        by.setdefault(r["truth"], [0, 0])
        by[r["truth"]][1] += 1
        by[r["truth"]][0] += r["ok"]
    out = ["| Event type | Files | Correct | Accuracy | 95% interval |", "|---|---:|---:|---:|---|"]
    for k, (c, n) in by.items():
        if n == 0:
            continue
        lo, hi = wilson(c, n)
        out.append(f"| {k} | {n} | {c} | {c / n * 100:.0f}% | {lo * 100:.0f}-{hi * 100:.0f}% |")
    c = sum(r["ok"] for r in rows)
    n = len(rows)
    lo, hi = wilson(c, n)
    out.append(f"| **All** | **{n}** | **{c}** | **{(c / n * 100 if n else 0):.0f}%** | **{lo * 100:.0f}-{hi * 100:.0f}%** |")
    return "\n".join(out)


def misses_for(rows):
    bad = [r for r in rows if not r["ok"]]
    if not bad:
        return "No misses."
    out = ["| File | Expected | Got |", "|---|---|---|"]
    for r in bad:
        out.append(f"| {r['name'][:46]} | {r['truth']} | {', '.join(r['labels']) or '(no event)'} |"[:230])
    return "\n".join(out)


def false_rate(rows):
    quiet = [r for r in rows if r["truth"] == "NO_ALERT"]
    mins = sum(r["minutes"] for r in quiet)
    n = sum(r["false_alerts"] for r in quiet)
    return n, mins


def section(title, note, folder):
    rows, skipped, ignored = score(folder)
    out = [f"## {title}", "", f"_{note}_", "", f"Folder: `{folder}`", ""]
    if not rows:
        out.append("No recordings with recognisable names found.")
        return "\n".join(out), rows
    out += [table_for(rows), ""]
    n, mins = false_rate(rows)
    if mins > 0:
        out.append(f"False alert-class events in NO_ALERT files: **{n}** over {mins:.1f} min of data "
                   f"(too short to quote as a per-hour rate; use the ride log for that).")
        out.append("")
    out += ["Misses:", "", misses_for(rows), ""]
    if skipped:
        out += ["Skipped (could not be read): " + "; ".join(f"{a} ({b})" for a, b in skipped), ""]
    if ignored:
        out += [f"Ignored (name does not start with a known label): {len(ignored)} file(s)", ""]
    return "\n".join(out), rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tuning", required=True)
    ap.add_argument("--test")
    ap.add_argument("--ride")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "results.md"))
    a = ap.parse_args()

    parts = ["# RoadRec results", ""]
    warnings = []

    tun_text, tun_rows = section(
        "Tuning set",
        "Thresholds and rules were adjusted while looking at these files. NOT an honest accuracy.",
        a.tuning)
    parts += [tun_text, ""]

    if a.test:
        test_text, test_rows = section(
            "Test set",
            "Recorded after the code was frozen. This is the accuracy to quote.",
            a.test)
        parts += [test_text, ""]
        shared = {r["name"] for r in tun_rows} & {r["name"] for r in test_rows}
        if shared:
            warnings.append(f"{len(shared)} file name(s) appear in both tuning and test folders: "
                            + ", ".join(sorted(shared)[:5]) + ". Test files must be new recordings.")
    else:
        parts += ["## Test set", "", "_Not recorded yet. Record it, freeze the code, then run this again with `--test`._", ""]

    if a.ride:
        mins, alerts, per_file, skipped = ride_score(a.ride)
        parts += ["## False-trigger rate (ordinary riding)", "", f"Folder: `{a.ride}`", ""]
        if mins <= 0:
            parts.append("No readable ride logs found.")
        else:
            hours = mins / 60.0
            parts.append(f"**{alerts}** alert-class events (CRASH, FALL, NEAR_MISS, DROP) over **{mins:.1f} min** "
                         f"= **{alerts / hours:.1f} per hour**. In ordinary riding every one of these is a false alarm.")
            if mins < 55:
                warnings.append(f"Ride data is only {mins:.0f} min; the brief asks for an hour of ordinary riding.")
            parts += ["", "| Log | Minutes | Alert-class events |", "|---|---:|---|"]
            for name, m, al in per_file:
                parts.append(f"| {name[:46]} | {m:.1f} | {', '.join(al) or '-'} |")
        if skipped:
            parts += ["", "Skipped: " + "; ".join(f"{x} ({y})" for x, y in skipped)]
        parts.append("")
    else:
        parts += ["## False-trigger rate (ordinary riding)", "",
                  "_Not measured yet. Log an hour of ordinary riding and run with `--ride FOLDER`._", ""]

    if warnings:
        parts = parts[:2] + ["> **Warnings**", ">"] + [f"> - {w}" for w in warnings] + [""] + parts[2:]

    text = "\n".join(parts)
    print(text)
    with open(a.out, "w") as fh:
        fh.write(text + "\n")
    print(f"\n(written to {a.out})")


if __name__ == "__main__":
    main()
