"""Organization-scoped recruitment batches, ranking and Top-K selection."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from time import perf_counter
from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import func
from sqlalchemy.orm import sessionmaker
from sqlalchemy.orm import Session

from .. import models
from ..auth import Actor, actor_for_user, add_audit, add_usage
from ..db import get_db
from ..ownership import require_org
from ..permissions import require_hr
from ..schemas import (CandidateSelectRequest, CandidateSkillsCorrectionRequest,
                       RecruitmentBatchRequest)
from ..services import recruitment, role_contract, talent as talent_service
from ..services.resume import ResumeFileError

router = APIRouter(prefix="/api/hr", tags=["hr"])
log = logging.getLogger(__name__)

# A parsed-and-scored candidate is written as "succeeded" by ``recruitment.process_file``
# but as "completed" by the showcase seeder. Ranking used to test ``== "succeeded"``, so
# every seeded batch answered "暂无候选人排名" while its own progress card said 14 成功 —
# the counts come from the batch columns, the rows from this predicate, and the two
# disagreed. Both spellings mean the same terminal state; keep them together in one set
# so counting, ranking and Top-K selection can never drift apart again.
SUCCEEDED_STATUSES = ("succeeded", "completed")
PROCESSED_STATUSES = SUCCEEDED_STATUSES + ("failed",)

# A batch whose worker died leaves the row in queued/processing forever: the UI polls it
# indefinitely and uploads stay blocked by the "批次正在处理中" guard. There is no
# scheduler here, so the read paths reap it.
STALE_BATCH_TIMEOUT = timedelta(minutes=30)


def _safe_commit(db: Session, context: str) -> bool:
    """Commit side-effects of a read route without letting them break the read.

    ``list_candidates`` / ``get_candidate`` append an AuditLog row and commit inside a
    GET. If that commit raises (lock wait, replica, disk) the caller gets a 500 even
    though the data it asked for was already loaded and serialized. The audit trail is
    secondary to serving the read, so a failure is rolled back and logged instead.
    """
    try:
        db.commit()
        return True
    except Exception:  # noqa: BLE001
        log.exception("deferred write failed on %s; read continues", context)
        db.rollback()
        return False


def _reap_stale_batches(db: Session, batches: list[models.RecruitmentBatch]) -> None:
    """Mark batches whose worker never reported back as failed. Best effort."""
    cutoff = datetime.utcnow() - STALE_BATCH_TIMEOUT
    stale = [row for row in batches
             if row.status in {"queued", "processing"}
             and (row.updated_at or row.created_at) < cutoff]
    if not stale:
        return
    for row in stale:
        row.status = "failed"
    _safe_commit(db, "recruitment.batch.reap")


def _batch_dict(db: Session, row: models.RecruitmentBatch) -> dict:
    job = db.query(models.Job).get(row.target_job_id)
    failures = db.query(models.BatchCandidate).filter(
        models.BatchCandidate.batch_id == row.id,
        models.BatchCandidate.parse_status == "failed").order_by(
        models.BatchCandidate.id).all()
    return {"id": row.id, "name": row.name, "status": row.status,
            "organization_id": row.organization_id,
            "job_id": row.target_job_id, "job_name": job.name if job else None,
            "job_version_id": row.target_job_version_id,
            "job_version": row.target_job_version,
            "progress": {"total": row.total_count, "processed": row.processed_count,
                         "succeeded": row.succeeded_count, "failed": row.failed_count},
            "failures": [{"candidate_id": item.id, "code": item.display_code,
                          "error_code": item.error_code,
                          "message": item.error_detail} for item in failures],
            "created_at": row.created_at.isoformat(),
            "updated_at": row.updated_at.isoformat()}


def _candidate_dict(db: Session, row: models.BatchCandidate, *, detail: bool = False) -> dict:
    item = {
        "id": row.id, "batch_id": row.batch_id, "code": row.display_code,
        "status": row.parse_status, "error_code": row.error_code,
        "error_detail": row.error_detail, "overall_score": row.overall_score,
        "dimension_scores": row.dimension_scores or {}, "rank": row.rank,
        "note": row.note, "created_at": row.created_at.isoformat(),
    }
    if detail:
        profile = db.get(models.ResumeProfile, row.resume_profile_id) if row.resume_profile_id else None
        selection_count = db.query(models.CandidateSelection).filter(
            models.CandidateSelection.batch_candidate_id == row.id).count()
        item.update({
            "resume_profile_id": row.resume_profile_id,
            "skills": profile.skills if profile else [],
            "skill_levels": profile.skill_levels if profile else {},
            "years_experience": profile.years_experience if profile else None,
            "education": profile.education if profile else None,
            "retention_expires_at": (profile.retention_expires_at.isoformat()
                                     if profile and profile.retention_expires_at else None),
            "selected": selection_count > 0,
            "selection_count": selection_count,
            "result": row.result_snapshot or {},
        })
    return item


def _batch_contract(db: Session, batch: models.RecruitmentBatch) -> dict:
    if isinstance(batch.contract_snapshot, dict) and batch.contract_snapshot.get("clusters") is not None:
        return batch.contract_snapshot
    job = db.get(models.Job, batch.target_job_id)
    if job is None:
        raise HTTPException(409, "批次关联岗位不存在")
    version = (db.get(models.JobVersion, batch.target_job_version_id)
               if batch.target_job_version_id else None)
    if version and isinstance(version.contract_snapshot, dict):
        batch.contract_snapshot = version.contract_snapshot
        db.flush()
        return batch.contract_snapshot
    if (job.version or 1) != batch.target_job_version:
        raise HTTPException(409, "批次缺少创建时的岗位契约快照，不能使用当前版本重算历史排名")
    batch.contract_snapshot = role_contract.build_contract_from_job(
        db, job, seniority=job.level or "unspecified",
        recruitment_type=job.recruitment_type or "mixed",
        track=job.track or "software", industry=job.industry or "general")
    db.flush()
    return batch.contract_snapshot


def _terminal_status(batch: models.RecruitmentBatch) -> str:
    if batch.processed_count != batch.total_count:
        return "failed"
    return "completed_with_errors" if batch.failed_count else "completed"


def _refresh_counts(db: Session, batch: models.RecruitmentBatch) -> None:
    rows = db.query(models.BatchCandidate).filter(
        models.BatchCandidate.batch_id == batch.id).all()
    # Keep the upload-time total while the worker is still materializing candidates;
    # otherwise the UI denominator shrinks to the number processed so far.
    batch.total_count = max(batch.total_count or 0, len(rows))
    batch.processed_count = sum(r.parse_status in PROCESSED_STATUSES for r in rows)
    batch.succeeded_count = sum(r.parse_status in SUCCEEDED_STATUSES for r in rows)
    batch.failed_count = sum(r.parse_status == "failed" for r in rows)


def _process_batch(bind, batch_id: int, items: list[tuple[str, bytes, str | None]],
                   retention_days: int, user_id: int) -> None:
    """Background worker. Raw bytes live only in this in-memory task and are never persisted."""
    db = sessionmaker(bind=bind)()
    started = perf_counter()
    try:
        batch = db.query(models.RecruitmentBatch).get(batch_id)
        user = db.query(models.AppUser).get(user_id)
        if not batch or not user:
            return
        actor = actor_for_user(db, user)
        contract = _batch_contract(db, batch)
        batch.status = "processing"
        db.commit()
        for filename, content, preset_error in items:
            recruitment.process_file(db, batch, filename, content, contract,
                                     retention_days, preset_error)
            _refresh_counts(db, batch)
            db.commit()
        recruitment.rerank(db, batch.id)
        _refresh_counts(db, batch)
        if batch.processed_count != batch.total_count:
            batch.status = "failed"
        else:
            batch.status = "completed_with_errors" if batch.failed_count else "completed"
        add_audit(db, actor, "recruitment.upload", "recruitment_batch", batch.id,
                  summary={"status": batch.status, "count": batch.total_count})
        add_usage(db, actor, "batch_resume", int((perf_counter() - started) * 1000),
                  batch.failed_count == 0)
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
        batch = db.query(models.RecruitmentBatch).get(batch_id)
        if batch:
            batch.status = "failed"
            db.commit()
    finally:
        db.close()


@router.post("/recruitment-batches", status_code=201)
def create_batch(payload: RecruitmentBatchRequest, actor: Actor = Depends(require_hr),
                 db: Session = Depends(get_db)):
    if actor.organization_id is None:
        raise HTTPException(403, "HR 未加入有效组织")
    if payload.idempotency_key:
        existing = db.query(models.RecruitmentBatch).filter(
            models.RecruitmentBatch.organization_id == actor.organization_id,
            models.RecruitmentBatch.idempotency_key == payload.idempotency_key).first()
        if existing:
            return {**_batch_dict(db, existing), "idempotent_replay": True}
    job = db.query(models.Job).filter(models.Job.id == payload.target_job_id,
                                      models.Job.status == "published").first()
    if not job:
        raise HTTPException(404, "岗位不存在")
    version = db.query(models.JobVersion).filter(
        models.JobVersion.job_id == job.id,
        models.JobVersion.version == (job.version or 1)).first()
    contract = role_contract.build_contract_from_job(
        db, job, seniority=job.level or "unspecified",
        recruitment_type=job.recruitment_type or "mixed",
        track=job.track or "software", industry=job.industry or "general")
    batch = models.RecruitmentBatch(
        organization_id=actor.organization_id, created_by=actor.user_id,
        name=payload.name, target_job_id=job.id,
        target_job_version_id=version.id if version else None,
        target_job_version=job.version or 1, contract_snapshot=contract, status="created",
        idempotency_key=payload.idempotency_key)
    db.add(batch)
    db.flush()
    add_audit(db, actor, "recruitment.create", "recruitment_batch", batch.id,
              summary={"status": "created", "version": batch.target_job_version})
    db.commit()
    return {**_batch_dict(db, batch), "idempotent_replay": False}


@router.get("/recruitment-batches")
def list_batches(status: str | None = None, page: int = 1, size: int = 20,
                 actor: Actor = Depends(require_hr), db: Session = Depends(get_db)):
    page, size = max(1, page), min(100, max(1, size))
    q = db.query(models.RecruitmentBatch).filter(
        models.RecruitmentBatch.organization_id == actor.organization_id)
    if status:
        q = q.filter(models.RecruitmentBatch.status == status)
    total = q.count()
    rows = q.order_by(models.RecruitmentBatch.updated_at.desc()).offset((page - 1) * size).limit(size).all()
    _reap_stale_batches(db, rows)
    return {"items": [_batch_dict(db, row) for row in rows],
            "total": total, "page": page, "size": size}


@router.get("/recruitment-batches/{batch_id}")
def get_batch(batch_id: int, actor: Actor = Depends(require_hr),
              db: Session = Depends(get_db)):
    batch = db.query(models.RecruitmentBatch).get(batch_id)
    if not batch:
        raise HTTPException(404, "批次不存在")
    require_org(batch, actor)
    _reap_stale_batches(db, [batch])
    return _batch_dict(db, batch)


@router.get("/recruitment-batches/{batch_id}/candidates")
def list_candidates(batch_id: int, status: str | None = None,
                    page: int = 1, size: int = 20,
                    actor: Actor = Depends(require_hr), db: Session = Depends(get_db)):
    batch = db.get(models.RecruitmentBatch, batch_id)
    if not batch:
        raise HTTPException(404, "批次不存在")
    require_org(batch, actor)
    valid_statuses = {"pending", "processing", "failed", *SUCCEEDED_STATUSES}
    if status and status not in valid_statuses:
        raise HTTPException(422, "候选状态无效")
    page, size = max(1, page), min(100, max(1, size))
    query = db.query(models.BatchCandidate).filter(
        models.BatchCandidate.batch_id == batch.id)
    if status:
        query = query.filter(models.BatchCandidate.parse_status == status)
    total = query.count()
    rows = query.order_by(models.BatchCandidate.id).offset(
        (page - 1) * size).limit(size).all()
    # Serialize before the audit commit: a rollback there must not disturb the payload.
    items = [_candidate_dict(db, row) for row in rows]
    add_audit(db, actor, "recruitment.candidate.list", "recruitment_batch", batch.id,
              summary={"count": len(rows), "status": status or "all"})
    _safe_commit(db, "recruitment.candidate.list")
    return {"items": items, "total": total, "page": page, "size": size}


@router.get("/recruitment-batches/{batch_id}/candidates/{candidate_id}")
def get_candidate(batch_id: int, candidate_id: int,
                  actor: Actor = Depends(require_hr), db: Session = Depends(get_db)):
    batch = db.get(models.RecruitmentBatch, batch_id)
    if not batch:
        raise HTTPException(404, "批次不存在")
    require_org(batch, actor)
    candidate = db.query(models.BatchCandidate).filter(
        models.BatchCandidate.id == candidate_id,
        models.BatchCandidate.batch_id == batch.id).first()
    if not candidate:
        raise HTTPException(404, "候选人不存在")
    detail = _candidate_dict(db, candidate, detail=True)
    add_audit(db, actor, "recruitment.candidate.view", "batch_candidate", candidate.id,
              summary={"status": candidate.parse_status})
    _safe_commit(db, "recruitment.candidate.view")
    return detail


@router.delete("/recruitment-batches/{batch_id}/candidates/{candidate_id}", status_code=204)
def delete_candidate(batch_id: int, candidate_id: int,
                     actor: Actor = Depends(require_hr), db: Session = Depends(get_db)):
    batch = db.get(models.RecruitmentBatch, batch_id)
    if not batch:
        raise HTTPException(404, "批次不存在")
    require_org(batch, actor)
    candidate = db.query(models.BatchCandidate).filter(
        models.BatchCandidate.id == candidate_id,
        models.BatchCandidate.batch_id == batch.id).first()
    if not candidate:
        raise HTTPException(404, "候选人不存在")
    selected = db.query(models.CandidateSelection).filter(
        models.CandidateSelection.batch_candidate_id == candidate.id).count()
    team_links = (db.query(models.TeamMember).filter(
        models.TeamMember.resume_profile_id == candidate.resume_profile_id).count()
        if candidate.resume_profile_id else 0)
    match_links = (db.query(models.MatchRun).filter(
        models.MatchRun.resume_profile_id == candidate.resume_profile_id).count()
        if candidate.resume_profile_id else 0)
    if selected or team_links or match_links:
        raise HTTPException(409, "候选已关联入选记录、团队成员或匹配历史，不能直接删除")
    profile_id = candidate.resume_profile_id
    add_audit(db, actor, "recruitment.candidate.delete", "batch_candidate", candidate.id,
              summary={"status": candidate.parse_status})
    db.delete(candidate)
    db.flush()
    if profile_id and not db.query(models.BatchCandidate).filter(
            models.BatchCandidate.resume_profile_id == profile_id).first():
        profile = db.get(models.ResumeProfile, profile_id)
        if profile:
            db.delete(profile)
    remaining = db.query(models.BatchCandidate).filter(
        models.BatchCandidate.batch_id == batch.id).all()
    batch.total_count = len(remaining)
    batch.processed_count = sum(row.parse_status in PROCESSED_STATUSES for row in remaining)
    batch.succeeded_count = sum(row.parse_status in SUCCEEDED_STATUSES for row in remaining)
    batch.failed_count = sum(row.parse_status == "failed" for row in remaining)
    if batch.status not in {"queued", "processing"}:
        batch.status = _terminal_status(batch)
    db.commit()


@router.post("/recruitment-batches/{batch_id}/candidates/{candidate_id}/retry")
async def retry_candidate(batch_id: int, candidate_id: int,
                          file: UploadFile = File(...),
                          authorization_confirmed: bool = Form(...),
                          retention_days: int = Form(90),
                          actor: Actor = Depends(require_hr), db: Session = Depends(get_db)):
    batch = db.get(models.RecruitmentBatch, batch_id)
    if not batch:
        raise HTTPException(404, "批次不存在")
    require_org(batch, actor)
    candidate = db.query(models.BatchCandidate).filter(
        models.BatchCandidate.id == candidate_id,
        models.BatchCandidate.batch_id == batch.id).first()
    if not candidate:
        raise HTTPException(404, "候选人不存在")
    if not authorization_confirmed:
        raise HTTPException(422, {"code": "AUTHORIZATION_REQUIRED",
                                  "message": "必须确认已获得候选人授权"})
    if retention_days < 1 or retention_days > 365:
        raise HTTPException(422, "保留期限必须为 1-365 天")
    if batch.status in {"queued", "processing"}:
        raise HTTPException(409, "批次正在处理中")
    content = await file.read()
    try:
        recruitment.retry_candidate(
            db, batch, candidate, file.filename or "retry", content,
            _batch_contract(db, batch), retention_days)
    except ValueError as exc:
        if str(exc) == "DUPLICATE_FILE":
            raise HTTPException(409, "替代文件已存在于当前批次") from None
        raise HTTPException(409, "仅失败候选可以重试") from None
    recruitment.rerank(db, batch.id)
    _refresh_counts(db, batch)
    batch.status = _terminal_status(batch)
    add_audit(db, actor, "recruitment.candidate.retry", "batch_candidate", candidate.id,
              summary={"status": candidate.parse_status, "action": "retry"})
    db.commit()
    return {"candidate": _candidate_dict(db, candidate, detail=True),
            "batch": _batch_dict(db, batch)}


@router.patch("/recruitment-batches/{batch_id}/candidates/{candidate_id}/skills")
def correct_candidate_skills(batch_id: int, candidate_id: int,
                             payload: CandidateSkillsCorrectionRequest,
                             actor: Actor = Depends(require_hr), db: Session = Depends(get_db)):
    batch = db.get(models.RecruitmentBatch, batch_id)
    if not batch:
        raise HTTPException(404, "批次不存在")
    require_org(batch, actor)
    candidate = db.query(models.BatchCandidate).filter(
        models.BatchCandidate.id == candidate_id,
        models.BatchCandidate.batch_id == batch.id).first()
    if not candidate:
        raise HTTPException(404, "候选人不存在")
    if not payload.confirmed:
        raise HTTPException(422, "必须人工确认脱敏技能后才能重算")
    try:
        recruitment.correct_candidate_skills(
            db, candidate, _batch_contract(db, batch), payload.skills,
            payload.skill_levels, payload.note)
    except ValueError as exc:
        messages = {"CANDIDATE_NOT_READY": "候选尚未成功解析",
                    "PROFILE_NOT_FOUND": "候选画像不存在",
                    "CONTACTS_NOT_ALLOWED": "技能修正不得包含联系方式或身份信息"}
        raise HTTPException(409 if str(exc) != "CONTACTS_NOT_ALLOWED" else 422,
                            messages.get(str(exc), "候选技能修正失败")) from None
    add_audit(db, actor, "recruitment.candidate.correct", "batch_candidate", candidate.id,
              summary={"status": "corrected", "count": len(payload.skills)})
    db.commit()
    return _candidate_dict(db, candidate, detail=True)


@router.post("/recruitment-batches/{batch_id}/files")
async def upload_files(batch_id: int, background_tasks: BackgroundTasks,
                       files: list[UploadFile] = File(...),
                       authorization_confirmed: bool = Form(...),
                       retention_days: int = Form(90),
                       actor: Actor = Depends(require_hr), db: Session = Depends(get_db)):
    batch = db.query(models.RecruitmentBatch).get(batch_id)
    if not batch:
        raise HTTPException(404, "批次不存在")
    require_org(batch, actor)
    if not authorization_confirmed:
        raise HTTPException(422, {"code": "AUTHORIZATION_REQUIRED",
                                  "message": "必须确认已获得候选人授权"})
    if retention_days < 1 or retention_days > 365:
        raise HTTPException(422, "保留期限必须为 1-365 天")
    if not files or len(files) > 100:
        raise HTTPException(422, "单批次每次上传 1-100 个文件")
    if batch.status in {"queued", "processing"}:
        raise HTTPException(409, "批次正在处理中")
    items: list[tuple[str, bytes, str | None]] = []
    for upload in files:
        content = await upload.read()
        try:
            items.extend(recruitment.expand_upload(upload.filename or "upload", content))
        except ResumeFileError as exc:
            items.append((upload.filename or "upload", b"", exc.code))
    if not items:
        raise HTTPException(422, "没有可处理的文件")
    existing_hashes = {
        value for (value,) in db.query(models.BatchCandidate.file_hash).filter(
            models.BatchCandidate.batch_id == batch.id).all()
    }
    pending_items: list[tuple[str, bytes, str | None]] = []
    seen_hashes = set(existing_hashes)
    for item in items:
        digest = recruitment.file_digest(*item)
        if digest in seen_hashes:
            continue
        seen_hashes.add(digest)
        pending_items.append(item)
    if not pending_items:
        return {**_batch_dict(db, batch), "idempotent_replay": True}
    batch.status = "queued"
    batch.total_count = len(existing_hashes) + len(pending_items)
    db.commit()
    response = {**_batch_dict(db, batch), "idempotent_replay": False}
    background_tasks.add_task(_process_batch, db.get_bind(), batch.id, pending_items,
                              retention_days, actor.user_id)
    return response


@router.get("/recruitment-batches/{batch_id}/ranking")
def ranking(batch_id: int, min_score: float = 0, page: int = 1, size: int = 50,
            actor: Actor = Depends(require_hr), db: Session = Depends(get_db)):
    batch = db.query(models.RecruitmentBatch).get(batch_id)
    if not batch:
        raise HTTPException(404, "批次不存在")
    require_org(batch, actor)
    page, size = max(1, page), min(100, max(1, size))
    # coalesce, not a bare >=: an unscored row compares NULL >= 0 -> NULL in SQL and is
    # silently dropped, which reads as "no candidates" rather than "not scored yet".
    q = db.query(models.BatchCandidate).filter(
        models.BatchCandidate.batch_id == batch.id,
        models.BatchCandidate.parse_status.in_(SUCCEEDED_STATUSES),
        func.coalesce(models.BatchCandidate.overall_score, 0.0) >= min_score)
    total = q.count()
    rows = q.order_by(models.BatchCandidate.rank, models.BatchCandidate.id).offset(
        (page - 1) * size).limit(size).all()
    return {"items": [{"rank": r.rank, "candidate_id": r.id, "code": r.display_code,
                       "overall_score": r.overall_score,
                       "dimension_scores": r.dimension_scores or {},
                       "status": r.parse_status} for r in rows],
            "total": total, "page": page, "size": size,
            "job_version": batch.target_job_version}


@router.post("/recruitment-batches/{batch_id}/select")
def select_candidates(batch_id: int, payload: CandidateSelectRequest,
                      actor: Actor = Depends(require_hr), db: Session = Depends(get_db)):
    batch = db.query(models.RecruitmentBatch).get(batch_id)
    if not batch:
        raise HTTPException(404, "批次不存在")
    require_org(batch, actor)
    rows = db.query(models.BatchCandidate).filter(
        models.BatchCandidate.batch_id == batch.id,
        models.BatchCandidate.id.in_(set(payload.candidate_ids)),
        models.BatchCandidate.parse_status.in_(SUCCEEDED_STATUSES)).all()
    if len(rows) != len(set(payload.candidate_ids)):
        raise HTTPException(422, "候选 ID 包含不存在、失败或跨批次记录")
    if payload.team_id is None:
        team = models.Team(name=f"{batch.name} Top-K", organization_id=actor.organization_id,
                           created_by=actor.user_id, target_job_id=batch.target_job_id)
        db.add(team)
        db.flush()
    else:
        team = db.query(models.Team).get(payload.team_id)
        if not team:
            raise HTTPException(404, "团队不存在")
        require_org(team, actor)
    before = talent_service.team_gap(db, team.id, batch.target_job_id)
    before_rate = float(before.get("coverage_rate", 0)) if before else 0.0
    selected = 0
    for row in rows:
        exists = db.query(models.CandidateSelection).filter(
            models.CandidateSelection.batch_candidate_id == row.id,
            models.CandidateSelection.team_id == team.id).first()
        if exists:
            continue
        member = db.query(models.TeamMember).filter(
            models.TeamMember.team_id == team.id,
            models.TeamMember.resume_profile_id == row.resume_profile_id).first()
        if not member:
            db.add(models.TeamMember(team_id=team.id, talent_id=None,
                                     resume_profile_id=row.resume_profile_id,
                                     display_name=row.display_code,
                                     role_label="候选人"))
            db.flush()
        db.add(models.CandidateSelection(
            batch_candidate_id=row.id, team_id=team.id, selected_by=actor.user_id,
            before_coverage=before_rate))
        selected += 1
    db.flush()
    after = talent_service.team_gap(db, team.id, batch.target_job_id)
    after_rate = float(after.get("coverage_rate", 0)) if after else before_rate
    db.query(models.CandidateSelection).filter(
        models.CandidateSelection.team_id == team.id,
        models.CandidateSelection.after_coverage.is_(None)).update(
        {models.CandidateSelection.after_coverage: after_rate}, synchronize_session=False)
    add_audit(db, actor, "recruitment.select", "team", team.id,
              summary={"count": selected, "status": "selected"})
    add_usage(db, actor, "team_review", 0, True)
    db.commit()
    return {"batch_id": batch.id, "team_id": team.id, "selected": selected,
            "before_coverage": before_rate, "after_coverage": after_rate,
            "coverage_delta": round(after_rate - before_rate, 4)}
