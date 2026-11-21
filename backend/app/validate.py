"""Checks an uploaded file before it reaches the classifier, with clear messages."""
import csv, io, math, os
from .errors import err

MAX_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", 5 * 1024 * 1024))
MIN_SECONDS = float(os.getenv("MIN_EVENT_SECONDS", 5))   # the classifier ignores the first 3 s (warm-up)
MIN_ROWS = 200
REQUIRED = ["t", "ax", "ay", "az", "gx", "gy", "gz"]


def check_event_csv(data: bytes) -> None:
    if len(data) == 0:
        raise err(422, "empty_file", "The file is empty")
    if b"\x00" in data[:4096]:
        raise err(422, "not_text", "This is a binary file, not an event CSV. Upload the evt_NNNN_*.csv from the SD card")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise err(422, "not_text", "The file is not valid UTF-8 text, so it cannot be an event CSV")
    rows = list(csv.reader(io.StringIO(text)))
    head = [c.strip() for c in rows[0]] if rows else []
    miss = [c for c in REQUIRED if c not in head]
    if miss:
        raise err(422, "bad_header", f"Missing columns: {', '.join(miss)}. The first line must be t,ax,ay,az,gx,gy,gz[,speed]")
    ix = [head.index(c) for c in REQUIRED]
    ts = []
    for n, r in enumerate(rows[1:], start=2):
        if not r or all(not c.strip() for c in r):
            continue
        try:
            vals = [float(r[i]) for i in ix]
        except (ValueError, IndexError):
            raise err(422, "bad_row", f"Line {n} has a missing or non-numeric value. The file looks corrupted or truncated")
        if not all(math.isfinite(v) for v in vals):
            raise err(422, "bad_row", f"Line {n} contains NaN or infinity")
        ts.append(vals[0])
    if len(ts) < MIN_ROWS:
        raise err(422, "too_little_data", f"Only {len(ts)} samples. At least {MIN_ROWS} are needed to judge an event")
    if any(b < a for a, b in zip(ts, ts[1:])):
        raise err(422, "bad_time", "Timestamps go backwards, so the file is out of order or corrupted")
    span = ts[-1] - ts[0]
    if span < MIN_SECONDS:
        raise err(422, "too_little_data", f"Only {span:.1f} s of data. At least {MIN_SECONDS:.0f} s is needed (the detector ignores the first 3 s while it learns gravity)")
