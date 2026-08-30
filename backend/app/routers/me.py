"""Owner-scoped personal profiles and reproducible match history."""
from __future__ import annotations

import hashlib
import io
import os
from pathlib import Path
import secrets
import stat
import warnings

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from PIL import Image, UnidentifiedImageError
from sqlalchemy.orm import Session
from starlette.responses import FileResponse
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

from .. import models
from ..auth import Actor, actor_dict, add_audit, current_actor
from ..db import get_db
from ..schemas import (AVATAR_PRESETS, ProfileUpdateRequest, is_preset_avatar,
                       uploaded_avatar_user_id)

router = APIRouter(prefix="/api/me", tags=["me"])

# 头像上传上限与真实类型白名单。扩展名只是个字符串，任何人都能改，
# 所以落盘用的扩展名一律由**完整图片解码结果**推出来，不信客户端给的名字/Content-Type。
AVATAR_MAX_BYTES = 2 * 1024 * 1024
AVATAR_MAX_PIXELS = 16 * 1024 * 1024
AVATAR_INPUT_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}
AVATAR_FORMAT_EXTENSIONS = {"PNG": "png", "JPEG": "jpg", "WEBP": "webp"}


def _upload_dir() -> Path:
    """用户文件必须在前端 ``static`` 之外，避免部署新版 dist 时被覆盖。"""
    configured = os.environ.get("AVATAR_UPLOAD_DIR")
    if configured:
        return Path(configured).resolve()
    backend_dir = Path(__file__).resolve().parents[2]
    return backend_dir / "user_uploads" / "avatars"


def _preset_dir() -> Path:
    static_dir = os.environ.get("STATIC_DIR")
    if static_dir:
        return Path(static_dir).resolve() / "avatars"
    return Path(__file__).resolve().parents[2] / "static" / "avatars"


class AvatarStaticFiles(StaticFiles):
    """Serve only canonical avatar names from the preset or persistent upload directory.

    A fresh ``StaticFiles`` delegate is used per request.  Mutating one mounted instance's
    ``directory`` would let concurrent preset/upload requests race and read from the wrong root.
    """

    async def check_config(self) -> None:
        # Presets appear only after the frontend build and uploads only after the first write;
        # either directory may legitimately be absent when the API process starts.
        return None

    async def get_response(self, path: str, scope: Scope):
        if scope["method"] not in {"GET", "HEAD"}:
            raise HTTPException(405, "Method Not Allowed")
        avatar_url = f"/avatars/{path}"
        upload_owner = uploaded_avatar_user_id(avatar_url)
        if is_preset_avatar(avatar_url):
            directory = _preset_dir()
            cache_control = "public, max-age=3600, must-revalidate"
        elif upload_owner is not None:
            directory = _upload_dir()
            cache_control = "public, max-age=31536000, immutable"
        else:
            raise HTTPException(404, "头像不存在")

        # Starlette normalizes ``a/../a01.webp`` before get_response().  Compare the raw
        # request path as well so encoded traversal, encoded separators and double slashes
        # are rejected rather than normalized into an otherwise valid avatar name.
        expected_raw_path = avatar_url.encode("ascii")
        raw_path = scope.get("raw_path")
        if isinstance(raw_path, str):
            raw_path = raw_path.encode("utf-8", "surrogatepass")
        if ((raw_path is not None and raw_path != expected_raw_path)
                or scope.get("path") != avatar_url):
            raise HTTPException(404, "头像不存在")

        # Reject a symlink at the requested pathname *before* StaticFiles resolves it.  Checking
        # only ``full_path`` is insufficient because lookup_path() returns the resolved target.
        candidate = directory / path
        try:
            candidate.relative_to(directory)
        except ValueError:
            raise HTTPException(404, "头像不存在") from None
        if candidate.is_symlink():
            raise HTTPException(404, "头像不存在")

        # Do not mutate this mounted instance: requests for the two roots may run together.
        delegate = StaticFiles(directory=directory, check_dir=False, follow_symlink=False)
        try:
            full_path, stat_result = delegate.lookup_path(path)
        except (OSError, ValueError):
            raise HTTPException(404, "头像不存在") from None
        if stat_result is None or not stat.S_ISREG(stat_result.st_mode):
            raise HTTPException(404, "头像不存在")
        extension = path.rsplit(".", 1)[-1]
        media_type = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp"}[extension]
        # Pass the type explicitly: Windows' mimetypes database may not know WebP.
        response = FileResponse(full_path, media_type=media_type, stat_result=stat_result)
        response.headers["Cache-Control"] = cache_control
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        return response


