"""既有岗位能力动态更新（演化）路由。"""
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from .. import models
from ..db import get_db
from ..schemas import EvolveRequest
from ..services import extraction, hallucination, evolution, graph_service
from .. import clients

router = APIRouter(prefix="/api/evolution", tags=["evolution"])


@router.get("/{job_id}/changes")
def change_history(job_id: int, db: Session = Depends(get_db)):
    """岗位能力变更历史（新增/删除/修改）。"""
    changes = db.query(models.CapabilityChange).filter(
        models.CapabilityChange.job_id == job_id).order_by(
        models.CapabilityChange.created_at.desc()).all()
    return {"job_id": job_id, "items": [{
        "version": c.version, "change_type": c.change_type, "skill_name": c.skill_name,
        "importance": c.importance, "old_value": c.old_value, "new_value": c.new_value,
        "reason": c.reason, "data_source": c.data_source, "confidence": c.confidence,
        "created_at": c.created_at.isoformat() if c.created_at else None} for c in changes]}


@router.post("/update")
def update_job(payload: EvolveRequest, db: Session = Depends(get_db)):
    """用新 JD 驱动既有岗位能力演化：识别变化→标注增删改→落库。"""
    job = db.query(models.Job).get(payload.job_id)
    if not job:
        raise HTTPException(404, "岗位不存在")

    # 旧能力项快照
    old_js = db.query(models.JobSkill).filter(models.JobSkill.job_id == job.id,
                                              models.JobSkill.status == "active").all()
    old_caps = []
    for j in old_js:
        sk = db.query(models.Skill).get(j.skill_id)
        if sk:
            old_caps.append({"name": sk.name, "importance": j.importance, "weight": j.weight,
                             "level_required": j.level_required, "confidence": j.confidence,
                             "source_count": j.source_count})

    # 解析新 JD → 聚合
    agg_input = []
    for i, jd_text in enumerate(payload.new_jds):
        parsed = extraction.parse_jd(jd_text)
        agg_input.append({"required_skills": parsed.get("required_skills", []),
                          "bonus_skills": parsed.get("bonus_skills", []),
                          "lag_days": 0, "is_duplicate": False, "raw_jd_id": None,
                          "source": "evolution-input"})
    if not agg_input:
        raise HTTPException(400, "请提供至少一条新 JD 文本")

    # 与历史能力合并交叉验证（旧能力作为先验来源之一）
    for c in old_caps:
        agg_input.append({"required_skills": [{"name": c["name"], "importance": "required",
                                               "level": c["level_required"], "category": "",
                                               "skill_type": "hard", "raw": c["name"]}]
                          if c["importance"] == "required" else [],
                          "bonus_skills": [{"name": c["name"], "importance": "bonus",
                                            "level": "familiar", "category": "", "skill_type": "hard",
                                            "raw": c["name"]}] if c["importance"] == "bonus" else [],
                          "lag_days": 120, "is_duplicate": False, "raw_jd_id": None,
                          "source": "history"})

    web_skills = set()
    if payload.use_web:
        res = clients.multi_source_search(f"{job.name} 最新 技能要求 2026", max_results=4)
        blob = " ".join((r.get("content") or "") for r in res).lower()
        from ..services.taxonomy import SYNONYMS
        for kw, nm in SYNONYMS.items():
            if kw in blob:
                web_skills.add(nm)

    agg = hallucination.aggregate_capabilities(agg_input, web_evidence_skills=web_skills)
    changes = evolution.compute_changes(old_caps, agg["capabilities"])
    result = evolution.apply_evolution(db, job, agg["capabilities"], changes)
    return {"ok": True, "job_id": job.id, "evolution": result, "changes": changes,
            "stats": agg["stats"], "job": graph_service.job_to_dict(db, job)}
