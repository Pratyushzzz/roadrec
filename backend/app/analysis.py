"""Runs the existing physics-rule classifier (python/classifier.py) on uploaded bytes."""
import hashlib, os, sys, tempfile
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "python"))
import classifier as C  # noqa: E402

CLASSIFIER_VERSION = "rules-2026-10-04"
# most serious first; the file's headline verdict is the highest-ranked detection
SEVERITY = ["CRASH", "FALL", "NEAR_MISS", "HARD_BRAKE", "DROP", "POTHOLE",
            "MINOR_IMPACT", "HANDLING", "BUMP", "INCOMPLETE"]


class AnalysisError(Exception):
    pass


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def jsonable(o):
    if isinstance(o, dict):
        return {k: jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [jsonable(v) for v in o]
    if isinstance(o, np.generic):
        return o.item()
    if isinstance(o, float) and (o != o):
        return None
    return o


def classify_bytes(data: bytes) -> dict:
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as fh:
        fh.write(data)
        path = fh.name
    try:
        d = C.load_csv(path)
        results = C.analyse(d)
    except SystemExit as e:           # classifier.load_csv exits on a bad header
        raise AnalysisError(str(e)) from None
    except Exception as e:
        raise AnalysisError(f"Could not read this file as an event CSV: {e}") from None
    finally:
        os.unlink(path)

    detections = []
    for f, label, why in results:
        detections.append(dict(label=label, reasons=why, time_s=float(d["t"][f["i0"]]),
                               features=jsonable({k: v for k, v in f.items()})))
    if not detections:
        detections = [dict(label="BUMP", reasons=["no trigger fired in this file"], time_s=0.0, features={})]
    detections.sort(key=lambda x: SEVERITY.index(x["label"]) if x["label"] in SEVERITY else 99)
    top = detections[0]
    return dict(verdict=top["label"], reasons=top["reasons"], evidence=top["features"], detections=detections)
