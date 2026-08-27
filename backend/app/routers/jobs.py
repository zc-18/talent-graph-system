"""岗位管理路由：公开岗位的只读查询与归档。"""
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import or_, func
from .. import models
from ..db import get_db
from ..guards import require_write
from ..schemas import JobUpsert, ManualSkillEdit
from ..services import graph_service, role_contract
from ..auth import Actor
from ..permissions import require_admin

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.get("")
def list_jobs(category: str | None = None, level: str | None = None,
              track: str | None = None, industry: str | None = None,
              recruitment_type: str | None = None,
              is_new: bool | None = None, q: str | None = None,
              page: int = 1, size: int = 50, db: Session = Depends(get_db)):
    page, size = max(1, page), min(100, max(1, size))
    query = db.query(models.Job).filter(models.Job.status == "published")
    if category and category != "全部":
        query = query.filter(models.Job.category == category)
    if level and level != "全部":
        query = query.filter(models.Job.level == level)
    if track and track != "全部":
        query = query.filter(models.Job.track == track)
    if industry and industry != "全部":
        query = query.filter(models.Job.industry == industry)
    if recruitment_type and recruitment_type != "全部":
        query = query.filter(models.Job.recruitment_type == recruitment_type)
    if is_new is not None:
        query = query.filter(models.Job.is_new == is_new)
    if q:
        query = query.filter(or_(models.Job.name.like(f"%{q}%"), models.Job.summary.like(f"%{q}%")))
    total = query.count()
    jobs = query.order_by(models.Job.is_new.desc(), models.Job.confidence.desc(),
                          models.Job.id) \
                .offset((page - 1) * size).limit(size).all()
    # 必备能力项数一次 GROUP BY 聚合出来（历史实现是每个岗位一条 count()）。
    # 只数**粗粒度**：详情页的「必备技能 (N)」也只列粗粒度项，细粒度技能点作为
    # 「细分技能点」chip 挂在父项下。两处口径不一致时卡片会喊 120、点进去只有 38，
    # 这种自相矛盾比数字大小本身更伤可信度。
    req_counts = dict(db.query(models.JobSkill.job_id, func.count(models.JobSkill.id))
                      .join(models.Skill, models.Skill.id == models.JobSkill.skill_id)
                      .filter(models.JobSkill.job_id.in_({j.id for j in jobs}),
                              models.JobSkill.importance == "required",
                              models.JobSkill.status == "active",
                              models.Skill.parent_id.is_(None))
                      .group_by(models.JobSkill.job_id).all()) if jobs else {}
    items = []
    for j in jobs:
        items.append({"id": j.id, "name": j.name, "category": j.category, "level": j.level,
                      "track": j.track, "industry": j.industry,
                      "recruitment_type": j.recruitment_type,
                      "is_new": bool(j.is_new), "confidence": j.confidence,
                      "evidence_count": j.evidence_count, "emergence_score": j.emergence_score,
                      "required_count": req_counts.get(j.id, 0), "version": j.version,
                      "summary": (j.summary or "")[:120]})
    return {"total": total, "page": page, "size": size, "items": items}


@router.get("/{job_id}")
def get_job(job_id: int, db: Session = Depends(get_db)):
    job = db.query(models.Job).get(job_id)
    if not job:
        raise HTTPException(404, "岗位不存在")
    return graph_service.job_to_dict(db, job)


@router.get("/{job_id}/contract")
def get_contract(job_id: int, seniority: str | None = None,
                 recruitment_type: str | None = None, db: Session = Depends(get_db)):
    job = db.query(models.Job).get(job_id)
    if not job or job.status != "published":
        raise HTTPException(404, "岗位不存在")
    return role_contract.build_contract_from_job(
        db, job, seniority=seniority or job.level or "unspecified",
        recruitment_type=recruitment_type or job.recruitment_type or "mixed",
        track=job.track or "software", industry=job.industry or "general")


