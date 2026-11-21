from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field


class RegisterIn(BaseModel):
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=254)
    password: str = Field(min_length=8, max_length=128)
    name: str = Field(default="", max_length=120)
    role: str = Field(default="fleet_manager", pattern="^(fleet_manager|insurer|authority)$")
    invite_code: str | None = None


class LoginIn(BaseModel):
    email: str
    password: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    email: str
    name: str
    role: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class VehicleIn(BaseModel):
    reg_no: str = Field(min_length=3, max_length=32)
    name: str = Field(default="", max_length=120)


class VehicleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    reg_no: str
    name: str
    created_at: datetime


class EventSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    vehicle_id: int
    filename: str
    recorded_at: datetime
    uploaded_at: datetime
    verdict: str
    sha256: str
    hash_status: str


class EventOut(EventSummary):
    size_bytes: int
    claimed_sha256: str | None
    reasons: list
    evidence: dict
    detections: list
    classifier_version: str


class VerifyOut(BaseModel):
    event_id: int
    stored_sha256: str
    recomputed_sha256: str
    intact: bool
    upload_hash_status: str


class ShareIn(BaseModel):
    days: int = Field(default=7, ge=1, le=90)


class ShareOut(BaseModel):
    token: str
    path: str
    expires_at: datetime


class SharedReport(BaseModel):
    vehicle_reg_no: str
    event: EventOut
    integrity: VerifyOut
    shared_expires_at: datetime


class EvidenceReport(SharedReport):
    case_summary: str
    viewed_by: str
