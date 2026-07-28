"""全景图谱与统计路由。"""
from __future__ import annotations
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func
from .. import models
from ..db import get_db
from ..services import graph_service
from ..services.taxonomy import CATEGORIES

router = APIRouter(prefix="/api/graph", tags=["graph"])


@router.get("/panorama")
def panorama(category: str | None = None, level: str | None = None,
             min_confidence: float = 0.0,
             max_skills: int = graph_service.DEFAULT_MAX_SKILLS,
             db: Session = Depends(get_db)):
    """新一代信息技术岗位全景图谱（岗位-技能点关系网）。

    max_skills：技能节点上限（默认 400，当前语料不触发）。触发截断时
    stats.truncated=True 且 stats.skills_total 给出截断前总数。
    """
    return graph_service.panoramic_graph(db, category, level, min_confidence,
                                         max_skills=max_skills)


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
    """某技能点关联的岗位（图谱下钻）。

    性能：3 条 SQL（技能 1 + 关系 1 + 岗位批量 1）。历史实现对每条关系
    单独 query 一次 Job（N+1）。
    """
    sk = db.query(models.Skill.id, models.Skill.name, models.Skill.category,
                  models.Skill.skill_type).filter(models.Skill.id == skill_id).first()
    if not sk:
        return {"error": "not found"}
    js = db.query(models.JobSkill).filter(models.JobSkill.skill_id == skill_id,
                                          models.JobSkill.status == "active").all()
    job_rows = {r.id: r for r in db.query(models.Job.id, models.Job.name, models.Job.category)
                .filter(models.Job.id.in_({j.job_id for j in js})).all()} if js else {}
    jobs = []
    for j in js:
        job = job_rows.get(j.job_id)
        if job:
            jobs.append({"job_id": job.id, "name": job.name, "category": job.category,
                         "importance": j.importance, "weight": j.weight})
    return {"skill": {"id": sk.id, "name": sk.name, "category": sk.category,
                      "skill_type": sk.skill_type}, "related_jobs": jobs}


@router.get("/skill-tree")
def skill_tree(db: Session = Depends(get_db)):
    """按技术栈聚合技能点（图谱视图：按技术栈切换）。

    性能：整棵树只用 2 条 SQL —— 度数由一次 GROUP BY 聚合出来，技能只取树需要的
    4 个列（不拉 embedding 等大字段）。历史实现对全部技能逐个 count()，
    本库 4567 个技能 = 4567 次公网往返，单请求 70+ 秒。
    """
    JS, SK = models.JobSkill, models.Skill
    degrees = dict(db.query(JS.skill_id, func.count(JS.id))
                   .filter(JS.status == "active").group_by(JS.skill_id).all())
    tree: dict[str, list] = {}
    # 按 id 升序遍历：与原「db.query(Skill).all() 全表扫描」的返回序（InnoDB 主键序）一致，
    # 保证分类出现顺序和同 degree 技能的相对顺序逐字节不变。
    for sk_id, name, category, stype in db.query(
            SK.id, SK.name, SK.category, SK.skill_type).order_by(SK.id).all():
        deg = degrees.get(sk_id, 0)
        if deg == 0:
            continue
        tree.setdefault(category or "其他", []).append(
            {"id": sk_id, "name": name, "skill_type": stype, "degree": deg})
    children = [{"name": cat, "children": sorted(items, key=lambda x: x["degree"], reverse=True)}
                for cat, items in tree.items()]
    return {"name": "新一代信息技术", "children": children}