@router.get("/{job_id}/versions")
def get_versions(job_id: int, page: int = 1, size: int = 20,
                 db: Session = Depends(get_db)):
    job = db.query(models.Job).get(job_id)
    if not job or job.status != "published":
        raise HTTPException(404, "岗位不存在")
    page, size = max(1, page), min(100, max(1, size))
    q = db.query(models.JobVersion).filter(models.JobVersion.job_id == job_id)
    total = q.count()
    rows = q.order_by(models.JobVersion.version.desc()).offset((page - 1) * size).limit(size).all()
    if not rows and page == 1:
        contract = role_contract.build_contract_from_job(
            db, job, seniority=job.level or "unspecified",
            recruitment_type=job.recruitment_type or "mixed",
            track=job.track or "software", industry=job.industry or "general")
        return {"items": [{"id": None, "job_id": job.id, "version": job.version or 1,
                           "status": "published", "effective_at": None,
                           "evidence_window": contract.get("evidence_window"),
                           "summary": job.summary, "created_by": None,
                           "contract": contract, "skills": [], "synthetic": True}],
                "total": 1, "page": 1, "size": size}
    version_ids = [row.id for row in rows]
    snapshot_rows = (db.query(models.JobVersionSkill, models.Skill)
                     .join(models.Skill, models.Skill.id == models.JobVersionSkill.skill_id)
                     .filter(models.JobVersionSkill.job_version_id.in_(version_ids)).all()) if rows else []
    skills: dict[int, list[dict]] = {}
    for snap, skill in snapshot_rows:
        skills.setdefault(snap.job_version_id, []).append({
            "skill_id": skill.id, "name": skill.name,
            "capability_cluster": snap.capability_cluster,
            "importance": snap.importance, "status": snap.status, "weight": snap.weight,
            "confidence": snap.confidence, "level_required": snap.level_required,
            "factors": snap.factors or {}, "evidence_refs": snap.evidence_refs or []})
    return {"items": [{"id": row.id, "job_id": row.job_id, "version": row.version,
                       "status": row.status,
                       "effective_at": row.effective_at.isoformat() if row.effective_at else None,
                       "evidence_window": row.evidence_window or {}, "summary": row.summary,
                       "created_by": row.created_by,
                       "contract": row.contract_snapshot or {},
                       "skills": skills.get(row.id, []), "synthetic": False,
                       "created_at": row.created_at.isoformat()} for row in rows],
            "total": total, "page": page, "size": size}


