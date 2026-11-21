#!/usr/bin/env python3
"""
roadrec/classifier.py - explainable (physics-rule) road event classifier.

Input CSV columns (header required, names case-sensitive):
    t, ax, ay, az, gx, gy, gz[, speed]
  t      seconds (any rate; resampled to 100 Hz)
  ax..az acceleration, in g (default) or m/s^2 (--units ms2)
  gx..gz angular rate in deg/s
  speed  optional GPS speed in m/s (blank / NaN where unknown)

Works on phone-recorded CSVs (Sensor Logger / Phyphox, renamed columns) today,
and on IMU logs from the XIAO later. All thresholds live in CFG so they can be
tuned against REAL recorded events.

Usage:
    python classifier.py ride.csv --out out/
"""
import argparse, json, os, sys, warnings
import numpy as np

warnings.filterwarnings("ignore", message="All-NaN slice")

FS = 100  # working sample rate, Hz

CFG = dict(
    # ---- triggers (cheap, causal; firmware-portable) ----
    trig_impact_g=2.0,       # |a - gravity| above this -> impact trigger
    trig_freefall_g=0.50,    # |a| below this ... (a tumbling phone reads 0.3-0.5 g in free-fall, not 0)
    trig_freefall_ms=80,     # ... for this long -> free-fall trigger
    trig_horiz_g=0.30,       # smoothed horizontal accel above this ...
    trig_horiz_ms=300,       # ... for this long -> braking/swerve trigger
    trig_gyro_dps=200,       # total angular rate trigger
    merge_gap_s=2.0,         # triggers closer than this are one event
    gravity_tau_s=8.0,       # slow gravity tracker time constant
    warmup_s=3.0,            # ignore triggers this soon after recording starts (no baseline yet)
    # ---- classification ----
    crash_g=4.0,             # high-energy impact
    pothole_max_ms=100,      # pothole spikes are short
    pothole_vert_frac=0.55,  # and mostly along gravity
    lying_deg=45,            # orientation change that means "down"
    upright_deg=20,
    stopped_mps=1.5,
    still_acc_std_g=0.10,    # post-event stillness
    still_gyro_dps=20,
    drop_freefall_ms=120,
    brake_g=0.35, brake_ms=400,
    swerve_dps=35,
    brake_min_mps=2.0,       # braking/near-miss only counts if the vehicle was moving at least this fast
    drop_quiet_s=15.0,       # after a DROP the device is being handled: ignore events for this long
)


# ----------------------------------------------------------------- loading
def load_csv(path, units="g"):
    raw = np.genfromtxt(path, delimiter=",", names=True, dtype=float)
    names = raw.dtype.names
    need = ["t", "ax", "ay", "az", "gx", "gy", "gz"]
    miss = [n for n in need if n not in names]
    if miss:
        sys.exit(f"CSV is missing columns: {miss}")
    t = raw["t"]
    order = np.argsort(t)
    t = t[order]
    k = 1.0 / 9.80665 if units == "ms2" else 1.0
    a = np.column_stack([raw[c][order] * k for c in ("ax", "ay", "az")])
    g = np.column_stack([raw[c][order] for c in ("gx", "gy", "gz")])
    sp = raw["speed"][order] if "speed" in names else np.full(len(t), np.nan)
    return resample(t, a, g, sp)


def resample(t, a, g, sp):
    tt = np.arange(t[0], t[-1], 1.0 / FS)
    ai = np.column_stack([np.interp(tt, t, a[:, i]) for i in range(3)])
    gi = np.column_stack([np.interp(tt, t, g[:, i]) for i in range(3)])
    # speed: hold last valid value (GPS updates at 1-10 Hz)
    ok = ~np.isnan(sp)
    if ok.any():
        idx = np.searchsorted(t[ok], tt, side="right") - 1
        spi = np.where(idx >= 0, sp[ok][np.clip(idx, 0, None)], np.nan)
    else:
        spi = np.full(len(tt), np.nan)
    return dict(t=tt, a=ai, w=gi, speed=spi)


