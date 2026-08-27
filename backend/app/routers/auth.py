"""Registration, login and revocable short-session endpoints."""
from __future__ import annotations

from datetime import datetime
from time import perf_counter

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models
from ..auth import (Actor, actor_dict, actor_for_user, add_audit, add_usage, create_session,
                    current_actor, hash_password, verify_password)
from ..db import get_db
from ..schemas import LoginRequest, RegisterRequest

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _session_response(actor: Actor, token: str, expires_at: datetime) -> dict:
    return {"access_token": token, "token_type": "bearer",
            "expires_at": expires_at.isoformat(), "user": actor_dict(actor)}


@router.post("/register", status_code=201)
def register(payload: RegisterRequest, db: Session = Depends(get_db)):
    started = perf_counter()
    username = payload.username.strip().lower()
    if len(username) < 3:
        raise HTTPException(422, "用户名至少 3 个字符")
    if db.query(models.AppUser).filter(models.AppUser.username == username).first():
        raise HTTPException(409, "用户名已存在")
    if payload.role == "hr" and not (payload.organization_name or "").strip():
        raise HTTPException(422, "HR 注册必须填写组织名称")

    user = models.AppUser(username=username, password_hash=hash_password(payload.password),
                          role=payload.role, status="active")
    db.add(user)
    db.flush()
    organization_id = None
    if payload.role == "hr":
        org_name = payload.organization_name.strip()
        if db.query(models.Organization).filter(models.Organization.name == org_name).first():
            db.rollback()
            raise HTTPException(409, "组织名称已存在，请联系管理员加入")
        org = models.Organization(name=org_name, status="active", created_by=user.id)
        db.add(org)
        db.flush()
        organization_id = org.id
        db.add(models.OrganizationMember(organization_id=org.id, user_id=user.id,
                                         role="hr", status="active"))
    token, session = create_session(db, user)
    actor = actor_for_user(db, user, session)
    add_audit(db, actor, "auth.register", "app_user", user.id)
    add_usage(db, actor, "register", int((perf_counter() - started) * 1000))
    db.commit()
    return _session_response(actor, token, session.expires_at)


@router.post("/login")
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    started = perf_counter()
    username = payload.username.strip().lower()
    user = db.query(models.AppUser).filter(models.AppUser.username == username).first()
    if not user or user.status != "active" or not verify_password(payload.password, user.password_hash):
        add_audit(db, None, "auth.login", "app_user", user.id if user else None,
                  result="denied", summary={"reason_code": "invalid_credentials"})
        add_usage(db, None, "login", int((perf_counter() - started) * 1000), False)
        db.commit()
        raise HTTPException(401, "用户名或密码错误")
    user.last_login_at = datetime.utcnow()
    token, session = create_session(db, user)
    actor = actor_for_user(db, user, session)
    add_audit(db, actor, "auth.login", "user_session", session.id)
    add_usage(db, actor, "login", int((perf_counter() - started) * 1000))
    db.commit()
    return _session_response(actor, token, session.expires_at)


@router.post("/logout", status_code=204)
def logout(actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    actor.session.revoked_at = datetime.utcnow()
    add_audit(db, actor, "auth.logout", "user_session", actor.session.id)
    db.commit()


@router.get("/me")
def me(actor: Actor = Depends(current_actor)):
    return actor_dict(actor)
