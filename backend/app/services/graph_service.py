"""图谱持久化与查询服务。

把交叉验证后的能力项落库为 Job / Skill / JobSkill / Evidence，并提供全景图谱查询。
"""
from __future__ import annotations
import re
from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy import func
from .. import models, clients
from .taxonomy import skill_category, skill_type
from .hallucination import job_confidence


def slugify(name: str) -> str:
    s = re.sub(r"[^\w一-鿿]+", "-", name.strip().lower()).strip("-")
    return s or "job"


def upsert_skill(db: Session, name: str, category: str | None = None,
                 stype: str | None = None, with_embedding: bool = False) -> models.Skill:
    sk = db.query(models.Skill).filter(models.Skill.normalized_name == name).first()
    if sk:
        return sk
    sk = models.Skill(name=name, normalized_name=name,
                      category=category or skill_category(name),
                      skill_type=stype or skill_type(name))
    if with_embedding:
        sk.embedding = clients.embed(name)
    db.add(sk)
    db.flush()
    return sk


def upsert_job(db: Session, *, job_title: str, category: str, level: str,
               responsibilities: list, scenarios: list, capabilities: list[dict],
               is_new: bool = False, summary: str = "", source_summary: dict | None = None,
               emergence_score: float = 0.0, with_embedding: bool = True) -> models.Job:
    """根据聚合能力项创建/更新岗位及其能力关系。"""
    slug = slugify(job_title)
    job = db.query(models.Job).filter(models.Job.slug == slug).first()
    if not job:
        job = models.Job(name=job_title, slug=slug)
        db.add(job)
        db.flush()
    job.category = category
    job.level = level
    job.is_new = is_new
    job.summary = summary
    job.core_responsibilities = responsibilities
    job.typical_scenarios = scenarios
    job.emergence_score = emergence_score
    job.source_summary = source_summary or {}
    active_caps = [c for c in capabilities if c.get("status") == "active"]
    job.confidence = job_confidence(capabilities)
    job.evidence_count = sum(c.get("source_count", 0) for c in active_caps)
    if with_embedding:
        job.embedding = clients.embed(f"{job_title} {summary} " + " ".join(c["name"] for c in active_caps[:15]))

    # 清空旧能力关系后重建（先删证据子表，避免外键约束）
    old_js_ids = [r[0] for r in db.query(models.JobSkill.id)
                  .filter(models.JobSkill.job_id == job.id).all()]
    if old_js_ids:
        db.query(models.Evidence).filter(
            models.Evidence.job_skill_id.in_(old_js_ids)).delete(synchronize_session=False)
        db.query(models.JobSkill).filter(
            models.JobSkill.id.in_(old_js_ids)).delete(synchronize_session=False)
    db.flush()
    for c in active_caps:
        sk = upsert_skill(db, c["name"], c.get("category"), c.get("skill_type"), with_embedding)
        js = models.JobSkill(
            job_id=job.id, skill_id=sk.id, importance=c["importance"],
            weight=c.get("weight", 0.5), level_required=c.get("level_required", "familiar"),
            confidence=c.get("confidence", 0.0), source_count=c.get("source_count", 0),
            status="active", first_seen=datetime.utcnow(), last_seen=datetime.utcnow())
        db.add(js)
        db.flush()
        for ev in c.get("evidence", [])[:6]:
            db.add(models.Evidence(
                job_skill_id=js.id, raw_jd_id=ev.get("raw_jd_id"),
                source_type=ev.get("source_type", "jd"), source_url=ev.get("source_url", ""),
                snippet=(ev.get("snippet") or "")[:500], weight=ev.get("weight", 1.0)))
    db.commit()
    return job


