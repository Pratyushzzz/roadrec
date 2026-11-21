#!/usr/bin/env python3
"""
roadrec/synth.py - SYNTHETIC test data + self-test for classifier.py.

This is only a development scaffold so the pipeline can be debugged without
hardware. It proves the code runs, NOT that the thresholds are right: the
judges want accuracy on REAL recorded events. Replace with phone/IMU logs.

    python synth.py --csv demo.csv      # write one demo ride with events
    python synth.py --test              # confusion matrix + false-trigger test
"""
import argparse
import numpy as np
import classifier as C

FS = C.FS


def base_ride(n, rng, speed=10.0):
    t = np.arange(n) / FS
    a = np.tile([0, 0, 1.0], (n, 1)) + rng.normal(0, 0.03, (n, 3))
    a[:, 2] += 0.05 * np.sin(2 * np.pi * 25 * t) + 0.03 * np.sin(2 * np.pi * 9 * t)
    w = rng.normal(0, 2.0, (n, 3)) + 3 * np.sin(2 * np.pi * 0.4 * t)[:, None]
    sp = speed + 0.5 * np.sin(2 * np.pi * 0.05 * t) + rng.normal(0, 0.1, n)
    return t, a, w, sp


def tilt_to(a, w, i0, dur, deg, rng):
    """Rotate gravity about the x (roll) axis from 0 to `deg` starting at i0 and stay there."""
    n = len(a)
    th = np.zeros(n)
    k = int(dur * FS)
    th[i0:i0 + k] = np.linspace(0, np.radians(deg), k)
    th[i0 + k:] = np.radians(deg)
    a[:, 1] += np.sin(th)
    a[:, 2] += np.cos(th) - 1.0
    w[i0:i0 + k, 0] += np.degrees(np.radians(deg) / dur)


def make(kind, rng, secs=30):
    n = secs * FS
    t, a, w, sp = base_ride(n, rng, speed=rng.uniform(6, 16))
    i = 15 * FS  # event instant
    v0 = sp[i]
    if kind == "POTHOLE":
        amp, dur = rng.uniform(3.0, 6.5), int(rng.uniform(0.025, 0.07) * FS)
        a[i:i + dur, 2] += amp * np.sin(np.linspace(0, np.pi, dur))
        osc = np.arange(40)
        a[i + dur:i + dur + 40, 2] += 0.8 * np.exp(-osc / 10) * np.sin(osc * 1.3)
        w[i:i + 15, 1] += rng.uniform(40, 120) * np.sin(np.linspace(0, np.pi, 15))
    elif kind == "BUMP":   # speed breaker: long, soft
        d = int(0.2 * FS)
        a[i:i + d, 2] += rng.uniform(0.8, 1.4) * np.sin(np.linspace(0, np.pi, d))
    elif kind == "HARD_BRAKE":
        d = int(rng.uniform(1.0, 2.0) * FS)
        g = rng.uniform(0.38, 0.55)
        a[i:i + d, 0] -= g
        sp[i:i + d] -= np.linspace(0, g * 9.8 * d / FS, d)
        sp[i + d:] -= g * 9.8 * d / FS
        sp[:] = np.maximum(sp, 1.5)
        a[i:i + d, 2] += 0.0
    elif kind == "NEAR_MISS":
        d = int(rng.uniform(0.8, 1.2) * FS)
        g = rng.uniform(0.45, 0.65)
        a[i:i + d, 0] -= g
        a[i:i + d, 1] += 0.4 * np.sin(np.linspace(0, 2 * np.pi, d))
        w[i:i + d, 2] += rng.uniform(55, 100) * np.sin(np.linspace(0, 2 * np.pi, d))
        sp[i:] -= g * 9.8 * d / FS * 0.8
        sp[:] = np.maximum(sp, 2.5)
    elif kind == "CRASH":
        dur = int(rng.uniform(0.03, 0.08) * FS)
        a[i:i + dur, 0] -= rng.uniform(6, 14) * np.sin(np.linspace(0, np.pi, dur))
        tilt_to(a, w, i + dur, rng.uniform(0.4, 0.9), rng.uniform(70, 110), rng)
        w[i + dur:i + dur + 80] += rng.normal(0, 200, (80, 3))
        a[i + dur:i + dur + 60] += rng.normal(0, 1.5, (60, 3))
        k = int(1.2 * FS)
        sp[i:i + k] = np.linspace(v0, 0, k); sp[i + k:] = 0
        w[i + 3 * FS:] = rng.normal(0, 1.0, (n - i - 3 * FS, 3))
        a[i + 3 * FS:, 2] -= 0.05 * np.sin(2 * np.pi * 25 * t[i + 3 * FS:])  # engine off
    elif kind == "FALL":
        sp[:] = rng.uniform(2.5, 4.5)
        k = int(1.0 * FS)
        sp[i:i + k] = np.linspace(sp[i], 0, k); sp[i + k:] = 0
        tilt_to(a, w, i, rng.uniform(0.6, 1.0), rng.uniform(75, 100), rng)
        a[i + 45:i + 52, 1] += rng.uniform(1.5, 2.8)
        w[i + 60:] = rng.normal(0, 1.0, (n - i - 60, 3))
        a[i + 3 * FS:, 2] -= 0.05 * np.sin(2 * np.pi * 25 * t[i + 3 * FS:])
    elif kind == "DROP":
        sp[:] = 0.0
        a[:i] = np.array([0, 0, 1.0]) + rng.normal(0, 0.01, (i, 3))
        a[:i] += 0.0
        w[:i] = rng.normal(0, 1, (i, 3))
        ff = int(rng.uniform(0.25, 0.45) * FS)
        a[i:i + ff] = rng.normal(0, 0.03, (ff, 3))
        a[i + ff:i + ff + 2] += np.array([0, 0, rng.uniform(8, 15)])
        a[i + ff + 2:i + ff + 25] += rng.normal(0, 1.0, (23, 3)) * np.exp(-np.arange(23) / 6)[:, None]
        w[i:i + ff + 25] += rng.normal(0, 120, (ff + 25, 3))
        a[i + ff + 25:] = np.array([0.1, 0.95, 0.2]) + rng.normal(0, 0.01, (n - i - ff - 25, 3))
        w[i + ff + 25:] = rng.normal(0, 0.8, (n - i - ff - 25, 3))
    elif kind == "RIDE":
        pass
    return dict(t=t, a=a, w=w, speed=sp)