# --------------------------------------------------------------- utilities
def norm(v):
    return np.linalg.norm(v, axis=-1)


def runs(mask, min_len):
    """Start indices of True-runs of at least min_len samples."""
    m = np.concatenate([[0], mask.astype(np.int8), [0]])
    d = np.diff(m)
    s, e = np.where(d == 1)[0], np.where(d == -1)[0]
    return [(i, j) for i, j in zip(s, e) if j - i >= min_len]


def longest_run(mask):
    r = runs(mask, 1)
    return max((j - i for i, j in r), default=0)


def smooth(x, n):
    k = np.ones(n) / n
    if x.ndim == 1:
        return np.convolve(x, k, mode="same")
    return np.column_stack([np.convolve(x[:, i], k, mode="same") for i in range(x.shape[1])])


def gravity_track(a, tau_s):
    """Slow, gated gravity estimate (only learns when |a|~1 g). Causal."""
    alpha = 1.0 / (tau_s * FS)
    g = np.zeros_like(a)
    cur = np.median(a[: min(len(a), 100)], axis=0)
    for i in range(len(a)):
        m = np.linalg.norm(a[i])
        if 0.9 < m < 1.1:
            cur = cur + alpha * (a[i] - cur)
        g[i] = cur
    return g


def split_vert_horiz(v, ghat):
    vert = np.einsum("ij,ij->i", v, ghat) if v.ndim == 2 else float(v @ ghat)
    if v.ndim == 2:
        hor = norm(v - vert[:, None] * ghat)
    else:
        hor = float(np.linalg.norm(v - vert * ghat))
    return vert, hor


def angle_deg(u, v):
    c = np.dot(u, v) / (np.linalg.norm(u) * np.linalg.norm(v) + 1e-9)
    return float(np.degrees(np.arccos(np.clip(c, -1, 1))))


# ---------------------------------------------------------------- triggers
def find_triggers(d, cfg=CFG):
    a, w = d["a"], d["w"]
    gl = gravity_track(a, cfg["gravity_tau_s"])
    ghat = gl / norm(gl)[:, None]
    dyn = a - gl
    vert, hor = split_vert_horiz(dyn, ghat)
    mag = norm(a)
    adyn = norm(dyn)
    hs = smooth(hor, int(0.1 * FS))

    trig = []
    trig += [i for i in np.where(adyn > cfg["trig_impact_g"])[0]]
    trig += [i for i, _ in runs(mag < cfg["trig_freefall_g"], int(cfg["trig_freefall_ms"] / 1000 * FS))]
    trig += [i for i, _ in runs(hs > cfg["trig_horiz_g"], int(cfg["trig_horiz_ms"] / 1000 * FS))]
    trig += [i for i in np.where(norm(w) > cfg["trig_gyro_dps"])[0]]
    trig = sorted(set(int(i) for i in trig))

    events, last = [], None
    trig = [i for i in trig if i >= cfg["warmup_s"] * FS]
    for i in trig:
        if last is None or i - last > cfg["merge_gap_s"] * FS:
            events.append(i)
        last = i
    return events


