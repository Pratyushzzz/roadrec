"""RoadRec backend. Run from backend/:  uvicorn app.main:app --reload"""
import os, re, secrets
from datetime import datetime, timedelta, timezone
from fastapi import FastAPI, Depends, File, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import analysis, auth, graph, models, schemas, validate
from .db import Base, engine, get_db
from .errors import err
from .summary import case_summary

Base.metadata.create_all(engine)
app = FastAPI(title="RoadRec API", version="0.2.0")
app.add_middleware(CORSMiddleware, allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),
                   allow_methods=["*"], allow_headers=["*"])
HEX64 = re.compile(r"^[0-9a-fA-F]{64}$")
fleet = auth.require_roles("fleet_manager")
reader = auth.require_roles("insurer", "authority")


@app.get("/health")
def health():
    return {"ok": True, "neo4j": graph.enabled()}


# ---- accounts ---------------------------------------------------------------
@app.post("/auth/register", response_model=schemas.TokenOut, status_code=201)
def register(b: schemas.RegisterIn, db: Session = Depends(get_db)):
    if b.role != "fleet_manager":
        code = os.getenv("INVITE_CODE")
        if not code or not b.invite_code or not secrets.compare_digest(b.invite_code, code):
            raise err(403, "invite_required", "Insurer and authority accounts need a valid invite code")
    email = b.email.strip().lower()
    if db.scalar(select(models.User).where(models.User.email == email)):
        raise err(409, "email_taken", "An account with this email already exists")
    u = models.User(email=email, name=b.name.strip(), role=b.role, password_hash=auth.hash_password(b.password))
    db.add(u); db.commit()
    return schemas.TokenOut(access_token=auth.make_token(u), user=u)


@app.post("/auth/login", response_model=schemas.TokenOut)
def login(b: schemas.LoginIn, db: Session = Depends(get_db)):
    u = db.scalar(select(models.User).where(models.User.email == b.email.strip().lower()))
    ok = auth.check_password(b.password, u.password_hash) if u else auth.check_password(b.password, "x$00$00")
    if not u or not ok:
        raise err(401, "bad_credentials", "Wrong email or password")
    return schemas.TokenOut(access_token=auth.make_token(u), user=u)


@app.get("/auth/me", response_model=schemas.UserOut)
def me(user: models.User = Depends(auth.current_user)):
    return user


# ---- vehicles (fleet managers) ---------------------------------------------
@app.post("/vehicles", response_model=schemas.VehicleOut, status_code=201)
def create_vehicle(v: schemas.VehicleIn, user=Depends(fleet), db: Session = Depends(get_db)):
    reg = v.reg_no.strip().upper()
    if db.scalar(select(models.Vehicle).where(models.Vehicle.reg_no == reg)):
        raise err(409, "vehicle_exists", f"Vehicle {reg} is already registered")
    row = models.Vehicle(owner_id=user.id, reg_no=reg, name=v.name.strip())
    db.add(row); db.commit()
    return row


@app.get("/vehicles", response_model=list[schemas.VehicleOut])
def list_vehicles(user=Depends(fleet), db: Session = Depends(get_db)):
    return db.scalars(select(models.Vehicle).where(models.Vehicle.owner_id == user.id).order_by(models.Vehicle.id)).all()


# ---- events (fleet managers) -------------------------------------------------
async def _read_limited(file: UploadFile) -> bytes:
    buf = bytearray()
    while chunk := await file.read(64 * 1024):
        buf += chunk
        if len(buf) > validate.MAX_BYTES:
            raise err(413, "file_too_large", f"File is larger than the {validate.MAX_BYTES // (1024 * 1024)} MB limit. "
                                              "A single event file is normally under 100 KB")
    return bytes(buf)


@app.post("/events", response_model=schemas.EventOut, status_code=201)
async def upload_event(
    vehicle_id: int = Form(...),
    file: UploadFile = File(...),
    recorded_at: datetime | None = Form(None),
    claimed_sha256: str | None = Form(None),
    user=Depends(fleet),
    db: Session = Depends(get_db),
):
    veh = db.get(models.Vehicle, vehicle_id)
    if not veh or veh.owner_id != user.id:      # same answer for "missing" and "not yours"
        raise err(404, "vehicle_not_found", f"Vehicle {vehicle_id} not found in your fleet")
    data = await _read_limited(file)
    claimed = claimed_sha256.strip().lower() if claimed_sha256 else None
    if claimed and not HEX64.match(claimed):
        raise err(422, "bad_hash", "The fingerprint must be 64 hex characters (the contents of the .sha256 file)")

    validate.check_event_csv(data)
    digest = analysis.sha256_hex(data)           # the server never trusts the client's hash
    if db.scalar(select(models.Event.id).where(models.Event.sha256 == digest)):
        raise err(409, "duplicate_upload", "This exact file has already been uploaded")
    try:
        res = analysis.classify_bytes(data)
    except analysis.AnalysisError as e:
        raise err(422, "unreadable", str(e))

    status = "not_provided" if not claimed else ("match" if claimed == digest else "mismatch")
    rec = recorded_at or datetime.now(timezone.utc)
    if rec.tzinfo is None:
        rec = rec.replace(tzinfo=timezone.utc)
    ev = models.Event(
        vehicle_id=veh.id, uploaded_by=user.id, filename=(file.filename or "event.csv")[:255], size_bytes=len(data),
        recorded_at=rec, sha256=digest, claimed_sha256=claimed, hash_status=status,
        verdict=res["verdict"], reasons=res["reasons"], evidence=res["evidence"],
        detections=res["detections"], classifier_version=analysis.CLASSIFIER_VERSION, raw=data)
    db.add(ev)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise err(409, "duplicate_upload", "This exact file has already been uploaded")
    graph.record_upload(user, veh, ev)
    return ev


