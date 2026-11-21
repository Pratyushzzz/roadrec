"""Plain-language case summary built from stored facts only (no generated text, no fault finding)."""
PLAIN = {
    "CRASH": "a high-energy impact followed by the vehicle being down or stopped",
    "FALL": "a low-energy tip-over: orientation changed and stayed changed",
    "NEAR_MISS": "hard braking with a swerve or very hard deceleration, ride continued",
    "HARD_BRAKE": "sustained hard braking without a swerve",
    "DROP": "free-fall while the vehicle was not moving (device dropped or handled)",
    "POTHOLE": "a short vertical spike, ride continued",
    "MINOR_IMPACT": "an impact while the vehicle stayed upright and moving",
    "HANDLING": "the device being carried or handled, no alert",
    "BUMP": "a minor bump below every event signature",
    "INCOMPLETE": "too little data after the event to judge the outcome",
}


def case_summary(ev, vehicle, intact: bool) -> str:
    f = ev.evidence or {}
    parts = [f"Vehicle {vehicle.reg_no} recorded an event on {ev.recorded_at:%d %b %Y %H:%M} UTC, "
             f"classified {ev.verdict}: {PLAIN.get(ev.verdict, 'see evidence')}."]
    if "peak_g" in f:
        parts.append(f"Peak acceleration {f['peak_g']:.1f} g over {f.get('spike_ms', 0):.0f} ms.")
    post = f.get("post") or {}
    if post.get("ok"):
        parts.append(f"Orientation changed {post['tilt_deg']:.0f} degrees; the device was "
                     f"{'motionless' if post.get('still') else 'still moving'} afterwards.")
    sp = f.get("speed_pre")
    parts.append(f"Speed before the event: {sp * 3.6:.0f} km/h." if sp is not None else "No GPS speed was recorded.")
    if ev.reasons:
        parts.append("Rule evidence: " + "; ".join(ev.reasons) + ".")
    n = len(ev.detections or [])
    if n > 1:
        parts.append(f"{n} events were found in this file; the most serious is shown.")
    parts.append("File integrity: " + ("the stored file still matches its upload fingerprint." if intact
                 else "WARNING - the stored file no longer matches its upload fingerprint."))
    parts.append({"match": "The fingerprint supplied by the uploader matched the server's own.",
                  "mismatch": "WARNING - the fingerprint supplied by the uploader did NOT match the server's own.",
                  "not_provided": "No device fingerprint was supplied, so the file was not compared with the recorder's own."}[ev.hash_status])
    parts.append("This is a reading for investigation, not a legal finding about fault.")
    return " ".join(parts)
