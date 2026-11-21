import os, sys, glob, hashlib
os.environ["DATABASE_URL"] = "sqlite:///./test_roadrec.db"
os.environ["INVITE_CODE"] = "letmein"
os.environ["MAX_UPLOAD_BYTES"] = str(300 * 1024)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.db import Base, engine, SessionLocal
from app import models

SAMPLES = os.path.join(os.path.dirname(__file__), "..", "..", "samples")


@pytest.fixture()
def c():
    Base.metadata.drop_all(engine); Base.metadata.create_all(engine)
    return TestClient(app)


def sample(name):
    p = glob.glob(os.path.join(SAMPLES, name + "*.csv"))[0]
    return os.path.basename(p), open(p, "rb").read()


def signup(c, email, role="fleet_manager", **kw):
    body = dict(email=email, password="correct-horse", role=role, **kw)
    r = c.post("/auth/register", json=body)
    assert r.status_code == 201, r.text
    return {"Authorization": "Bearer " + r.json()["access_token"]}


def fleet(c, email="fm@x.com"):
    h = signup(c, email)
    vid = c.post("/vehicles", json={"reg_no": "dl1cab1234", "name": "Van 1"}, headers=h).json()["id"]
    return h, vid


def upload(c, h, vid, name, data, **form):
    return c.post("/events", data={"vehicle_id": vid, **form}, files={"file": (name, data)}, headers=h)


# ---- auth ---------------------------------------------------------------
def test_auth_required_and_roles(c):
    assert c.get("/vehicles").status_code == 401
    assert c.post("/auth/register", json=dict(email="i@x.com", password="correct-horse", role="insurer")).status_code == 403
    ins = signup(c, "i@x.com", "insurer", invite_code="letmein")
    assert c.get("/auth/me", headers=ins).json()["role"] == "insurer"
    assert c.get("/vehicles", headers=ins).status_code == 403       # insurers cannot use fleet endpoints
    assert c.post("/auth/login", json=dict(email="i@x.com", password="nope")).status_code == 401
    assert c.post("/auth/login", json=dict(email="I@x.com", password="correct-horse")).status_code == 200
    assert c.post("/auth/register", json=dict(email="i@x.com", password="correct-horse")).status_code == 409


def test_fleet_isolation(c):
    h1, vid = fleet(c, "a@x.com")
    name, data = sample("evt_0001")
    eid = upload(c, h1, vid, name, data).json()["id"]
    h2 = signup(c, "b@x.com")
    assert c.get(f"/events/{eid}", headers=h2).status_code == 404
    assert c.get("/events", headers=h2).json() == []
    assert upload(c, h2, vid, name, data).status_code == 404


# ---- main journey --------------------------------------------------------
def test_upload_report_share_journey(c):
    h, vid = fleet(c)
    name, data = sample("evt_0001_DROP")
    digest = hashlib.sha256(data).hexdigest()
    r = upload(c, h, vid, name, data, claimed_sha256=digest, recorded_at="2026-10-03T17:30:22+05:30")
    assert r.status_code == 201, r.text
    j = r.json()
    assert j["verdict"] == "DROP" and j["hash_status"] == "match" and j["evidence"]["freefall_ms"] >= 120
    assert c.get(f"/events/{j['id']}/verify", headers=h).json()["intact"] is True
    s = c.post(f"/events/{j['id']}/share", headers=h).json()
    pub = c.get(s["path"])                                            # no sign-in
    assert pub.status_code == 200 and pub.json()["event"]["verdict"] == "DROP"
    assert pub.json()["integrity"]["intact"] is True and "raw" not in pub.text
    assert c.delete(f"/shares/{s['token']}", headers=h).status_code == 204
    assert c.get(s["path"]).status_code == 410


def test_crash_sample_wins_over_trailing_incomplete(c):
    h, vid = fleet(c)
    name, data = sample("evt_0002_CRASH")
    j = upload(c, h, vid, name, data).json()
    assert j["verdict"] == "CRASH" and j["hash_status"] == "not_provided"


def test_wrong_client_hash_flagged(c):
    h, vid = fleet(c)
    name, data = sample("evt_0003")
    j = upload(c, h, vid, name, data, claimed_sha256="0" * 64).json()
    assert j["hash_status"] == "mismatch" and j["sha256"] == hashlib.sha256(data).hexdigest()


# ---- insurer journey ------------------------------------------------------
def test_insurer_evidence_report_and_tamper_detection(c):
    h, vid = fleet(c)
    name, data = sample("evt_0002_CRASH")
    eid = upload(c, h, vid, name, data).json()["id"]
    tok = c.post(f"/events/{eid}/share", headers=h).json()["token"]
    ins = signup(c, "i@x.com", "insurer", invite_code="letmein")
    assert c.get(f"/shared/{tok}/evidence-report").status_code == 401
    assert c.get(f"/shared/{tok}/evidence-report", headers=h).status_code == 403
    r = c.get(f"/shared/{tok}/evidence-report", headers=ins).json()
    assert r["integrity"]["intact"] is True and "CRASH" in r["case_summary"]
    assert "not a legal finding" in r["case_summary"] and r["viewed_by"].startswith("i@x.com")
    with SessionLocal() as s:                                          # tamper with the stored file
        ev = s.get(models.Event, eid); ev.raw = data + b"0.00,0,0,1,0,0,0,\n"; s.commit()
    r = c.get(f"/shared/{tok}/evidence-report", headers=ins).json()
    assert r["integrity"]["intact"] is False and "WARNING" in r["case_summary"]


# ---- bad input -------------------------------------------------------------
def code(r):
    return r.json()["detail"]["code"]


def test_bad_input_gives_clear_errors(c):
    h, vid = fleet(c)
    name, data = sample("evt_0001")
    assert upload(c, h, vid, name, data).status_code == 201
    r = upload(c, h, vid, name, data); assert (r.status_code, code(r)) == (409, "duplicate_upload")
    r = upload(c, h, vid, "big.csv", b"t,ax,ay,az,gx,gy,gz\n" + b"0,0,0,1,0,0,0\n" * 40000)
    assert (r.status_code, code(r)) == (413, "file_too_large")
    r = upload(c, h, vid, "e.csv", b""); assert code(r) == "empty_file"
    r = upload(c, h, vid, "b.bin", b"\x89PNG\x00\x00\x00"); assert code(r) == "not_text"
    r = upload(c, h, vid, "x.csv", b"hello\nworld"); assert code(r) == "bad_header"
    head = b"t,ax,ay,az,gx,gy,gz\n"
    r = upload(c, h, vid, "s.csv", head + b"".join(b"%.2f,0,0,1,0,0,0\n" % (i / 100) for i in range(100)))
    assert code(r) == "too_little_data"
    r = upload(c, h, vid, "t.csv", data[: len(data) // 2] + b"1.0,abc,0,1,0,0,0\n"); assert code(r) == "bad_row"
    r = upload(c, h, vid, "n.csv", head + b"".join(b"%.2f,0,0,1,0,0,0\n" % (i / 100) for i in range(600))); assert r.status_code == 201
    assert r.json()["verdict"] in ("BUMP", "HANDLING")           # quiet file: valid, nothing happened
    assert upload(c, h, 999, name, data).status_code == 404
