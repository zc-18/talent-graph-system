"""Private HR resume batch processing and deterministic ranking."""
from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import io
from pathlib import PurePosixPath
import zipfile

from sqlalchemy.orm import Session

from .. import models
from . import matching, resume, role_contract

MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_ZIP_FILES = 100
MAX_ZIP_BYTES = 64 * 1024 * 1024


def file_digest(filename: str, content: bytes, preset_error: str | None = None) -> str:
    """Stable identity for one submitted file within a recruitment batch."""
    normalized_name = PurePosixPath((filename or "upload").replace("\\", "/")).name.strip().lower()
    marker = (preset_error or "").encode("utf-8")
    return hashlib.sha256(
        normalized_name.encode("utf-8") + b"\0" + marker + b"\0" + content
    ).hexdigest()


def expand_upload(filename: str, content: bytes) -> list[tuple[str, bytes, str | None]]:
    name = filename or "upload"
    if len(content) > MAX_ZIP_BYTES:
        raise resume.ResumeFileError("FILE_TOO_LARGE", "上传内容超过 64MB", 413)
    if not name.lower().endswith(".zip"):
        return [(name, content, None)]
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise resume.ResumeFileError("CORRUPT_FILE", "ZIP 文件损坏") from exc
    infos = [info for info in archive.infolist() if not info.is_dir()]
    if len(infos) > MAX_ZIP_FILES:
        raise resume.ResumeFileError("ZIP_FILE_LIMIT", "ZIP 内文件数超过 100", 413)
    if sum(info.file_size for info in infos) > MAX_ZIP_BYTES:
        raise resume.ResumeFileError("ZIP_SIZE_LIMIT", "ZIP 解压后总大小超过 64MB", 413)
    items = []
    for info in infos:
        path = PurePosixPath(info.filename.replace("\\", "/"))
        if path.is_absolute() or ".." in path.parts:
            raise resume.ResumeFileError("ZIP_PATH_TRAVERSAL", "ZIP 包含不安全路径")
        if info.file_size > MAX_FILE_BYTES:
            items.append((path.name, b"", "FILE_TOO_LARGE"))
            continue
        items.append((path.name, archive.read(info), None))
    return items


def process_file(db: Session, batch: models.RecruitmentBatch, filename: str, content: bytes,
                 contract: dict, retention_days: int,
                 preset_error: str | None = None) -> models.BatchCandidate:
    digest = file_digest(filename, content, preset_error)
    existing = db.query(models.BatchCandidate).filter(
        models.BatchCandidate.batch_id == batch.id,
        models.BatchCandidate.file_hash == digest).first()
    if existing:
        return existing
    seq = db.query(models.BatchCandidate).filter(
        models.BatchCandidate.batch_id == batch.id).count() + 1
    candidate = models.BatchCandidate(
        batch_id=batch.id, file_hash=digest, display_code=f"B{batch.id}-C{seq:03d}",
        parse_status="processing")
    db.add(candidate)
    db.flush()
    return process_candidate_content(
        db, batch, candidate, filename, content, contract, retention_days, preset_error)