# APIRouter.mount does not inherit router.prefix.  FastAPI includes this mount at
# /avatars/... before main.py's SPA catch-all, while the profile APIs remain /api/me/....
router.mount("/avatars", AvatarStaticFiles(directory=None, check_dir=False),
             name="user-avatars")


def _detect_image_ext(content: bytes) -> str | None:
    """Decode the complete image and reject truncated, polyglot, or decompression-bomb inputs."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(content)) as image:
                detected = AVATAR_FORMAT_EXTENSIONS.get(image.format or "")
                width, height = image.size
                if (detected is None or width < 1 or height < 1
                        or width * height > AVATAR_MAX_PIXELS
                        or getattr(image, "is_animated", False)
                        or getattr(image, "n_frames", 1) != 1):
                    return None
                image.verify()
            # ``verify`` checks structure; reopening and ``load`` forces pixel decoding.
            with Image.open(io.BytesIO(content)) as image:
                image.load()
        return detected
    except (Image.DecompressionBombError, Image.DecompressionBombWarning,
            UnidentifiedImageError, OSError, SyntaxError, ValueError):
        return None


def _owned_upload_path(value: str | None, user_id: int) -> Path | None:
    if not value or uploaded_avatar_user_id(value) != user_id:
        return None
    root = _upload_dir()
    # The schema's full-match regex guarantees a separator-free canonical filename.  Keep
    # the unresolved path so callers can still detect and reject a symlink at that pathname.
    candidate = root / value.rsplit("/", 1)[-1]
    try:
        candidate.resolve(strict=False).relative_to(root)
    except ValueError:
        return None
    return candidate


def _remove_upload(value: str | None, user_id: int, *, keep: Path | None = None) -> None:
    """Best-effort removal of the user's previous upload; never follows a DB-supplied path."""
    old = _owned_upload_path(value, user_id)
    if old is not None and old != keep and not old.is_symlink():
        try:
            old.unlink(missing_ok=True)
        except OSError:
            # 资料更新本身不应因一次清理失败而变成 500；上传时还会做目录配额清理。
            pass


def _prune_user_uploads(user_id: int, *, keep: Path | None) -> None:
    """Bound per-user disk use even if an earlier best-effort deletion failed."""
    root = _upload_dir()
    for candidate in root.glob(f"u{user_id}-*"):
        # A malicious/pre-existing symlink must never make cleanup unlink anything outside
        # the persistent avatar root.  Valid uploads are regular, non-symlink files.
        if candidate != keep and candidate.is_file() and not candidate.is_symlink():
            try:
                candidate.unlink()
            except OSError:
                pass


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
    old_upload_to_remove: str | None = None
    if payload.avatar_url is not None:
        upload_owner = uploaded_avatar_user_id(payload.avatar_url)
        if upload_owner is not None:
            if upload_owner != actor.user_id:
                # 不能通过 PATCH 把自己的资料指向另一用户仍公开可读的上传文件。
                raise HTTPException(422, "只能选择自己上传的头像")
            upload_path = _owned_upload_path(payload.avatar_url, actor.user_id)
            if (upload_path is None or not upload_path.is_file()
                    or upload_path.is_symlink()):
                raise HTTPException(422, "上传头像不存在，请重新上传")
        if is_preset_avatar(payload.avatar_url):
            old_upload_to_remove = user.avatar_url
        user.avatar_url = payload.avatar_url
        changed.append("avatar_url")
    add_audit(db, actor, "me.profile.update", "app_user", user.id,
              summary={"action": ",".join(changed), "count": len(changed)})
    db.commit()
    db.refresh(user)
    # Only unlink after the profile transaction succeeds; a commit failure must not leave
    # the DB pointing at a file that was already removed.
    _remove_upload(old_upload_to_remove, actor.user_id)
    _prune_user_uploads(actor.user_id, keep=_owned_upload_path(user.avatar_url, actor.user_id))
    return actor_dict(actor)


