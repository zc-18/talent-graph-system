"""人才与团队盘点路由（老师意见⑧：团队成员简历读取学习）。

只读接口 + 一个写接口（把真实简历加进团队）。写接口沿用与 /api/match 相同的
隐私口径：原文与姓名只在内存里解析，落库只留脱敏后的技能要素。
"""
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.orm import Session

from .. import models
from ..db import get_db
from ..services import resume as resume_svc, talent as talent_svc

router = APIRouter(prefix="/api/talent", tags=["talent"])


@router.get("/corpus")
def corpus(db: Session = Depends(get_db)):
    """简历语料台账：批次、来源、许可证、条数、脱敏说明。"""
    return talent_svc.corpus_overview(db)


@router.get("/profiles")
def profiles(page: int = 1, size: int = 20, source_type: str | None = None,
             language: str | None = None, cluster: str | None = None,
             db: Session = Depends(get_db)):
    """脱敏人才画像分页列表。"""
    q = db.query(models.TalentProfile)
    if source_type:
        q = q.filter(models.TalentProfile.source_type == source_type)
    if language:
        q = q.filter(models.TalentProfile.language == language)
    if cluster:
        q = q.filter(models.TalentProfile.target_cluster == cluster)
    total = q.count()
    rows = (q.order_by(models.TalentProfile.id)
            .offset(max(0, (page - 1)) * size).limit(min(size, 100)).all())
    job_names = {j.id: j.name for j in db.query(models.Job.id, models.Job.name).all()}
    return {"total": total, "page": page, "size": size, "items": [{
        "id": p.id, "code": p.code, "source_type": p.source_type,
        "source_name": p.source_name, "source_url": p.source_url, "license": p.license,
        "language": p.language, "target_cluster": p.target_cluster,
        "matched_job_id": p.matched_job_id,
        "matched_job_name": job_names.get(p.matched_job_id),
        "years_experience": p.years_experience, "education": p.education,
        "skill_count": p.skill_count, "skills": (p.skills or [])[:40],
        "text_len": p.text_len, "quality_score": p.quality_score, "holdout": p.holdout,
    } for p in rows]}


@router.get("/supply-demand")
def supply_demand(job_id: int, db: Session = Depends(get_db)):
    """岗位需求 vs 人才供给缺口。"""
    data = talent_svc.supply_demand(db, job_id)
    if not data:
        raise HTTPException(404, "岗位不存在")
    return data


@router.get("/teams")
def teams(db: Session = Depends(get_db)):
    rows = db.query(models.Team).order_by(models.Team.id).all()
    sizes = {}
    for t in rows:
        sizes[t.id] = db.query(models.TeamMember).filter(
            models.TeamMember.team_id == t.id).count()
    return {"items": [{"id": t.id, "name": t.name, "description": t.description,
                       "size": sizes.get(t.id, 0)} for t in rows]}


@router.get("/teams/{team_id}")
def team_detail(team_id: int, db: Session = Depends(get_db)):
    team = db.query(models.Team).get(team_id)
    if not team:
        raise HTTPException(404, "团队不存在")
    members = db.query(models.TeamMember).filter(
        models.TeamMember.team_id == team_id).order_by(models.TeamMember.id).all()
    tp = {p.id: p for p in db.query(models.TalentProfile).filter(
        models.TalentProfile.id.in_([m.talent_id for m in members])).all()} if members else {}
    job_names = {j.id: j.name for j in db.query(models.Job.id, models.Job.name).all()}
    return {"id": team.id, "name": team.name, "description": team.description,
            "members": [{
                "member_id": m.id, "display_name": m.display_name, "role_label": m.role_label,
                "talent_id": m.talent_id,
                "talent_code": tp[m.talent_id].code if m.talent_id in tp else None,
                "source_type": tp[m.talent_id].source_type if m.talent_id in tp else None,
                "skill_count": tp[m.talent_id].skill_count if m.talent_id in tp else 0,
                "skills": (tp[m.talent_id].skills or [])[:30] if m.talent_id in tp else [],
                "target_cluster": tp[m.talent_id].target_cluster if m.talent_id in tp else None,
                "matched_job_name": job_names.get(tp[m.talent_id].matched_job_id)
                if m.talent_id in tp else None,
            } for m in members]}