def process_candidate_content(db: Session, batch: models.RecruitmentBatch,
                              candidate: models.BatchCandidate, filename: str,
                              content: bytes, contract: dict, retention_days: int,
                              preset_error: str | None = None) -> models.BatchCandidate:
    """Populate an existing candidate without persisting raw resume bytes."""
    candidate.parse_status = "processing"
    candidate.error_code = None
    candidate.error_detail = None
    candidate.overall_score = None
    candidate.dimension_scores = None
    candidate.result_snapshot = None
    candidate.rank = None
    profile = None
    try:
        if preset_error:
            messages = {"FILE_TOO_LARGE": "单文件超过 8MB",
                        "CORRUPT_FILE": "压缩包或文件损坏",
                        "ZIP_FILE_LIMIT": "ZIP 内文件数超过限制",
                        "ZIP_SIZE_LIMIT": "ZIP 解压后总大小超过限制",
                        "ZIP_PATH_TRAVERSAL": "ZIP 包含不安全路径"}
            raise resume.ResumeFileError(preset_error, messages.get(preset_error, "文件处理失败"),
                                         413 if "LIMIT" in preset_error or preset_error == "FILE_TOO_LARGE" else 422)
        if len(content) > MAX_FILE_BYTES:
            raise resume.ResumeFileError("FILE_TOO_LARGE", "单文件超过 8MB", 413)
        text = resume.extract_text(filename, content)
        parsed = resume.parse_resume(resume.mask_contacts(text))
        skills = parsed.get("skills", []) or []
        profile = models.ResumeProfile(
            owner_user_id=None, organization_id=batch.organization_id,
            code=candidate.display_code, source_type="batch", skills=skills,
            skill_levels=parsed.get("skill_levels", {}) or {},
            years_experience=parsed.get("years_experience", 0) or 0,
            education=(parsed.get("education") or "")[:64], authorized=True,
            retention_expires_at=datetime.utcnow() + timedelta(days=retention_days))
        db.add(profile)
        db.flush()
        result = matching.match(role_contract.matching_capabilities(contract), skills,
                                profile.skill_levels, use_semantic=False)
        candidate.resume_profile_id = profile.id
        candidate.parse_status = "succeeded"
        candidate.overall_score = result["overall_score"]
        candidate.dimension_scores = result["dimension_scores"]
        candidate.result_snapshot = result
    except resume.ResumeFileError as exc:
        if profile is not None:
            db.delete(profile)
        candidate.resume_profile_id = None
        candidate.parse_status = "failed"
        candidate.error_code = exc.code
        candidate.error_detail = exc.message[:256]
    except Exception:  # noqa: BLE001
        if profile is not None:
            db.delete(profile)
        candidate.resume_profile_id = None
        candidate.parse_status = "failed"
        candidate.error_code = "PARSE_FAILED"
        candidate.error_detail = "简历解析失败"
    return candidate


def retry_candidate(db: Session, batch: models.RecruitmentBatch,
                    candidate: models.BatchCandidate, filename: str, content: bytes,
                    contract: dict, retention_days: int) -> models.BatchCandidate:
    if candidate.parse_status != "failed":
        raise ValueError("CANDIDATE_NOT_FAILED")
    digest = file_digest(filename, content)
    duplicate = db.query(models.BatchCandidate).filter(
        models.BatchCandidate.batch_id == batch.id,
        models.BatchCandidate.file_hash == digest,
        models.BatchCandidate.id != candidate.id).first()
    if duplicate:
        raise ValueError("DUPLICATE_FILE")
    candidate.file_hash = digest
    return process_candidate_content(
        db, batch, candidate, filename, content, contract, retention_days)


def correct_candidate_skills(db: Session, candidate: models.BatchCandidate,
                             contract: dict, skills: list[str],
                             skill_levels: dict[str, str], note: str | None = None
                             ) -> models.BatchCandidate:
    if candidate.parse_status != "succeeded" or not candidate.resume_profile_id:
        raise ValueError("CANDIDATE_NOT_READY")
    correction_text = "\n".join([
        *skills,
        *(str(key) for key in skill_levels),
        *(str(value) for value in skill_levels.values()),
        note or "",
    ])
    if resume.contains_contacts(correction_text):
        raise ValueError("CONTACTS_NOT_ALLOWED")
    profile = db.get(models.ResumeProfile, candidate.resume_profile_id)
    if profile is None:
        raise ValueError("PROFILE_NOT_FOUND")
    profile.skills = list(dict.fromkeys(skills))
    profile.skill_levels = {
        key: str(value)[:32] for key, value in skill_levels.items()
        if key in profile.skills
    }
    result = matching.match(role_contract.matching_capabilities(contract), profile.skills,
                            profile.skill_levels, use_semantic=False)
    candidate.overall_score = result["overall_score"]
    candidate.dimension_scores = result["dimension_scores"]
    candidate.result_snapshot = result
    candidate.note = (note or "").strip() or None
    rerank(db, candidate.batch_id)
    return candidate


def rerank(db: Session, batch_id: int) -> list[models.BatchCandidate]:
    rows = db.query(models.BatchCandidate).filter(
        models.BatchCandidate.batch_id == batch_id,
        models.BatchCandidate.parse_status == "succeeded").all()
    rows.sort(key=lambda row: (-(row.overall_score or 0), row.id))
    for rank, row in enumerate(rows, 1):
        row.rank = rank
    return rows
