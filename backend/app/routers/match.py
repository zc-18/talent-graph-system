"""简历解析、人岗匹配与差距分析路由。"""
from __future__ import annotations
from datetime import datetime
from time import perf_counter
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session
from .. import models
from ..db import get_db
from ..schemas import MatchRequest, ResumeTextRequest
from ..services import resume as resume_svc, extraction, matching, role_contract
from ..services.job_resolution import resolve_job_query
from ..services.taxonomy import normalize_skill, skill_category
from ..auth import Actor, add_audit, add_usage, optional_actor

router = APIRouter(prefix="/api/match", tags=["match"])


MAX_RESUME_BYTES = 8 * 1024 * 1024


def _file_error(exc: resume_svc.ResumeFileError):
    raise HTTPException(exc.status_code, {"code": exc.code, "message": exc.message})


def _private_profile(db: Session, actor: Actor, parsed: dict,
                      *, authorized: bool = True) -> models.ResumeProfile | None:
    # Organization-owned resumes must enter through the HR batch endpoint, which
    # records authorization and retention. Generic matching only persists self-owned data.
    if actor.user is None or actor.role != "user":
        return None
    row = models.ResumeProfile(
        owner_user_id=actor.user_id,
        organization_id=None,
        code=f"P{actor.user_id}-{int(datetime.utcnow().timestamp() * 1000)}",
        source_type="upload", skills=parsed.get("skills", []) or [],
        skill_levels=parsed.get("skill_levels", {}) or {},
        years_experience=parsed.get("years_experience", 0) or 0,
        education=(parsed.get("education") or "")[:64], authorized=authorized)
    db.add(row)
    db.flush()
    return row


@router.post("/resume/upload")
async def upload_resume(file: UploadFile = File(...), actor: Actor = Depends(optional_actor),
                        db: Session = Depends(get_db)):
    """上传简历(PDF/Word/txt)→解析→抽取技能要素。"""
    content = await file.read()
    if len(content) > MAX_RESUME_BYTES:
        raise HTTPException(413, {"code": "FILE_TOO_LARGE", "message": "文件过大(>8MB)"})
    try:
        text = resume_svc.extract_text(file.filename, content)
    except resume_svc.ResumeFileError as exc:
        _file_error(exc)
    parsed = resume_svc.parse_resume(text)
    profile = _private_profile(db, actor, parsed)
    if profile:
        add_audit(db, actor, "resume_profile.create", "resume_profile", profile.id)
        db.commit()
    return {"resume_id": profile.id if profile else None, "filename": file.filename, "extracted": parsed,
            "skill_count": len(parsed.get("skills", [])),
            "privacy_notice": "原始简历与姓名等个人信息仅用于本次解析，不在服务端留存"}


@router.post("/resume/text")
def parse_resume_text(payload: ResumeTextRequest, actor: Actor = Depends(optional_actor),
                       db: Session = Depends(get_db)):
    """直接提交简历文本解析。"""
    text = payload.text
    parsed = resume_svc.parse_resume(text)
    profile = _private_profile(db, actor, parsed)
    if profile:
        add_audit(db, actor, "resume_profile.create", "resume_profile", profile.id)
        db.commit()
    return {"resume_id": profile.id if profile else None, "extracted": parsed,
            "skill_count": len(parsed.get("skills", [])),
            "privacy_notice": "原始简历与姓名等个人信息仅用于本次解析，不在服务端留存"}


def _job_caps(db: Session, job_id: int) -> list[dict]:
    js = db.query(models.JobSkill).filter(models.JobSkill.job_id == job_id,
                                          models.JobSkill.status == "active").all()
    # 技能名/技术栈一次批量取（历史实现逐条 Skill.get，是 N+1）
    sk_rows = {r.id: r for r in db.query(models.Skill.id, models.Skill.name, models.Skill.category)
               .filter(models.Skill.id.in_({j.skill_id for j in js})).all()} if js else {}
    caps = []
    for j in js:
        sk = sk_rows.get(j.skill_id)
        if sk:
            caps.append({"name": sk.name, "importance": j.importance, "weight": j.weight,
                         "level_required": j.level_required, "category": sk.category,
                         "confidence": j.confidence, "status": "active"})
    return caps


def _skill_relations(db: Session, names: list[str]) -> dict:
    """构造缺失技能的先修关系图（用于学习路径）。"""
    # 一次 IN 查询取回全部命中技能（历史实现按名字逐个 query，是 N+1）
    name_to_id = {}
    if names:
        rows = db.query(models.Skill.normalized_name, models.Skill.id).filter(
            models.Skill.normalized_name.in_(set(names))).order_by(models.Skill.id).all()
        for nm, sid in rows:
            name_to_id.setdefault(nm, sid)   # 同名多行时保留 id 最小的，与原 .first() 一致
    id_to_name = {v: k for k, v in name_to_id.items()}
    rels = {}
    if name_to_id:
        relations = db.query(models.SkillRelation).filter(
            models.SkillRelation.relation_type == "prerequisite",
            models.SkillRelation.to_skill_id.in_(list(name_to_id.values()))).all()
        for r in relations:
            tgt = id_to_name.get(r.to_skill_id)
            src = id_to_name.get(r.from_skill_id)
            if tgt and src:
                rels.setdefault(tgt, []).append(src)
    return rels