def ordinary_hour(rng, minutes=60):
    """Ordinary riding: random potholes/speed breakers/braking, no incidents."""
    secs = minutes * 60
    n = secs * FS
    t, a, w, sp = base_ride(n, rng, speed=9)
    # a rough-road vibration texture
    a[:, 2] += rng.normal(0, 0.06, n)
    for _ in range(int(secs / 20)):  # a pothole every ~20 s
        i = rng.integers(500, n - 600)
        dur = int(rng.uniform(0.025, 0.07) * FS)
        a[i:i + dur, 2] += rng.uniform(2.5, 6.0) * np.sin(np.linspace(0, np.pi, dur))
        w[i:i + 15, 1] += rng.uniform(30, 100) * np.sin(np.linspace(0, np.pi, 15))
    for _ in range(int(secs / 40)):  # speed breakers
        i = rng.integers(500, n - 600)
        d = int(0.25 * FS)
        a[i:i + d, 2] += rng.uniform(0.6, 1.4) * np.sin(np.linspace(0, np.pi, d))
    for _ in range(int(secs / 90)):  # ordinary braking at junctions
        i = rng.integers(500, n - 600)
        d = int(rng.uniform(0.8, 1.6) * FS)
        a[i:i + d, 0] -= rng.uniform(0.15, 0.28)
    return dict(t=t, a=a, w=w, speed=np.maximum(sp, 0))


def save_csv(d, path):
    sp = d["speed"].copy()
    sp[np.arange(len(sp)) % 10 != 0] = np.nan  # GPS at 10 Hz
    arr = np.column_stack([d["t"], d["a"], d["w"], sp])
    np.savetxt(path, arr, delimiter=",", fmt="%.4f", header="t,ax,ay,az,gx,gy,gz,speed", comments="")


def thin_speed(d):
    d = dict(d)
    sp = d["speed"].copy()
    sp[np.arange(len(sp)) % 10 != 0] = np.nan
    ok = ~np.isnan(sp)
    idx = np.searchsorted(np.where(ok)[0], np.arange(len(sp)), side="right") - 1
    d["speed"] = sp[ok][np.clip(idx, 0, None)]
    return d


def selftest(trials=30, seed=1):
    rng = np.random.default_rng(seed)
    kinds = ["POTHOLE", "BUMP", "HARD_BRAKE", "NEAR_MISS", "CRASH", "FALL", "DROP"]
    labels = kinds + ["MINOR_IMPACT", "INCOMPLETE", "NONE"]
    conf = {k: {l: 0 for l in labels} for k in kinds}
    for k in kinds:
        for _ in range(trials):
            d = thin_speed(make(k, rng))
            res = C.analyse(d)
            # choose the event nearest to t=15 s
            res = [r for r in res if abs(d["t"][r[0]["i0"]] - 15) < 3]
            lab = res[0][1] if res else "NONE"
            conf[k][lab] += 1
    print("\nrows = true event, columns = predicted (SYNTHETIC data, %d trials each)" % trials)
    cols = [l for l in labels if any(conf[k][l] for k in kinds)]
    print(f"{'':<12}" + "".join(f"{c[:10]:>11}" for c in cols))
    ok = tot = 0
    for k in kinds:
        print(f"{k:<12}" + "".join(f"{conf[k][c]:>11}" for c in cols))
        ok += conf[k].get(k, 0); tot += trials
    # BUMP is allowed to be NONE (below trigger) - that is a correct 'no event'
    ok += sum(conf["BUMP"]["NONE"] for _ in [0])
    print(f"overall accuracy: {ok/tot*100:.1f}%  (BUMP->NONE counted correct)")

    d = thin_speed(ordinary_hour(rng))
    res = C.analyse(d)
    from collections import Counter
    cnt = Counter(r[1] for r in res)
    alerts = sum(cnt[x] for x in ("CRASH", "FALL", "NEAR_MISS"))
    print(f"\n1 h synthetic ordinary riding: {len(res)} triggers -> {dict(cnt)}")
    print(f"false alert-class events (crash/fall/near-miss): {alerts}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true")
    ap.add_argument("--csv")
    a = ap.parse_args()
    if a.csv:
        rng = np.random.default_rng(7)
        parts = [make(k, rng, 30) for k in ("POTHOLE", "HARD_BRAKE", "CRASH")]
        t0, out = 0.0, {k: [] for k in ("t", "a", "w", "speed")}
        for p in parts:
            out["t"].append(p["t"] + t0); t0 += p["t"][-1] + 1 / FS
            for k in ("a", "w", "speed"):
                out[k].append(p[k])
        d = {k: np.concatenate(v) for k, v in out.items()}
        save_csv(d, a.csv)
        print("wrote", a.csv)
    if a.test or not a.csv:
        selftest()