# ---------------------------------------------------------------- features
def features(d, i0, cfg=CFG):
    a, w, sp, N = d["a"], d["w"], d["speed"], len(d["t"])
    lo = max(0, i0 - 300)
    hi_pre = max(lo + 1, i0 - 30)
    g_pre = np.median(a[lo:hi_pre], axis=0) if hi_pre - lo >= 30 else a[max(0, i0 - 1)]
    ghat = g_pre / np.linalg.norm(g_pre)

    s0, s1 = max(0, i0 - 10), min(N, i0 + 200)
    dyn = a[s0:s1] - g_pre
    vert, hor = split_vert_horiz(dyn, ghat[None, :].repeat(len(dyn), 0))
    tot = norm(dyn)
    ipk = int(np.argmax(tot))
    peak_g = float(tot[ipk])
    ip = s0 + ipk

    half = peak_g * 0.5
    l = r = ipk
    while l > 0 and tot[l - 1] > half:
        l -= 1
    while r < len(tot) - 1 and tot[r + 1] > half:
        r += 1
    spike_ms = (r - l + 1) * 1000 / FS
    vf = float(np.sum(vert[l:r + 1] ** 2) / (np.sum(tot[l:r + 1] ** 2) + 1e-9))

    w_win = w[s0:min(N, i0 + 300)]
    gyro_peak = float(norm(w_win).max())
    yaw_peak = float(np.abs(w_win @ ghat).max())

    mag = norm(a[max(0, i0 - 30):min(N, i0 + 100)])
    freefall_ms = longest_run(mag < cfg["trig_freefall_g"]) * 1000 / FS

    hs = smooth(split_vert_horiz(a[s0:min(N, i0 + 250)] - g_pre,
                                 ghat[None, :].repeat(len(a[s0:min(N, i0 + 250)]), 0))[1], int(0.1 * FS))
    horiz_peak = float(hs.max())
    horiz_ms = float((hs > cfg["brake_g"]).sum()) * 1000 / FS

    pa, pb = ip + 100, min(N, ip + 300)
    post_ok = pb - pa >= 60
    post = dict(ok=post_ok)
    if post_ok:
        pm = a[pa:pb]
        post["tilt_deg"] = angle_deg(np.median(pm, axis=0), g_pre)
        post["acc_std"] = float(norm(pm).std())
        post["gyro_mean"] = float(norm(w[pa:pb]).mean())
        post["still"] = post["acc_std"] < cfg["still_acc_std_g"] and post["gyro_mean"] < cfg["still_gyro_dps"]
    sp_pre = np.nanmedian(sp[max(0, i0 - 200):max(1, i0 - 20)]) if not np.all(np.isnan(sp)) else np.nan
    sp_post = np.nanmedian(sp[ip + 200:min(N, ip + 400)]) if (ip + 200 < N and not np.all(np.isnan(sp[ip + 200:min(N, ip + 400)]))) else np.nan
    sp_min = np.nanmin(sp[i0:min(N, i0 + 300)]) if not np.all(np.isnan(sp[i0:min(N, i0 + 300)])) else np.nan

    return dict(
        i0=i0, i_peak=ip, g_pre=g_pre.tolist(), peak_g=peak_g, spike_ms=spike_ms,
        vert_frac=vf, gyro_peak=gyro_peak, yaw_peak=yaw_peak, freefall_ms=freefall_ms,
        horiz_peak_g=horiz_peak, horiz_ms=horiz_ms, post=post,
        speed_pre=None if np.isnan(sp_pre) else float(sp_pre),
        speed_post=None if np.isnan(sp_post) else float(sp_post),
        speed_min=None if np.isnan(sp_min) else float(sp_min),
    )