def _transient_contract(text: str) -> tuple[dict, dict]:
    parsed = extraction.parse_jd(text)
    title = parsed.get("job_title") or parsed.get("title") or text[:80]
    resolution = resolve_job_query(title)
    capabilities = []
    for importance, key in (("required", "required_skills"), ("bonus", "bonus_skills")):
        for item in parsed.get(key, []) or []:
            item = {"name": item} if isinstance(item, str) else item
            name = normalize_skill(item.get("name") or "")
            if not name:
                continue
            capabilities.append({"name": name, "importance": importance,
                                 "weight": float(item.get("weight") or
                                                 (0.75 if importance == "required" else 0.35)),
                                 "level_required": item.get("level") or "familiar",
                                 "confidence": float(item.get("confidence") or 0.65),
                                 "source_count": 1, "employer_count": 1,
                                 "category": item.get("category") or skill_category(name),
                                 "status": "active"})
    contract = role_contract.build_role_contract(
        capabilities, job_id=None, job_name=resolution.canonical_title,
        seniority=resolution.seniority, recruitment_type=resolution.recruitment_type,
        track=resolution.track, industry=resolution.industry, version=0, min_employers=1)
    return contract, {"id": None, "name": resolution.canonical_title,
                      "category": parsed.get("category") or "临时岗位",
                      "transient": True}


@router.post("/analyze")
def analyze(payload: MatchRequest, actor: Actor = Depends(optional_actor),
            db: Session = Depends(get_db)):
    """人岗匹配诊断与差距分析。输入技能或简历文本，对比目标岗位图谱。"""
    job = db.query(models.Job).get(payload.job_id) if payload.job_id is not None else None
    if payload.job_id is not None and not job:
        raise HTTPException(404, "岗位不存在")
    if payload.save and actor.user is not None and not (
            actor.role == "user" or
            (actor.role == "hr" and actor.organization_id is not None)):
        raise HTTPException(403, "当前账号没有可归属的匹配历史空间")
    if payload.save and actor.role == "hr":
        raise HTTPException(422, {
            "code": "HR_BATCH_REQUIRED",
            "message": "企业候选简历必须通过招聘批次提交授权声明和保留期限",
        })

    skills, levels = payload.skills, payload.skill_levels
    if payload.resume_text and not skills:
        parsed = resume_svc.parse_resume(payload.resume_text)
        skills, levels = parsed["skills"], parsed["skill_levels"]

    started = perf_counter()
    if job:
        contract = role_contract.build_contract_from_job(
            db, job, seniority=payload.seniority or job.level or "unspecified",
            recruitment_type=payload.recruitment_type or job.recruitment_type or "mixed",
            track=payload.track or job.track or "software",
            industry=payload.industry or job.industry or "general")
        job_response = {"id": job.id, "name": job.name, "category": job.category,
                        "transient": False}
    else:
        contract, job_response = _transient_contract(payload.target_job_text or "")
    caps = role_contract.matching_capabilities(contract)
    result = matching.match(caps, skills, levels, use_semantic=True)

    # 学习路径
    rels = _skill_relations(db, [m["name"] for m in result["missing_required"]])
    learning_path = matching.build_learning_path(result["missing_required"], rels)

    # 改进建议
    suggestions = {}
    if payload.generate_suggestions:
        suggestions = matching.generate_suggestions(
            job_response["name"], result["missing_required"], result["missing_bonus"],
            result["summary"]["required_matched"], result["overall_score"])

    rec = None
    if payload.save and actor.user is not None:
        version_row = (db.query(models.JobVersion).filter(
            models.JobVersion.job_id == job.id,
            models.JobVersion.version == (job.version or 1)).first()) if job else None
        profile = _private_profile(db, actor, {"skills": skills, "skill_levels": levels,
                                               "years_experience": 0, "education": ""})
        rec = models.MatchRun(
            owner_user_id=actor.user_id if actor.role == "user" else None,
            organization_id=actor.organization_id if actor.role == "hr" else None,
            resume_profile_id=profile.id if profile else None,
            job_id=job.id if job else None, job_version_id=version_row.id if version_row else None,
            job_version=(job.version or 1) if job else None, status="completed",
            contract_snapshot=contract,
            result_snapshot={**result, "suggestions": suggestions}, learning_path=learning_path)
        db.add(rec)
        db.flush()
        add_audit(db, actor, "match.save", "match_run", rec.id,
                  summary={"status": "completed", "version": (job.version or 1) if job else 0})
    add_usage(db, actor, "match", int((perf_counter() - started) * 1000), True)
    db.commit()

    return {"job": job_response,
            "result": result, "learning_path": learning_path, "suggestions": suggestions,
            "contract": contract, "match_id": rec.id if rec else None}