@router.get("/teams/{team_id}/gap")
def team_gap(team_id: int, job_id: int, db: Session = Depends(get_db)):
    """团队对目标岗位的能力缺口：已覆盖 / 谁能补 / 还缺谁。"""
    data = talent_svc.team_gap(db, team_id, job_id)
    if not data:
        raise HTTPException(404, "团队或岗位不存在")
    return data


@router.get("/aliases")
def aliases(status: str | None = None, limit: int = 200, db: Session = Depends(get_db)):
    """从简历学到（或拒绝）的技能表述台账。"""
    q = db.query(models.SkillAlias)
    if status:
        q = q.filter(models.SkillAlias.status == status)
    total = q.count()
    rows = q.order_by(models.SkillAlias.talent_count.desc(),
                      models.SkillAlias.alias).limit(min(limit, 500)).all()
    return {"total": total, "items": [{
        "alias": a.alias, "canonical": a.canonical, "status": a.status,
        "talent_count": a.talent_count, "confidence": a.confidence,
        "reason": a.reject_reason, "skill_id": a.skill_id,
    } for a in rows]}


@router.post("/teams/{team_id}/members/upload")
async def upload_member_resume(team_id: int, file: UploadFile = File(...),
                               display_name: str = Form("成员"),
                               role_label: str = Form(""),
                               target_cluster: str = Form(""),
                               db: Session = Depends(get_db)):
    """上传一份真实简历，解析后作为团队成员加入（意见⑧的"读取"入口）。

    隐私口径与 /api/match/resume/upload 一致：原文与姓名只在内存里参与本次解析，
    落库只有脱敏后的技能要素；display_name 由上传方自己给的化名，不取简历里的姓名。
    """
    team = db.query(models.Team).get(team_id)
    if not team:
        raise HTTPException(404, "团队不存在")
    content = await file.read()
    if len(content) > 8 * 1024 * 1024:
        raise HTTPException(400, "文件过大(>8MB)")
    text = resume_svc.extract_text(file.filename, content)
    if not text.strip():
        raise HTTPException(422, "无法从文件中提取文本，请检查文件格式")
    text = resume_svc.mask_contacts(text)
    parsed = resume_svc.parse_resume(text)

    seq = db.query(models.TalentProfile).count() + 1
    code = f"T{seq:03d}"
    while db.query(models.TalentProfile).filter_by(code=code).first():
        seq += 1
        code = f"T{seq:03d}"

    job = None
    if target_cluster:
        from ..services.ingest import title_key
        job = db.query(models.Job).filter(
            models.Job.name == title_key("", target_cluster)).first()

    skills = parsed.get("skills", []) or []
    tp = models.TalentProfile(
        code=code, batch_id=None, source_type="upload",
        source_name="团队成员上传", source_url="", license="本人授权",
        language="zh", target_cluster=target_cluster or None,
        matched_job_id=job.id if job else None,
        years_experience=parsed.get("years_experience", 0) or 0,
        education=(parsed.get("education") or "")[:64],
        skills=skills, skill_levels=parsed.get("skill_levels", {}) or {},
        raw_skill_terms=parsed.get("raw_skill_terms", []) or [],
        skill_count=len(skills), text_len=len(text), quality_score=0.0, holdout=False)
    db.add(tp)
    db.flush()
    db.add(models.TeamMember(team_id=team_id, talent_id=tp.id,
                             display_name=display_name or code,
                             role_label=role_label or None))
    db.commit()
    return {"talent_id": tp.id, "code": tp.code, "skill_count": len(skills),
            "skills": skills,
            "privacy_notice": "简历原文与姓名等个人信息仅用于本次解析，不在服务端留存"}
