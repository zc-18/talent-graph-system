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
