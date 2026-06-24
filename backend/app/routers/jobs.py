"""岗位管理路由：列表、详情、人工优化、手动能力项编辑。"""
from __future__ import annotations
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import or_
from .. import models
from ..db import get_db
from ..schemas import JobUpsert, ManualSkillEdit
from ..services import graph_service
from ..services.taxonomy import normalize_skill, skill_category, skill_type

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.get("")
def list_jobs(category: str | None = None, level: str | None = None,
              is_new: bool | None = None, q: str | None = None,
              page: int = 1, size: int = 50, db: Session = Depends(get_db)):
    query = db.query(models.Job).filter(models.Job.status == "published")
    if category and category != "全部":
        query = query.filter(models.Job.category == category)
    if level and level != "全部":
        query = query.filter(models.Job.level == level)
    if is_new is not None:
        query = query.filter(models.Job.is_new == is_new)
    if q:
        query = query.filter(or_(models.Job.name.like(f"%{q}%"), models.Job.summary.like(f"%{q}%")))
    total = query.count()
    jobs = query.order_by(models.Job.is_new.desc(), models.Job.confidence.desc()) \
                .offset((page - 1) * size).limit(size).all()
    items = []
    for j in jobs:
        req = db.query(models.JobSkill).filter(models.JobSkill.job_id == j.id,
                                               models.JobSkill.importance == "required",
                                               models.JobSkill.status == "active").count()
        items.append({"id": j.id, "name": j.name, "category": j.category, "level": j.level,
                      "is_new": bool(j.is_new), "confidence": j.confidence,
                      "evidence_count": j.evidence_count, "emergence_score": j.emergence_score,
                      "required_count": req, "version": j.version,
                      "summary": (j.summary or "")[:120]})
    return {"total": total, "page": page, "size": size, "items": items}


@router.get("/{job_id}")
def get_job(job_id: int, db: Session = Depends(get_db)):
    job = db.query(models.Job).get(job_id)
    if not job:
        raise HTTPException(404, "岗位不存在")
    return graph_service.job_to_dict(db, job)


@router.get("/{job_id}/evidence")
def job_evidence(job_id: int, db: Session = Depends(get_db)):
    """返回岗位各能力项的溯源证据（反幻觉可解释性）。"""
    js = db.query(models.JobSkill).filter(models.JobSkill.job_id == job_id).all()
    out = []
    for j in js:
        sk = db.query(models.Skill).get(j.skill_id)
        evs = db.query(models.Evidence).filter(models.Evidence.job_skill_id == j.id).all()
        out.append({"skill": sk.name if sk else "", "importance": j.importance,
                    "confidence": j.confidence, "source_count": j.source_count,
                    "status": j.status,
                    "evidences": [{"type": e.source_type, "snippet": e.snippet,
                                   "url": e.source_url, "weight": e.weight} for e in evs]})
    return {"job_id": job_id, "items": out}


@router.post("")
def create_or_update_job(payload: JobUpsert, db: Session = Depends(get_db)):
    """人工创建/优化岗位定义。"""
    caps = []
    for s in payload.required_skills:
        nm = normalize_skill(s.name)
        caps.append({"name": nm, "importance": "required", "weight": s.weight,
                     "level_required": s.level_required, "confidence": s.confidence or 0.9,
                     "source_count": 1, "category": skill_category(nm),
                     "skill_type": skill_type(nm), "status": "active", "evidence": []})
    req_names = {c["name"] for c in caps}
    for s in payload.bonus_skills:
        nm = normalize_skill(s.name)
        if nm in req_names:
            continue
        caps.append({"name": nm, "importance": "bonus", "weight": s.weight,
                     "level_required": s.level_required, "confidence": s.confidence or 0.85,
                     "source_count": 1, "category": skill_category(nm),
                     "skill_type": skill_type(nm), "status": "active", "evidence": []})
    job = graph_service.upsert_job(
        db, job_title=payload.name, category=payload.category, level=payload.level,
        responsibilities=payload.core_responsibilities, scenarios=payload.typical_scenarios,
        capabilities=caps, is_new=payload.is_new, summary=payload.summary,
        source_summary={"origin": "manual"}, with_embedding=False)
    return {"ok": True, "job": graph_service.job_to_dict(db, job)}


@router.post("/manual-edit")
def manual_edit_skill(payload: ManualSkillEdit, db: Session = Depends(get_db)):
    """对单个能力项进行人工增删改，并记录为变更（支持人工优化）。"""
    job = db.query(models.Job).get(payload.job_id)
    if not job:
        raise HTTPException(404, "岗位不存在")
    nm = normalize_skill(payload.skill_name)
    sk = graph_service.upsert_skill(db, nm)
    js = db.query(models.JobSkill).filter(models.JobSkill.job_id == job.id,
                                          models.JobSkill.skill_id == sk.id).first()
    change_type, old_value, new_value = payload.action, None, None
    if payload.action == "remove":
        if js:
            old_value = {"importance": js.importance, "weight": js.weight}
            js.status = "deprecated"
        change_type = "delete"
    elif payload.action in ("add", "update"):
        new_value = {"importance": payload.importance, "weight": payload.weight,
                     "level_required": payload.level_required}
        if js:
            old_value = {"importance": js.importance, "weight": js.weight}
            js.importance = payload.importance
            js.weight = payload.weight
            js.level_required = payload.level_required
            js.status = "active"
            change_type = "modify"
        else:
            db.add(models.JobSkill(job_id=job.id, skill_id=sk.id, importance=payload.importance,
                                   weight=payload.weight, level_required=payload.level_required,
                                   confidence=0.9, source_count=1, status="active",
                                   first_seen=datetime.utcnow(), last_seen=datetime.utcnow()))
            change_type = "add"
    job.version = (job.version or 1) + 1
    db.add(models.CapabilityChange(job_id=job.id, version=job.version, change_type=change_type,
                                   skill_name=nm, importance=payload.importance,
                                   old_value=old_value, new_value=new_value,
                                   reason=payload.reason, data_source={"origin": "manual"},
                                   confidence=0.95))
    db.commit()
    return {"ok": True, "job": graph_service.job_to_dict(db, job)}


@router.delete("/{job_id}")
def delete_job(job_id: int, db: Session = Depends(get_db)):
    job = db.query(models.Job).get(job_id)
    if not job:
        raise HTTPException(404, "岗位不存在")
    db.delete(job)
    db.commit()
    return {"ok": True}