# ------------------------------------------------------------------- rules
def classify(f, cfg=CFG):
    """Ordered physics rules. Returns (label, [reasons])."""
    why = []
    post = f["post"]
    sp_pre, sp_post = f["speed_pre"], f["speed_post"]
    known = sp_pre is not None and sp_post is not None
    # 0. dropped during installation: free-fall, and not riding. Needs no post-event data,
    #    so it is judged before the INCOMPLETE check (a drop recorded right up to the end of the file).
    if f["freefall_ms"] >= cfg["drop_freefall_ms"] and not (sp_pre is not None and sp_pre >= 2.0):
        return "DROP", [f"free-fall {f['freefall_ms']:.0f} ms (>= {cfg['drop_freefall_ms']})",
                        "vehicle was not moving (device handled, not ridden)"]
    if not post["ok"]:
        return "INCOMPLETE", ["not enough data after the event to judge the outcome"]

    lying = post["tilt_deg"] >= cfg["lying_deg"]
    upright = post["tilt_deg"] <= cfg["upright_deg"]
    stopped = (sp_post < cfg["stopped_mps"]) if known else post["still"]
    continued = (sp_pre >= 2 and sp_post >= 0.5 * sp_pre) if known else (not post["still"])
    moving_before = (sp_pre is None) or sp_pre >= 2.0

    # 2. crash: high-energy impact, then the vehicle is down or stopped
    if f["peak_g"] >= cfg["crash_g"] and moving_before and (lying or stopped):
        why = [f"impact {f['peak_g']:.1f} g (>= {cfg['crash_g']})"]
        if lying:
            why.append(f"orientation changed {post['tilt_deg']:.0f} deg (>= {cfg['lying_deg']}): vehicle down")
        if stopped:
            why.append("vehicle stopped / motionless after impact")
        return "CRASH", why

    # 3. fall: orientation lost without a high-energy impact (tip-over, low-side slide)
    if lying and not stopped:
        return "HANDLING", [f"orientation changed {post['tilt_deg']:.0f} deg but the device kept moving afterwards "
                            "(carried / hand-held), not a vehicle lying still - no alert"]
    if lying:
        return "FALL", [f"orientation changed {post['tilt_deg']:.0f} deg and stays there",
                        f"peak only {f['peak_g']:.1f} g (< {cfg['crash_g']}) - low-energy tip-over"]

    # 4. pothole: short, vertical, upright, and the ride continues
    if (f["peak_g"] >= cfg["trig_impact_g"] and f["spike_ms"] <= cfg["pothole_max_ms"]
            and f["vert_frac"] >= cfg["pothole_vert_frac"] and upright and continued):
        return "POTHOLE", [f"spike {f['spike_ms']:.0f} ms (<= {cfg['pothole_max_ms']})",
                           f"{f['vert_frac']*100:.0f}% of energy along gravity",
                           f"orientation unchanged ({post['tilt_deg']:.0f} deg)", "ride continued normally"]

    # 5. braking / near miss: no impact, no orientation change, ride continues
    swerve = f["yaw_peak"] >= cfg["swerve_dps"]
    hard = f["horiz_ms"] >= cfg["brake_ms"] or f["horiz_peak_g"] >= 0.7
    if f["peak_g"] < cfg["crash_g"] and upright and hard and not (sp_pre is not None and sp_pre >= cfg["brake_min_mps"]):
        return "HANDLING", ["hard deceleration/rotation seen, but the vehicle was not confirmed moving "
                            "(no GPS speed, or speed < %.0f m/s) - treated as the device being handled, no alert" % cfg["brake_min_mps"]]
    if f["peak_g"] < cfg["crash_g"] and upright and hard:
        if swerve or f["horiz_peak_g"] >= 0.7:
            return "NEAR_MISS", [f"hard deceleration {f['horiz_peak_g']:.2f} g",
                                 f"combined with swerve (yaw {f['yaw_peak']:.0f} deg/s)" if swerve else "very hard braking >= 0.7 g",
                                 "no impact, orientation unchanged, ride continued"]
        return "HARD_BRAKE", [f"sustained horizontal decel {f['horiz_peak_g']:.2f} g for {f['horiz_ms']:.0f} ms",
                              "no swerve, no impact, orientation unchanged"]

    if f["peak_g"] >= cfg["crash_g"] and continued:
        return "MINOR_IMPACT", [f"{f['peak_g']:.1f} g impact but vehicle upright and still moving - contact/collision without loss of control"]
    return "BUMP", ["trigger below every event signature (speed breaker / rough patch)"]