def _own_event(db, user, event_id) -> models.Event:
    ev = db.get(models.Event, event_id)
    if not ev or ev.vehicle.owner_id != user.id:
        raise err(404, "event_not_found", "Event not found")
    return ev


def _verify(ev) -> schemas.VerifyOut:
    again = analysis.sha256_hex(ev.raw)
    return schemas.VerifyOut(event_id=ev.id, stored_sha256=ev.sha256, recomputed_sha256=again,
                             intact=(again == ev.sha256), upload_hash_status=ev.hash_status)


@app.get("/events", response_model=list[schemas.EventSummary])
def list_events(vehicle_id: int | None = None, verdict: str | None = None,
                user=Depends(fleet), db: Session = Depends(get_db)):
    q = (select(models.Event).join(models.Vehicle).where(models.Vehicle.owner_id == user.id)
         .order_by(models.Event.recorded_at.desc()))
    if vehicle_id is not None:
        q = q.where(models.Event.vehicle_id == vehicle_id)
    if verdict:
        q = q.where(models.Event.verdict == verdict.upper())
    return db.scalars(q).all()


@app.get("/events/{event_id}", response_model=schemas.EventOut)
def get_event(event_id: int, user=Depends(fleet), db: Session = Depends(get_db)):
    return _own_event(db, user, event_id)


@app.get("/events/{event_id}/verify", response_model=schemas.VerifyOut)
def verify_event(event_id: int, user=Depends(fleet), db: Session = Depends(get_db)):
    return _verify(_own_event(db, user, event_id))


# ---- sharing -----------------------------------------------------------------
@app.post("/events/{event_id}/share", response_model=schemas.ShareOut, status_code=201)
def share_event(event_id: int, b: schemas.ShareIn = schemas.ShareIn(), user=Depends(fleet),
                db: Session = Depends(get_db)):
    ev = _own_event(db, user, event_id)
    link = models.ShareLink(token=secrets.token_urlsafe(24), event_id=ev.id, created_by=user.id,
                            expires_at=datetime.now(timezone.utc) + timedelta(days=b.days))
    db.add(link); db.commit()
    graph.record_share(user, ev, link)
    return schemas.ShareOut(token=link.token, path=f"/shared/{link.token}", expires_at=link.expires_at)


@app.delete("/shares/{token}", status_code=204)
def revoke_share(token: str, user=Depends(fleet), db: Session = Depends(get_db)):
    link = db.scalar(select(models.ShareLink).where(models.ShareLink.token == token))
    if not link or link.created_by != user.id:
        raise err(404, "share_not_found", "Share link not found")
    link.revoked = True
    db.commit()


def _live_link(db, token) -> models.ShareLink:
    link = db.scalar(select(models.ShareLink).where(models.ShareLink.token == token))
    if not link:
        raise err(404, "share_not_found", "This share link does not exist")
    exp = link.expires_at if link.expires_at.tzinfo else link.expires_at.replace(tzinfo=timezone.utc)
    if link.revoked or exp < datetime.now(timezone.utc):
        raise err(410, "share_expired", "This share link has expired or was revoked. Ask the sender for a new one")
    return link


def _report(db, link, cls=schemas.SharedReport, **extra):
    ev = db.get(models.Event, link.event_id)
    return cls(vehicle_reg_no=ev.vehicle.reg_no, event=schemas.EventOut.model_validate(ev),
               integrity=_verify(ev), shared_expires_at=link.expires_at, **extra), ev


@app.get("/shared/{token}", response_model=schemas.SharedReport)
def shared_report(token: str, db: Session = Depends(get_db)):
    """Read-only report for anyone holding the link. No sign-in, no raw file."""
    return _report(db, _live_link(db, token))[0]


# ---- insurer / authority journey ----------------------------------------------
@app.get("/shared/{token}/evidence-report", response_model=schemas.EvidenceReport)
def evidence_report(token: str, user=Depends(reader), db: Session = Depends(get_db)):
    """Tamper-checked evidence report with a short case summary. Needs an insurer or authority account."""
    link = _live_link(db, token)
    ev = db.get(models.Event, link.event_id)
    ver = _verify(ev)
    rep = schemas.EvidenceReport(
        vehicle_reg_no=ev.vehicle.reg_no, event=schemas.EventOut.model_validate(ev), integrity=ver,
        shared_expires_at=link.expires_at, case_summary=case_summary(ev, ev.vehicle, ver.intact),
        viewed_by=f"{user.email} ({user.role})")
    graph.record_view(user, ev)
    return rep
