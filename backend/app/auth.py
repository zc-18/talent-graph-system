"""Session authentication with scrypt password hashes and hashed bearer tokens."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import hmac
import secrets

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from . import models
from .db import get_db


SESSION_TTL = timedelta(hours=8)
_bearer = HTTPBearer(auto_error=False)

ROLE_PERMISSIONS = {
    "guest": ("public:read", "match:once"),
    "user": ("public:read", "match:once", "profile:own", "match:own", "feedback:own"),
    "hr": ("public:read", "candidate:org", "recruitment:org", "team:org", "feedback:org"),
    "admin": ("public:read", "admin:manage", "candidate:review", "audit:read", "usage:read"),
}


@dataclass(frozen=True)
class Actor:
    user: models.AppUser | None
    role: str
    organization_id: int | None
    permissions: tuple[str, ...]
    session: models.UserSession | None = None

    @property
    def user_id(self) -> int | None:
        return self.user.id if self.user else None


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt$16384$8$1${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt_hex, digest_hex = encoded.split("$", 5)
        if algorithm != "scrypt":
            return False
        candidate = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt_hex),
                                   n=int(n), r=int(r), p=int(p), dklen=len(bytes.fromhex(digest_hex)))
        return hmac.compare_digest(candidate.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(db: Session, user: models.AppUser) -> tuple[str, models.UserSession]:
    raw_token = secrets.token_urlsafe(32)
    session = models.UserSession(user_id=user.id, token_hash=token_hash(raw_token),
                                 expires_at=datetime.utcnow() + SESSION_TTL)
    db.add(session)
    db.flush()
    return raw_token, session


def _organization_id(db: Session, user: models.AppUser) -> int | None:
    if user.role != "hr":
        return None
    member = db.query(models.OrganizationMember).join(
        models.Organization,
        models.Organization.id == models.OrganizationMember.organization_id).filter(
        models.OrganizationMember.user_id == user.id,
        models.OrganizationMember.status == "active",
        models.Organization.status == "active").order_by(
        models.OrganizationMember.id).first()
    return member.organization_id if member else None


def actor_for_user(db: Session, user: models.AppUser,
                   session: models.UserSession | None = None) -> Actor:
    role = user.role if user.role in ROLE_PERMISSIONS else "user"
    return Actor(user, role, _organization_id(db, user), ROLE_PERMISSIONS[role], session)


def optional_actor(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> Actor:
    if credentials is None:
        return Actor(None, "guest", None, ROLE_PERMISSIONS["guest"])
    if credentials.scheme.lower() != "bearer":
        raise HTTPException(401, "无效的认证方式", headers={"WWW-Authenticate": "Bearer"})
    session = db.query(models.UserSession).filter(
        models.UserSession.token_hash == token_hash(credentials.credentials)).first()
    now = datetime.utcnow()
    if not session or session.revoked_at is not None or session.expires_at <= now:
        raise HTTPException(401, "会话无效或已过期", headers={"WWW-Authenticate": "Bearer"})
    user = db.query(models.AppUser).get(session.user_id)
    if not user or user.status != "active":
        raise HTTPException(401, "用户不可用", headers={"WWW-Authenticate": "Bearer"})
    return actor_for_user(db, user, session)


def current_actor(actor: Actor = Depends(optional_actor)) -> Actor:
    if actor.user is None:
        raise HTTPException(401, "请先登录", headers={"WWW-Authenticate": "Bearer"})
    return actor


def actor_dict(actor: Actor) -> dict:
    return {
        "id": actor.user_id,
        "username": actor.user.username if actor.user else None,
        "role": actor.role,
        "status": actor.user.status if actor.user else "anonymous",
        "organization_id": actor.organization_id,
        "permissions": list(actor.permissions),
    }


def add_audit(db: Session, actor: Actor | None, action: str, target_type: str,
              target_id: int | str | None = None, result: str = "success",
              summary: dict | None = None) -> models.AuditLog:
    """Append a metadata-only audit row. Callers must never pass request bodies or resume text."""
    allowed = {"status", "count", "action", "reason_code", "feature", "revision", "version"}
    safe_summary = {k: v for k, v in (summary or {}).items() if k in allowed}
    row = models.AuditLog(
        actor_user_id=actor.user_id if actor else None,
        organization_id=actor.organization_id if actor else None,
        action=action, target_type=target_type,
        target_id=str(target_id) if target_id is not None else None,
        result=result, summary=safe_summary)
    db.add(row)
    return row


def add_usage(db: Session, actor: Actor | None, feature: str, duration_ms: int,
              success: bool = True) -> models.UsageEvent:
    row = models.UsageEvent(user_id=actor.user_id if actor else None,
                            organization_id=actor.organization_id if actor else None,
                            feature=feature, duration_ms=max(0, int(duration_ms)), success=success)
    db.add(row)
    return row
