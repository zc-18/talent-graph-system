"""人才与团队盘点路由（老师意见⑧：团队成员简历读取学习）。

只读接口 + 一个写接口（把真实简历加进团队）。写接口沿用与 /api/match 相同的
隐私口径：原文与姓名只在内存里解析，落库只留脱敏后的技能要素。
"""
from __future__ import annotations
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.orm import Session
from sqlalchemy import func, or_

from .. import models
from ..db import get_db
from ..guards import require_org_append
from ..auth import Actor, optional_actor, add_audit
from ..ownership import require_org
from ..schemas import TeamCreateRequest, TeamMemberRequest
from ..services import resume as resume_svc, talent as talent_svc

router = APIRouter(prefix="/api/talent", tags=["talent"])


def _page(page: int, size: int, *, maximum: int = 100) -> tuple[int, int]:
    return max(1, page), min(maximum, max(1, size))


def _team_state(db: Session, team: models.Team) -> dict:
    member_count = db.query(models.TeamMember).filter(
        models.TeamMember.team_id == team.id).count()
    state = {"member_count": member_count, "coverage_rate": None}
    if team.target_job_id:
        gap = talent_svc.team_gap(db, team.id, team.target_job_id)
        if gap:
            state["coverage_rate"] = float(gap.get("coverage_rate", 0))
    return state


def _add_team_event(db: Session, actor: Actor, team: models.Team, action: str,
                    *, member_id: int | None = None, details: dict | None = None,
                    before: dict | None = None, after: dict | None = None) -> None:
    db.add(models.TeamEvent(
        team_id=team.id, organization_id=team.organization_id,
        actor_user_id=actor.user_id, action=action, member_id=member_id,
        details=details or {}, before_snapshot=before, after_snapshot=after))


@router.get("/corpus")
def corpus(db: Session = Depends(get_db)):
    """简历语料台账：批次、来源、许可证、条数、脱敏说明。"""
    return talent_svc.corpus_overview(db)


@router.get("/profiles")
def profiles(page: int = 1, size: int = 20, source_type: str | None = None,
             language: str | None = None, cluster: str | None = None,
             db: Session = Depends(get_db)):
    """脱敏人才画像分页列表。"""
    page, size = _page(page, size)
    q = db.query(models.TalentProfile)
    if source_type:
        q = q.filter(models.TalentProfile.source_type == source_type)
    if language:
        q = q.filter(models.TalentProfile.language == language)
    if cluster:
        q = q.filter(models.TalentProfile.target_cluster == cluster)
    total = q.count()
    rows = (q.order_by(models.TalentProfile.id)
            .offset((page - 1) * size).limit(size).all())
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
def teams(page: int = 1, size: int = 20, actor: Actor = Depends(optional_actor),
          db: Session = Depends(get_db)):
    page, size = _page(page, size)
    q = db.query(models.Team)
    if actor.role == "hr" and actor.organization_id is not None:
        q = q.filter(or_(models.Team.organization_id.is_(None),
                         models.Team.organization_id == actor.organization_id))
    elif actor.role != "admin":
        q = q.filter(models.Team.organization_id.is_(None))
    total = q.count()
    rows = q.order_by(models.Team.id).offset((page - 1) * size).limit(size).all()
    team_ids = {t.id for t in rows}
    sizes = dict(db.query(models.TeamMember.team_id, func.count(models.TeamMember.id)).filter(
        models.TeamMember.team_id.in_(team_ids)).group_by(
        models.TeamMember.team_id).all()) if team_ids else {}
    return {"items": [{"id": t.id, "name": t.name, "description": t.description,
                       "target_job_id": t.target_job_id,
                       "size": sizes.get(t.id, 0)} for t in rows],
            "total": total, "page": page, "size": size}


@router.post("/teams", status_code=201)
def create_team(payload: TeamCreateRequest, actor: Actor = Depends(require_org_append),
                db: Session = Depends(get_db)):
    job = db.query(models.Job).filter(
        models.Job.id == payload.target_job_id,
        models.Job.status == "published").first()
    if not job:
        raise HTTPException(404, "目标岗位不存在")
    team = models.Team(
        name=payload.name.strip(), description=payload.description,
        organization_id=actor.organization_id, created_by=actor.user_id,
        target_job_id=job.id)
    db.add(team)
    db.flush()
    after = _team_state(db, team)
    _add_team_event(db, actor, team, "created", after=after,
                    details={"target_job_id": job.id})
    add_audit(db, actor, "team.create", "team", team.id,
              summary={"status": "created", "target_job_id": job.id})
    db.commit()
    return {"id": team.id, "name": team.name, "description": team.description,
            "target_job_id": team.target_job_id, "size": 0}


