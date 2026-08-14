"""既有岗位能力动态更新（演化）路由。"""
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from .. import models
from ..db import get_db
from ..schemas import EvolveRequest
from ..services import extraction, hallucination, evolution, graph_service, leveling
from ..guards import is_read_only, READ_ONLY_MESSAGE
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


@router.get("/{job_id}/levels")
def level_profiles(job_id: int, db: Session = Depends(get_db)):
    """岗位分级能力画像（初/中/高级）。"""
    job = db.query(models.Job).get(job_id)
    if not job:
        raise HTTPException(404, "岗位不存在")
    rows = db.query(models.JobLevelSkill, models.Skill).join(
        models.Skill, models.JobLevelSkill.skill_id == models.Skill.id).filter(
        models.JobLevelSkill.job_id == job_id).all()
    levels: dict = {}
    for jls, sk in rows:
        lv = levels.setdefault(jls.level, {"jd_count": jls.jd_count, "skills": []})
        lv["skills"].append({
            "skill_id": sk.id, "name": sk.name, "importance": jls.importance,
            "weight": jls.weight, "level_required": jls.level_required,
            "confidence": jls.confidence, "factors": jls.factors,
            "source_count": jls.source_count})
    for lv in levels.values():
        lv["skills"].sort(key=lambda s: (s["importance"] == "required", s["weight"]),
                          reverse=True)
    order = {"junior": 0, "middle": 1, "senior": 2}
    available = sorted(levels.keys(), key=lambda x: order.get(x, 9))
    return {"job_id": job_id, "available": available, "levels": levels}


@router.get("/{job_id}/level-diff")
def level_diff(job_id: int, frm: str, to: str, db: Session = Depends(get_db)):
    """两级能力画像对比（晋升视角的新增/强化/默认前提）。"""
    if frm not in leveling.LEVELS or to not in leveling.LEVELS:
        raise HTTPException(400, "级别必须是 junior/middle/senior")
    job = db.query(models.Job).get(job_id)
    if not job:
        raise HTTPException(404, "岗位不存在")
    return leveling.level_diff(db, job_id, frm, to)


@router.post("/update")
def update_job(payload: EvolveRequest, db: Session = Depends(get_db)):
    """用新 JD 驱动既有岗位能力演化：识别变化→标注增删改→落库。"""
    job = db.query(models.Job).get(payload.job_id)
    if not job:
        raise HTTPException(404, "岗位不存在")

    # 旧能力项快照（技能名一次批量取，历史实现逐条 Skill.get 是 N+1）
    old_js = db.query(models.JobSkill).filter(models.JobSkill.job_id == job.id,
                                              models.JobSkill.status == "active").all()
    sk_name = dict(db.query(models.Skill.id, models.Skill.name).filter(
        models.Skill.id.in_({j.skill_id for j in old_js})).all()) if old_js else {}
    old_caps = []
    for j in old_js:
        nm = sk_name.get(j.skill_id)
        if nm is not None:
            old_caps.append({"name": nm, "importance": j.importance, "weight": j.weight,
                             "level_required": j.level_required, "confidence": j.confidence,
                             "source_count": j.source_count})

    # 解析新 JD → 聚合
    agg_input = []
    for i, jd_text in enumerate(payload.new_jds):
        parsed = extraction.parse_jd(jd_text)
        agg_input.append({"required_skills": parsed.get("required_skills", []),
                          "bonus_skills": parsed.get("bonus_skills", []),
                          # 细粒度技能点也参与演化聚合：漏了它，演化只会更新粗粒度大概念，
                          # 而赛题要求颗粒度到技能点，岗位跑过演化反而比新建时更粗。
                          "fine_skills": parsed.get("fine_skills", []),
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
    if is_read_only():
        # 只读模式：推演照跑、结果照返回，只是不落库。演示效果与写入版一致，
        # 但演示站的图谱不会被访客点击改动（两次线上事故均由此而来）。
        return {"ok": True, "job_id": job.id, "dry_run": True,
                "evolution": {"version": job.version, "changes_applied": 0,
                              "added": sum(1 for c in changes if c["change_type"] == "add"),
                              "deleted": sum(1 for c in changes if c["change_type"] == "delete"),
                              "modified": sum(1 for c in changes if c["change_type"] == "modify")},
                "changes": changes, "stats": agg["stats"],
                "job": graph_service.job_to_dict(db, job),
                "notice": READ_ONLY_MESSAGE}
    result = evolution.apply_evolution(db, job, agg["capabilities"], changes)
    return {"ok": True, "job_id": job.id, "evolution": result, "changes": changes,
            "stats": agg["stats"], "job": graph_service.job_to_dict(db, job)}
