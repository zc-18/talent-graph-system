"""Owner-scoped personal profiles and reproducible match history."""
from __future__ import annotations

import hashlib
import os

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from .. import models
from ..auth import Actor, actor_dict, add_audit, current_actor
from ..db import get_db
from ..schemas import AVATAR_PRESETS, ProfileUpdateRequest

router = APIRouter(prefix="/api/me", tags=["me"])

# 头像上传上限与真实类型白名单。扩展名只是个字符串，任何人都能改，
# 所以落盘用的扩展名一律由**文件头魔数**推出来，不信客户端给的名字。
AVATAR_MAX_BYTES = 2 * 1024 * 1024
AVATAR_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpg"),
)


def _static_dir() -> str:
    """与 app/main.py 的 STATIC_DIR 解析保持一致（同一目录被 SPA 兜底路由直接托管）。"""
    return os.environ.get("STATIC_DIR") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "static")


def _detect_image_ext(content: bytes) -> str | None:
    for magic, ext in AVATAR_MAGIC:
        if content.startswith(magic):
            return ext
    if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "webp"
    return None


def _scope(query, model, actor: Actor):
    if actor.role == "admin":
        return query.filter(False)
    if actor.role == "hr":
        return query.filter(model.organization_id == actor.organization_id)
    return query.filter(model.owner_user_id == actor.user_id)


def _match_dict(row: models.MatchRun, job_name: str | None = None,
                detail: bool = False) -> dict:
    result = row.result_snapshot or {}
    job_name = job_name or (row.contract_snapshot or {}).get("job_name")
    item = {"id": row.id, "status": row.status, "created_at": row.created_at.isoformat(),
            "job_id": row.job_id, "job_name": job_name,
            "job_version_id": row.job_version_id, "job_version": row.job_version,
            "overall_score": result.get("overall_score", 0), "level": result.get("level"),
            "top_gaps": (result.get("missing_required") or [])[:10]}
    if detail:
        item.update({"contract_snapshot": row.contract_snapshot or {},
                     "result": result, "learning_path": row.learning_path or [],
                     "resume_profile_id": row.resume_profile_id})
    return item


@router.get("/matches")
def matches(page: int = 1, size: int = 20, actor: Actor = Depends(current_actor),
            db: Session = Depends(get_db)):
    page, size = max(1, page), min(100, max(1, size))
    q = _scope(db.query(models.MatchRun), models.MatchRun, actor)
    total = q.count()
    rows = q.order_by(models.MatchRun.created_at.desc()).offset((page - 1) * size).limit(size).all()
    names = dict(db.query(models.Job.id, models.Job.name).filter(
        models.Job.id.in_({r.job_id for r in rows if r.job_id})).all()) if rows else {}
    return {"items": [_match_dict(row, names.get(row.job_id)) for row in rows],
            "total": total, "page": page, "size": size}


@router.get("/matches/{match_id}")
def match_detail(match_id: int, actor: Actor = Depends(current_actor),
                 db: Session = Depends(get_db)):
    row = _scope(db.query(models.MatchRun), models.MatchRun, actor).filter(
        models.MatchRun.id == match_id).first()
    if not row:
        raise HTTPException(404, "匹配记录不存在")
    job = db.query(models.Job).get(row.job_id) if row.job_id else None
    return _match_dict(row, job.name if job else None, detail=True)


@router.get("/resume-profiles")
def resume_profiles(page: int = 1, size: int = 20,
                    actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    page, size = max(1, page), min(100, max(1, size))
    q = _scope(db.query(models.ResumeProfile), models.ResumeProfile, actor)
    total = q.count()
    rows = q.order_by(models.ResumeProfile.created_at.desc()).offset((page - 1) * size).limit(size).all()
    return {"items": [{"id": r.id, "code": r.code, "skills": r.skills or [],
                       "skill_levels": r.skill_levels or {},
                       "years_experience": r.years_experience, "education": r.education,
                       "retention_expires_at": (r.retention_expires_at.isoformat()
                                                if r.retention_expires_at else None),
                       "created_at": r.created_at.isoformat()} for r in rows],
            "total": total, "page": page, "size": size}


# ---------------- 个人资料自助维护 ----------------
# 刻意不挂 require_write：READ_ONLY 总闸只关「改公共知识图谱」的路由（见 app/guards.py
# 的说明），用户自己的私有数据照常可写 —— routers/feedback.py 就是同样的口径。
# 昵称和头像不进入图谱、不参与置信度、不影响任何对外口径数字。

@router.get("/avatar-presets")
def avatar_presets(actor: Actor = Depends(current_actor)):
    """预置头像图库（站内相对路径，前端直接渲染）。"""
    return {"items": list(AVATAR_PRESETS), "total": len(AVATAR_PRESETS),
            "max_upload_bytes": AVATAR_MAX_BYTES}


@router.patch("/profile")
def update_profile(payload: ProfileUpdateRequest, actor: Actor = Depends(current_actor),
                   db: Session = Depends(get_db)):
    """改昵称 / 选一张预置（或此前上传过的）头像。返回体与 /api/auth/me 同构。"""
    user = db.query(models.AppUser).get(actor.user_id)
    if not user:
        raise HTTPException(404, "用户不存在")
    changed = []
    if payload.nickname is not None:
        user.nickname = payload.nickname
        changed.append("nickname")
    if payload.avatar_url is not None:
        user.avatar_url = payload.avatar_url
        changed.append("avatar_url")
    add_audit(db, actor, "me.profile.update", "app_user", user.id,
              summary={"action": ",".join(changed), "count": len(changed)})
    db.commit()
    db.refresh(user)
    return actor_dict(actor)


@router.post("/avatar")
async def upload_avatar(file: UploadFile = File(...), actor: Actor = Depends(current_actor),
                        db: Session = Depends(get_db)):
    """上传自定义头像：≤2MB，且必须**真的**是 png/jpg/webp（按文件头判定）。"""
    filename = (file.filename or "").strip()
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if extension not in {"png", "jpg", "jpeg", "webp"}:
        raise HTTPException(422, "头像仅支持 .png/.jpg/.jpeg/.webp")

    chunks: list[bytes] = []
    size = 0
    while True:
        chunk = await file.read(64 * 1024)
        if not chunk:
            break
        size += len(chunk)
        if size > AVATAR_MAX_BYTES:
            raise HTTPException(413, "头像文件不得超过 2MB")
        chunks.append(chunk)
    content = b"".join(chunks)
    if not content:
        raise HTTPException(422, "头像文件为空")

    detected = _detect_image_ext(content)
    if detected is None:
        raise HTTPException(422, "文件内容不是有效的 PNG/JPEG/WebP 图片")
    if detected != ("jpg" if extension == "jpeg" else extension):
        raise HTTPException(422, "文件扩展名与实际图片格式不一致")

    digest = hashlib.sha256(content).hexdigest()[:16]
    name = f"u{actor.user_id}-{digest}.{detected}"
    target_dir = os.path.join(_static_dir(), "avatars")
    os.makedirs(target_dir, exist_ok=True)
    with open(os.path.join(target_dir, name), "wb") as handle:
        handle.write(content)

    user = db.query(models.AppUser).get(actor.user_id)
    if not user:
        raise HTTPException(404, "用户不存在")
    user.avatar_url = f"/avatars/{name}"
    add_audit(db, actor, "me.avatar.upload", "app_user", user.id,
              summary={"action": "upload", "status": detected, "count": size})
    db.commit()
    db.refresh(user)
    return {"avatar_url": user.avatar_url, "size": size, "format": detected,
            "user": actor_dict(actor)}
