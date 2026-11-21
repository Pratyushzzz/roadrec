"""Password hashing (scrypt), signed bearer tokens (JWT HS256) and role checks."""
import hashlib, hmac, os, secrets
from datetime import datetime, timedelta, timezone
import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session
from .db import get_db
from .errors import err
from . import models

ROLES = ("fleet_manager", "insurer", "authority")
TOKEN_HOURS = 12
_bearer = HTTPBearer(auto_error=False)


def secret_key() -> str:
    k = os.getenv("SECRET_KEY")
    if not k:
        if os.getenv("ENV") == "production":
            raise RuntimeError("SECRET_KEY must be set in production")
        k = "dev-only-insecure-key-change-me-0123456789"
    return k


def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt${salt.hex()}${h.hex()}"


def check_password(pw: str, stored: str) -> bool:
    try:
        _, salt, h = stored.split("$")
        calc = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1, dklen=32)
        return hmac.compare_digest(calc.hex(), h)
    except Exception:
        return False


def make_token(user: models.User) -> str:
    exp = datetime.now(timezone.utc) + timedelta(hours=TOKEN_HOURS)
    return jwt.encode({"sub": str(user.id), "role": user.role, "exp": exp}, secret_key(), algorithm="HS256")


def current_user(cred: HTTPAuthorizationCredentials | None = Depends(_bearer),
                 db: Session = Depends(get_db)) -> models.User:
    if not cred:
        raise err(401, "not_signed_in", "Sign in to continue")
    try:
        uid = int(jwt.decode(cred.credentials, secret_key(), algorithms=["HS256"])["sub"])
    except jwt.ExpiredSignatureError:
        raise err(401, "token_expired", "Your session expired. Sign in again")
    except Exception:
        raise err(401, "bad_token", "Invalid sign-in token")
    user = db.get(models.User, uid)
    if not user:
        raise err(401, "bad_token", "Account no longer exists")
    return user


def require_roles(*roles):
    def dep(user: models.User = Depends(current_user)) -> models.User:
        if user.role not in roles:
            raise err(403, "wrong_role", f"This action needs one of these roles: {', '.join(roles)}")
        return user
    return dep