@router.post("/avatar")
async def upload_avatar(file: UploadFile = File(...), actor: Actor = Depends(current_actor),
                        db: Session = Depends(get_db)):
    """上传自定义头像：≤2MB，且必须**真的**是 png/jpg/webp（按文件头判定）。"""
    filename = (file.filename or "").strip()
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if extension not in AVATAR_INPUT_EXTENSIONS:
        raise HTTPException(422, "头像仅支持 .png/.jpg/.jpeg/.webp")
    # Starlette 已把 multipart 文件流式写到 SpooledTemporaryFile；这里先用解析器记录的
    # 长度拒绝超限输入，再最多读取 MAX+1 字节，避免在应用内构造超限 bytes/chunks 列表。
    if file.size is not None and file.size > AVATAR_MAX_BYTES:
        raise HTTPException(413, "头像文件不得超过 2MB")
    content = await file.read(AVATAR_MAX_BYTES + 1)
    if len(content) > AVATAR_MAX_BYTES or await file.read(1):
        raise HTTPException(413, "头像文件不得超过 2MB")
    size = len(content)
    if not content:
        raise HTTPException(422, "头像文件为空")

    detected = _detect_image_ext(content)
    if detected is None:
        raise HTTPException(422, "文件内容不是有效的单帧 PNG/JPEG/WebP 图片")
    if detected != ("jpg" if extension == "jpeg" else extension):
        raise HTTPException(422, "文件扩展名与实际图片格式不一致")

    digest = hashlib.sha256(content).hexdigest()
    name = f"u{actor.user_id}-{digest}.{detected}"
    target = _upload_dir() / name
    target.parent.mkdir(parents=True, exist_ok=True)

    user = db.query(models.AppUser).get(actor.user_id)
    if not user:
        raise HTTPException(404, "用户不存在")
    old_avatar = user.avatar_url
    target_preexisted = target.is_file() and not target.is_symlink()
    temporary = target.with_name(f".{name}.{secrets.token_hex(8)}.tmp")
    if target.is_symlink() or temporary.exists() or temporary.is_symlink():
        raise HTTPException(409, "头像存储冲突，请重试")
    try:
        with open(temporary, "xb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if target_preexisted:
            # Verify before reusing: a manually corrupted file must not inherit this digest URL.
            try:
                with open(target, "rb") as existing:
                    existing_digest = hashlib.file_digest(existing, "sha256").hexdigest()
            except OSError:
                existing_digest = ""
            if existing_digest != digest:
                raise HTTPException(409, "头像存储冲突，请重试")
            temporary.unlink(missing_ok=True)
        else:
            os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)

    user.avatar_url = f"/avatars/{name}"
    add_audit(db, actor, "me.avatar.upload", "app_user", user.id,
              summary={"action": "upload", "status": detected, "count": size})
    try:
        db.commit()
    except Exception:
        db.rollback()
        # Keep a successfully installed content-addressed file on transaction failure.  A
        # concurrent identical upload may already have committed the same URL after both
        # requests observed a missing target; deleting here could break that successful row.
        # A later successful upload for this user prunes the harmless orphan.
        raise
    db.refresh(user)
    _remove_upload(old_avatar, actor.user_id, keep=target)
    _prune_user_uploads(actor.user_id, keep=target)
    return {"avatar_url": user.avatar_url, "size": size, "format": detected,
            "user": actor_dict(actor)}
