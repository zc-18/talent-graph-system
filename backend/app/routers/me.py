"""Owner-scoped personal profiles and reproducible match history."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models
from ..auth import Actor, current_actor
from ..db import get_db

router = APIRouter(prefix="/api/me", tags=["me"])


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