@router.get("/teams/{team_id}")
def team_detail(team_id: int, actor: Actor = Depends(optional_actor), db: Session = Depends(get_db)):
    team = db.query(models.Team).get(team_id)
    if not team:
        raise HTTPException(404, "团队不存在")
    if team.organization_id is not None:
        if actor.user is None or (actor.role != "admin" and actor.organization_id != team.organization_id):
            raise HTTPException(404, "团队不存在")
    members = db.query(models.TeamMember).filter(
        models.TeamMember.team_id == team_id).order_by(models.TeamMember.id).all()
    talent_ids = [m.talent_id for m in members if m.talent_id is not None]
    resume_ids = [m.resume_profile_id for m in members if m.resume_profile_id is not None]
    tp = {p.id: p for p in db.query(models.TalentProfile).filter(
        models.TalentProfile.id.in_(talent_ids)).all()} if talent_ids else {}
    rp = {p.id: p for p in db.query(models.ResumeProfile).filter(
        models.ResumeProfile.id.in_(resume_ids)).all()} if resume_ids else {}
    profile = {m.id: (tp.get(m.talent_id) if m.talent_id is not None
                      else rp.get(m.resume_profile_id)) for m in members}
    job_names = {j.id: j.name for j in db.query(models.Job.id, models.Job.name).all()}
    return {"id": team.id, "name": team.name, "description": team.description,
            "organization_id": team.organization_id, "target_job_id": team.target_job_id,
            "members": [{
                "member_id": m.id, "display_name": m.display_name, "role_label": m.role_label,
                "talent_id": m.talent_id,
                "resume_profile_id": m.resume_profile_id,
                "talent_code": profile[m.id].code if profile.get(m.id) else None,
                "source_type": profile[m.id].source_type if profile.get(m.id) else None,
                "skill_count": (profile[m.id].skill_count if m.talent_id is not None
                                else len(profile[m.id].skills or [])) if profile.get(m.id) else 0,
                "skills": (profile[m.id].skills or [])[:30] if profile.get(m.id) else [],
                "target_cluster": (profile[m.id].target_cluster
                                   if m.talent_id is not None and profile.get(m.id) else None),
                "matched_job_name": (job_names.get(profile[m.id].matched_job_id)
                                     if m.talent_id is not None and profile.get(m.id) else None),
            } for m in members]}


@router.get("/teams/{team_id}/history")
def team_history(team_id: int, page: int = 1, size: int = 20,
                 actor: Actor = Depends(require_org_append), db: Session = Depends(get_db)):
    page, size = _page(page, size)
    team = db.query(models.Team).get(team_id)
    if not team:
        raise HTTPException(404, "团队不存在")
    require_org(team, actor)
    q = db.query(models.TeamEvent).filter(models.TeamEvent.team_id == team.id)
    total = q.count()
    rows = q.order_by(models.TeamEvent.created_at.desc(), models.TeamEvent.id.desc()).offset(
        (page - 1) * size).limit(size).all()
    return {"items": [{"id": row.id, "action": row.action,
                       "member_id": row.member_id, "details": row.details or {},
                       "before": row.before_snapshot, "after": row.after_snapshot,
                       "created_at": row.created_at.isoformat()} for row in rows],
            "total": total, "page": page, "size": size}


@router.post("/teams/{team_id}/members", status_code=201)
def add_team_member(team_id: int, payload: TeamMemberRequest,
                    actor: Actor = Depends(require_org_append), db: Session = Depends(get_db)):
    team = db.query(models.Team).get(team_id)
    if not team:
        raise HTTPException(404, "团队不存在")
    require_org(team, actor)
    profile = db.query(models.ResumeProfile).filter(
        models.ResumeProfile.id == payload.resume_profile_id,
        models.ResumeProfile.organization_id == actor.organization_id,
        models.ResumeProfile.authorized.is_(True)).first()
    if not profile or (profile.retention_expires_at and profile.retention_expires_at <= datetime.utcnow()):
        raise HTTPException(404, "候选人画像不存在或已过期")
    exists = db.query(models.TeamMember).filter(
        models.TeamMember.team_id == team.id,
        models.TeamMember.resume_profile_id == profile.id).first()
    if exists:
        raise HTTPException(409, "该候选人已在团队中")
    before = _team_state(db, team)
    member = models.TeamMember(
        team_id=team.id, talent_id=None, resume_profile_id=profile.id,
        display_name=payload.display_name, role_label=payload.role_label)
    db.add(member)
    db.flush()
    after = _team_state(db, team)
    _add_team_event(db, actor, team, "member_added", member_id=member.id,
                    details={"code": profile.code, "display_name": member.display_name},
                    before=before, after=after)
    add_audit(db, actor, "team.member.add", "team", team.id,
              summary={"count": 1, "member_id": member.id})
    db.commit()
    return {"member_id": member.id, "team_id": team.id,
            "before": before, "after": after}


@router.delete("/teams/{team_id}/members/{member_id}")
def remove_team_member(team_id: int, member_id: int,
                       actor: Actor = Depends(require_org_append), db: Session = Depends(get_db)):
    team = db.query(models.Team).get(team_id)
    if not team:
        raise HTTPException(404, "团队不存在")
    require_org(team, actor)
    member = db.query(models.TeamMember).filter(
        models.TeamMember.id == member_id,
        models.TeamMember.team_id == team.id).first()
    if not member:
        raise HTTPException(404, "团队成员不存在")
    before = _team_state(db, team)
    details = {"display_name": member.display_name,
               "resume_profile_id": member.resume_profile_id,
               "talent_id": member.talent_id}
    db.delete(member)
    db.flush()
    after = _team_state(db, team)
    _add_team_event(db, actor, team, "member_removed", member_id=member_id,
                    details=details, before=before, after=after)
    add_audit(db, actor, "team.member.remove", "team", team.id,
              summary={"count": 1, "member_id": member_id})
    db.commit()
    return {"team_id": team.id, "member_id": member_id,
            "before": before, "after": after}