@router.get("/{job_id}/evidence")
def job_evidence(job_id: int, db: Session = Depends(get_db)):
    """返回岗位各能力项的溯源证据（反幻觉可解释性）。

    JD 类证据 join RawJD 补全 来源平台/公司/发布时间/原始URL —— 溯源看得见（2026-07 整改）。

    性能：固定 4 条 SQL（能力项 / 技能名 / 证据 / RawJD 溯源字段），组装在内存完成。
    历史实现对每个能力项各查 1 次 Skill + 1 次 Evidence、再对每条证据查 1 次 RawJD
    （能力项最多的岗位是 2536 条 SQL / 45 秒），且 RawJD 整行取回会连 raw_text 全文一起传。
    """
    E, R, SK = models.Evidence, models.RawJD, models.Skill
    js = db.query(models.JobSkill).filter(models.JobSkill.job_id == job_id).all()
    skill_names = dict(db.query(SK.id, SK.name).filter(
        SK.id.in_({j.skill_id for j in js})).all()) if js else {}
    # 证据按 (job_skill_id, id) 排序分组：复现原「逐能力项查询」时二级索引
    # ix_evidence_job_skill_id 的天然返回序，证据顺序逐字节不变。
    ev_by_js: dict[int, list] = {}
    if js:
        for e in db.query(E.job_skill_id, E.source_type, E.snippet, E.source_url,
                          E.weight, E.source_name, E.raw_jd_id) \
                   .filter(E.job_skill_id.in_({j.id for j in js})) \
                   .order_by(E.job_skill_id, E.id).all():
            ev_by_js.setdefault(e.job_skill_id, []).append(e)
    jd_ids = {e.raw_jd_id for evs in ev_by_js.values() for e in evs if e.raw_jd_id}
    jd_rows = {r.id: r for r in db.query(
        R.id, R.source_url, R.platform, R.source, R.company, R.publish_date, R.job_title)
        .filter(R.id.in_(jd_ids)).all()} if jd_ids else {}

    out = []
    for j in js:
        ev_list = []
        for e in ev_by_js.get(j.id, ()):
            item = {"type": e.source_type, "snippet": e.snippet,
                    "url": e.source_url or "", "weight": e.weight,
                    "source": e.source_name or "", "company": "", "publish_date": None}
            if e.raw_jd_id:
                rj = jd_rows.get(e.raw_jd_id)
                if rj:
                    item["url"] = item["url"] or (rj.source_url or "")
                    item["source"] = item["source"] or (rj.platform or rj.source or "")
                    item["company"] = rj.company or ""
                    item["publish_date"] = rj.publish_date.strftime("%Y-%m-%d") if rj.publish_date else None
                    item["job_title"] = rj.job_title or ""
            ev_list.append(item)
        out.append({"skill": skill_names.get(j.skill_id, ""), "importance": j.importance,
                    "confidence": j.confidence, "factors": j.factors,
                    "source_count": j.source_count,
                    "status": j.status, "evidences": ev_list})
    return {"job_id": job_id, "items": out}


@router.get("/{job_id}/authority")
def job_authority(job_id: int, db: Session = Depends(get_db)):
    """岗位的权威佐证（部委政策文件 / 头部机构报告）—— 新兴岗位有理有据。"""
    rows = db.query(models.AuthorityEvidence).filter(
        models.AuthorityEvidence.job_id == job_id).order_by(
        models.AuthorityEvidence.publish_date.desc()).all()
    return {"job_id": job_id, "items": [{
        "kind": r.kind, "title": r.title, "issuer": r.issuer,
        "publish_date": r.publish_date.strftime("%Y-%m-%d") if r.publish_date else None,
        "url": r.url, "excerpt": r.excerpt, "local_file": r.local_file,
    } for r in rows]}


@router.post("", dependencies=[Depends(require_write)])
def create_or_update_job(payload: JobUpsert, actor: Actor = Depends(require_admin),
                         db: Session = Depends(get_db)):
    """Retired: public jobs may only be published through governed workflows."""
    raise HTTPException(410, {
        "code": "DIRECT_JOB_WRITE_RETIRED",
        "message": "公共岗位不能直接创建，请通过新岗位发现候选与管理员审核发布",
        "workflow": "/api/discovery/runs",
    })


@router.post("/manual-edit", dependencies=[Depends(require_write)])
def manual_edit_skill(payload: ManualSkillEdit, actor: Actor = Depends(require_admin),
                      db: Session = Depends(get_db)):
    """Retired: capability edits must be reviewed as versioned evolution runs."""
    raise HTTPException(410, {
        "code": "DIRECT_JOB_WRITE_RETIRED",
        "message": "公共岗位能力不能直接编辑，请通过管理员演化工作流发布新版本",
        "workflow": "/api/admin/evolution-runs",
    })


@router.delete("/{job_id}", dependencies=[Depends(require_write)])
def delete_job(job_id: int, actor: Actor = Depends(require_admin),
               db: Session = Depends(get_db)):
    """Retired: archival must be proposed and reviewed as a versioned change."""
    raise HTTPException(410, {
        "code": "DIRECT_JOB_WRITE_RETIRED",
        "message": "公共岗位不能直接归档，请通过管理员演化工作流审核",
        "workflow": "/api/admin/evolution-runs",
    })
