"""全景图谱与统计路由。"""
from __future__ import annotations
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from .. import models
from ..db import get_db
from ..services import graph_service
from ..services.taxonomy import CATEGORIES

router = APIRouter(prefix="/api/graph", tags=["graph"])


@router.get("/panorama")
def panorama(category: str | None = None, level: str | None = None,
             min_confidence: float = 0.0, db: Session = Depends(get_db)):
    """新一代信息技术岗位全景图谱（岗位-技能点关系网）。"""
    return graph_service.panoramic_graph(db, category, level, min_confidence)


@router.get("/stats")
def stats(db: Session = Depends(get_db)):
    return graph_service.stats_overview(db)


@router.get("/categories")
def categories(db: Session = Depends(get_db)):
    return {"categories": CATEGORIES, "levels": ["junior", "middle", "senior", "expert"]}


@router.get("/pipeline-stats")
def pipeline_stats(db: Session = Depends(get_db)):
    """全流程闭环漏斗 + 数据源采集台账（驾驶舱用，均为轻量聚合查询）。"""
    from sqlalchemy import func, cast, String as SAString

    collected = db.query(func.count(models.RawJD.id)).scalar() or 0
    after_dedup = db.query(func.count(models.RawJD.id)).filter(
        models.RawJD.is_duplicate == False).scalar() or 0  # noqa: E712
    validated_caps = db.query(func.count(models.JobSkill.id)).filter(
        models.JobSkill.status == "active").scalar() or 0
    total_caps = db.query(func.count(models.JobSkill.id)).scalar() or 0
    jobs = db.query(func.count(models.Job.id)).scalar() or 0
    skills = db.query(func.count(models.Skill.id)).scalar() or 0

    platforms = [
        {"platform": p or "未知", "count": c,
         "latest": latest.strftime("%Y-%m-%d") if latest else None}
        for p, c, latest in db.query(
            models.RawJD.platform, func.count(models.RawJD.id),
            func.max(models.RawJD.collected_at)
        ).group_by(models.RawJD.platform).order_by(func.count(models.RawJD.id).desc()).all()
    ]

    batches = [
        {"batch_key": b.batch_key, "platform": b.platform, "tier": b.tier,
         "kept": b.kept,
         "finished_at": b.finished_at.strftime("%Y-%m-%d %H:%M") if b.finished_at else None}
        for b in db.query(models.CrawlBatch).order_by(
            models.CrawlBatch.finished_at.desc()).limit(12).all()
    ]

    manual_edits = db.query(func.count(models.CapabilityChange.id)).filter(
        cast(models.CapabilityChange.data_source, SAString).like("%manual%")).scalar() or 0
    evolution_runs = db.query(func.count(func.distinct(
        func.concat(models.CapabilityChange.job_id, "-", models.CapabilityChange.version)
    ))).scalar() or 0

    return {
        "funnel": {
            "collected": collected,
            "after_dedup": after_dedup,
            "parsed": after_dedup,
            "validated_caps": validated_caps,
            "filtered_caps": max(total_caps - validated_caps, 0),
            "jobs": jobs,
            "skills": skills,
        },
        "platforms": platforms,
        "batches": batches,
        "loop": {"manual_edits": manual_edits, "evolution_runs": evolution_runs},
    }


@router.get("/skill/{skill_id}")
def skill_detail(skill_id: int, db: Session = Depends(get_db)):
    """某技能点关联的岗位（图谱下钻）。"""
    sk = db.query(models.Skill).get(skill_id)
    if not sk:
        return {"error": "not found"}
    js = db.query(models.JobSkill).filter(models.JobSkill.skill_id == skill_id,
                                          models.JobSkill.status == "active").all()
    jobs = []
    for j in js:
        job = db.query(models.Job).get(j.job_id)
        if job:
            jobs.append({"job_id": job.id, "name": job.name, "category": job.category,
                         "importance": j.importance, "weight": j.weight})
    return {"skill": {"id": sk.id, "name": sk.name, "category": sk.category,
                      "skill_type": sk.skill_type}, "related_jobs": jobs}


@router.get("/skill-tree")
def skill_tree(db: Session = Depends(get_db)):
    """按技术栈聚合技能点（图谱视图：按技术栈切换）。"""
    skills = db.query(models.Skill).all()
    tree: dict[str, list] = {}
    for sk in skills:
        deg = db.query(models.JobSkill).filter(models.JobSkill.skill_id == sk.id,
                                               models.JobSkill.status == "active").count()
        if deg == 0:
            continue
        tree.setdefault(sk.category or "其他", []).append(
            {"id": sk.id, "name": sk.name, "skill_type": sk.skill_type, "degree": deg})
    children = [{"name": cat, "children": sorted(items, key=lambda x: x["degree"], reverse=True)}
                for cat, items in tree.items()]
    return {"name": "新一代信息技术", "children": children}