@router.get("/teams/{team_id}/gap")
def team_gap(team_id: int, job_id: int, actor: Actor = Depends(optional_actor),
             db: Session = Depends(get_db)):
    """团队对目标岗位的能力缺口：已覆盖 / 谁能补 / 还缺谁。"""
    team = db.query(models.Team).get(team_id)
    if team and team.organization_id is not None:
        if actor.user is None or (actor.role != "admin" and actor.organization_id != team.organization_id):
            raise HTTPException(404, "团队或岗位不存在")
    data = talent_svc.team_gap(db, team_id, job_id)
    if not data:
        raise HTTPException(404, "团队或岗位不存在")
    return data


@router.get("/aliases")
def aliases(status: str | None = None, page: int = 1, size: int = 100,
            limit: int | None = None, db: Session = Depends(get_db)):
    """从简历学到（或拒绝）的技能表述台账。"""
    page, size = _page(page, limit if limit is not None else size)
    q = db.query(models.SkillAlias)
    if status:
        q = q.filter(models.SkillAlias.status == status)
    total = q.count()
    rows = q.order_by(models.SkillAlias.talent_count.desc(),
                      models.SkillAlias.alias).offset((page - 1) * size).limit(size).all()
    return {"total": total, "items": [{
        "alias": a.alias, "canonical": a.canonical, "status": a.status,
        "talent_count": a.talent_count, "confidence": a.confidence,
        "reason": a.reject_reason, "skill_id": a.skill_id,
    } for a in rows], "page": page, "size": size}


@router.post("/teams/{team_id}/members/upload")
async def upload_member_resume(team_id: int, file: UploadFile = File(...),
                               display_name: str = Form("成员"),
                               role_label: str = Form(""),
                               target_cluster: str = Form(""),
                               authorization_confirmed: bool = Form(...),
                               retention_days: int = Form(90),
                               actor: Actor = Depends(require_org_append),
                               db: Session = Depends(get_db)):
    """上传一份真实简历，解析后作为团队成员加入（意见⑧的"读取"入口）。

    隐私口径与 /api/match/resume/upload 一致：原文与姓名只在内存里参与本次解析，
    落库只有脱敏后的技能要素；display_name 由上传方自己给的化名，不取简历里的姓名。
    """
    team = db.query(models.Team).get(team_id)
    if not team:
        raise HTTPException(404, "团队不存在")
    require_org(team, actor)
    if not authorization_confirmed:
        raise HTTPException(422, {"code": "AUTHORIZATION_REQUIRED",
                                  "message": "必须确认已获得成员授权"})
    if retention_days < 1 or retention_days > 365:
        raise HTTPException(422, "保留期限必须为 1-365 天")
    content = await file.read()
    if len(content) > 8 * 1024 * 1024:
        raise HTTPException(413, {"code": "FILE_TOO_LARGE", "message": "文件过大(>8MB)"})
    try:
        text = resume_svc.extract_text(file.filename, content)
    except resume_svc.ResumeFileError as exc:
        raise HTTPException(exc.status_code, {"code": exc.code, "message": exc.message})
    text = resume_svc.mask_contacts(text)
    parsed = resume_svc.parse_resume(text)

    skills = parsed.get("skills", []) or []
    code = f"ORG{actor.organization_id}-{int(__import__('time').time() * 1000)}"
    before = _team_state(db, team)
    tp = models.ResumeProfile(
        owner_user_id=None, organization_id=actor.organization_id,
        code=code, source_type="upload", authorized=True,
        years_experience=parsed.get("years_experience", 0) or 0,
        education=(parsed.get("education") or "")[:64],
        skills=skills, skill_levels=parsed.get("skill_levels", {}) or {},
        retention_expires_at=datetime.utcnow() + timedelta(days=retention_days),
    )
    db.add(tp)
    db.flush()
    member = models.TeamMember(team_id=team_id, talent_id=None, resume_profile_id=tp.id,
                               display_name=display_name or code,
                               role_label=role_label or None)
    db.add(member)
    db.flush()
    after = _team_state(db, team)
    _add_team_event(db, actor, team, "member_uploaded", member_id=member.id,
                    details={"code": tp.code, "display_name": member.display_name},
                    before=before, after=after)
    add_audit(db, actor, "team.member.upload", "team", team.id, summary={"count": 1})
    db.commit()
    return {"talent_id": tp.id, "code": tp.code, "skill_count": len(skills),
            "skills": skills,
            "privacy_notice": "简历原文与姓名等个人信息仅用于本次解析，不在服务端留存"}