# ------------------------------------------------------------------ report
def write_report(d, f, label, why, out, n):
    os.makedirs(out, exist_ok=True)
    N = len(d["t"])
    a0, a1 = max(0, f["i0"] - 10 * FS), min(N, f["i0"] + 5 * FS)  # 10 s before, 5 s after
    t = d["t"][a0:a1] - d["t"][f["i0"]]
    a, w, sp = d["a"][a0:a1], d["w"][a0:a1], d["speed"][a0:a1]
    g_pre = np.array(f["g_pre"])
    ghat = g_pre / np.linalg.norm(g_pre)
    vert, hor = split_vert_horiz(a - g_pre, ghat[None, :].repeat(len(a), 0))
    am = smooth(a, 20)
    tilt = np.array([angle_deg(am[i], g_pre) for i in range(len(am))])

    base = os.path.join(out, f"event_{n:02d}_{label}")
    np.savetxt(base + ".csv", np.column_stack([t, a, w, sp, tilt]), delimiter=",", fmt="%.4f",
               header="t_rel_s,ax,ay,az,gx,gy,gz,speed_mps,tilt_deg", comments="")
    with open(base + ".json", "w") as fh:
        json.dump(dict(label=label, reasons=why, features={k: v for k, v in f.items() if k != "g_pre"}), fh, indent=2, default=float)

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(4, 1, figsize=(9, 9), sharex=True)
        ax[0].plot(t, norm(a), label="|a|", lw=1)
        ax[0].plot(t, hor, label="horizontal", lw=.8)
        ax[0].plot(t, vert, label="vertical", lw=.8)
        ax[0].set_ylabel("accel (g)"); ax[0].legend(loc="upper left", ncol=3)
        ax[1].plot(t, norm(w), color="C3"); ax[1].set_ylabel("rotation (deg/s)")
        ax[2].plot(t, sp * 3.6, color="C2"); ax[2].set_ylabel("GPS speed (km/h)")
        ax[3].plot(t, tilt, color="C4"); ax[3].set_ylabel("orientation change (deg)")
        ax[3].set_xlabel("seconds relative to trigger")
        for x in ax:
            x.axvline(0, color="k", ls=":", lw=.8); x.grid(alpha=.3)
        fig.suptitle(f"Event {n}: {label}\n" + "; ".join(why), fontsize=9)
        fig.tight_layout(); fig.savefig(base + ".png", dpi=110); plt.close(fig)
    except ImportError:
        pass

    sp_txt = lambda v: "n/a" if v is None else f"{v*3.6:.0f} km/h"
    with open(base + ".txt", "w") as fh:
        fh.write(f"EVENT {n}: {label}\n")
        fh.write(f"time in file : {d['t'][f['i0']]:.2f} s\n")
        fh.write("why          : " + "; ".join(why) + "\n")
        fh.write(f"peak accel   : {f['peak_g']:.1f} g over {f['spike_ms']:.0f} ms\n")
        fh.write(f"peak rotation: {f['gyro_peak']:.0f} deg/s\n")
        fh.write(f"speed before : {sp_txt(f['speed_pre'])}   after: {sp_txt(f['speed_post'])}\n")
        if f["post"]["ok"]:
            fh.write(f"orientation  : changed {f['post']['tilt_deg']:.0f} deg; "
                     f"{'motionless' if f['post']['still'] else 'moving'} afterwards\n")


def analyse(d, cfg=CFG):
    out, quiet_until = [], -1
    for i0 in find_triggers(d, cfg):
        if i0 < quiet_until:
            continue  # device is still being handled after a drop (picked up, turned over)
        f = features(d, i0, cfg)
        label, why = classify(f, cfg)
        if label == "DROP":
            quiet_until = f["i_peak"] + int(cfg["drop_quiet_s"] * FS)
        out.append((f, label, why))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("--out", default="out")
    ap.add_argument("--units", choices=["g", "ms2"], default="g")
    args = ap.parse_args()
    d = load_csv(args.csv, args.units)
    hours = (d["t"][-1] - d["t"][0]) / 3600
    res = analyse(d)
    print(f"{hours*60:.1f} min of data, {len(res)} triggers")
    alerts = 0
    for n, (f, label, why) in enumerate(res, 1):
        print(f"  [{d['t'][f['i0']]:8.2f}s] {label:<12} {why[0]}")
        write_report(d, f, label, why, args.out, n)
        alerts += label in ("CRASH", "FALL", "NEAR_MISS")
    print(f"alert-class events (crash/fall/near-miss): {alerts}  ->  {alerts/max(hours,1e-9):.1f} per hour")


if __name__ == "__main__":
    main()