def job_to_dict(db: Session, job: models.Job, include_candidates: bool = False) -> dict:
    js = db.query(models.JobSkill).filter(models.JobSkill.job_id == job.id).all()
    skills = []
    for j in js:
        sk = db.query(models.Skill).get(j.skill_id)
        if not sk:
            continue
        skills.append({
            "id": j.id, "skill_id": sk.id, "name": sk.name, "category": sk.category,
            "skill_type": sk.skill_type, "importance": j.importance, "weight": j.weight,
            "level_required": j.level_required, "confidence": j.confidence,
            "source_count": j.source_count, "status": j.status,
        })
    required = [s for s in skills if s["importance"] == "required"]
    bonus = [s for s in skills if s["importance"] == "bonus"]
    return {
        "id": job.id, "name": job.name, "slug": job.slug, "category": job.category,
        "level": job.level, "is_new": job.is_new, "status": job.status,
        "summary": job.summary, "core_responsibilities": job.core_responsibilities or [],
        "typical_scenarios": job.typical_scenarios or [],
        "required_skills": sorted(required, key=lambda x: x["weight"], reverse=True),
        "bonus_skills": sorted(bonus, key=lambda x: x["weight"], reverse=True),
        "confidence": job.confidence, "evidence_count": job.evidence_count,
        "emergence_score": job.emergence_score, "version": job.version,
        "source_summary": job.source_summary or {},
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "updated_at": job.updated_at.isoformat() if job.updated_at else None,
    }


def panoramic_graph(db: Session, category: str | None = None, level: str | None = None,
                    min_confidence: float = 0.0) -> dict:
    """构建全景图谱：岗位节点 + 技能点节点 + 关系边。"""
    q = db.query(models.Job).filter(models.Job.status == "published")
    if category and category != "全部":
        q = q.filter(models.Job.category == category)
    if level and level != "全部":
        q = q.filter(models.Job.level == level)
    jobs = q.all()

    nodes, edges = [], []
    skill_seen: dict[int, dict] = {}
    for job in jobs:
        nodes.append({"id": f"job-{job.id}", "name": job.name, "type": "job",
                      "category": job.category, "level": job.level, "is_new": bool(job.is_new),
                      "confidence": job.confidence, "value": 30})
        js = db.query(models.JobSkill).filter(
            models.JobSkill.job_id == job.id,
            models.JobSkill.status == "active",
            models.JobSkill.confidence >= min_confidence).all()
        for j in js:
            sk = db.query(models.Skill).get(j.skill_id)
            if not sk:
                continue
            if sk.id not in skill_seen:
                skill_seen[sk.id] = {"id": f"skill-{sk.id}", "name": sk.name, "type": "skill",
                                     "category": sk.category, "skill_type": sk.skill_type,
                                     "value": 10, "degree": 0}
            skill_seen[sk.id]["degree"] += 1
            edges.append({"source": f"job-{job.id}", "target": f"skill-{sk.id}",
                          "importance": j.importance, "weight": round(j.weight, 3),
                          "confidence": round(j.confidence, 3)})
    for s in skill_seen.values():
        s["value"] = 8 + min(40, s["degree"] * 4)
        nodes.append(s)
    return {"nodes": nodes, "edges": edges,
            "stats": {"jobs": len(jobs), "skills": len(skill_seen), "relations": len(edges)}}


def stats_overview(db: Session) -> dict:
    total_jobs = db.query(func.count(models.Job.id)).scalar() or 0
    new_jobs = db.query(func.count(models.Job.id)).filter(models.Job.is_new == True).scalar() or 0  # noqa: E712
    total_skills = db.query(func.count(models.Skill.id)).scalar() or 0
    total_jds = db.query(func.count(models.RawJD.id)).scalar() or 0
    dup_jds = db.query(func.count(models.RawJD.id)).filter(models.RawJD.is_duplicate == True).scalar() or 0  # noqa: E712
    by_cat = dict(db.query(models.Job.category, func.count(models.Job.id)).group_by(models.Job.category).all())
    avg_conf = db.query(func.avg(models.Job.confidence)).scalar() or 0
    return {
        "total_jobs": total_jobs, "new_jobs": new_jobs, "total_skills": total_skills,
        "total_jds": total_jds, "duplicate_jds": dup_jds,
        "categories": by_cat, "avg_confidence": round(float(avg_conf), 4),
    }
