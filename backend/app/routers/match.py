"""简历解析、人岗匹配与差距分析路由。"""
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session
from .. import models
from ..db import get_db
from ..schemas import MatchRequest
from ..services import resume as resume_svc, matching, graph_service

router = APIRouter(prefix="/api/match", tags=["match"])


@router.post("/resume/upload")
async def upload_resume(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """上传简历(PDF/Word/txt)→解析→抽取技能要素。"""
    content = await file.read()
    if len(content) > 8 * 1024 * 1024:
        raise HTTPException(400, "文件过大(>8MB)")
    text = resume_svc.extract_text(file.filename, content)
    if not text.strip():
        raise HTTPException(422, "无法从文件中提取文本，请检查文件格式")
    parsed = resume_svc.parse_resume(text)
    row = models.Resume(filename=file.filename, candidate_name=parsed.get("candidate_name", ""),
                        raw_text=text[:20000], extracted=parsed, skills=parsed.get("skills", []),
                        years_experience=parsed.get("years_experience", 0))
    db.add(row)
    db.commit()
    return {"resume_id": row.id, "filename": file.filename, "extracted": parsed,
            "skill_count": len(parsed.get("skills", []))}


@router.post("/resume/text")
def parse_resume_text(payload: dict, db: Session = Depends(get_db)):
    """直接提交简历文本解析。"""
    text = payload.get("text", "")
    if not text.strip():
        raise HTTPException(400, "文本为空")
    parsed = resume_svc.parse_resume(text)
    row = models.Resume(filename="text-input", candidate_name=parsed.get("candidate_name", ""),
                        raw_text=text[:20000], extracted=parsed, skills=parsed.get("skills", []),
                        years_experience=parsed.get("years_experience", 0))
    db.add(row)
    db.commit()
    return {"resume_id": row.id, "extracted": parsed, "skill_count": len(parsed.get("skills", []))}


def _job_caps(db: Session, job_id: int) -> list[dict]:
    js = db.query(models.JobSkill).filter(models.JobSkill.job_id == job_id,
                                          models.JobSkill.status == "active").all()
    caps = []
    for j in js:
        sk = db.query(models.Skill).get(j.skill_id)
        if sk:
            caps.append({"name": sk.name, "importance": j.importance, "weight": j.weight,
                         "level_required": j.level_required, "category": sk.category,
                         "confidence": j.confidence, "status": "active"})
    return caps


def _skill_relations(db: Session, names: list[str]) -> dict:
    """构造缺失技能的先修关系图（用于学习路径）。"""
    name_to_id = {}
    for nm in names:
        sk = db.query(models.Skill).filter(models.Skill.normalized_name == nm).first()
        if sk:
            name_to_id[nm] = sk.id
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


@router.post("/analyze")
def analyze(payload: MatchRequest, db: Session = Depends(get_db)):
    """人岗匹配诊断与差距分析。输入技能或简历文本，对比目标岗位图谱。"""
    job = db.query(models.Job).get(payload.job_id)
    if not job:
        raise HTTPException(404, "岗位不存在")

    skills, levels = payload.skills, payload.skill_levels
    if payload.resume_text and not skills:
        parsed = resume_svc.parse_resume(payload.resume_text)
        skills, levels = parsed["skills"], parsed["skill_levels"]

    caps = _job_caps(db, payload.job_id)
    result = matching.match(caps, skills, levels, use_semantic=True)

    # 学习路径
    rels = _skill_relations(db, [m["name"] for m in result["missing_required"]])
    learning_path = matching.build_learning_path(result["missing_required"], rels)

    # 改进建议
    suggestions = {}
    if payload.generate_suggestions:
        suggestions = matching.generate_suggestions(
            job.name, result["missing_required"], result["missing_bonus"],
            result["summary"]["required_matched"], result["overall_score"])

    rec = models.MatchResult(
        resume_id=None, job_id=job.id, overall_score=result["overall_score"],
        dimension_scores=result["dimension_scores"], matched_skills=result["matched_skills"],
        missing_required=result["missing_required"], missing_bonus=result["missing_bonus"],
        suggestions=suggestions, learning_path=learning_path)
    db.add(rec)
    db.commit()

    return {"job": {"id": job.id, "name": job.name, "category": job.category},
            "result": result, "learning_path": learning_path, "suggestions": suggestions,
            "match_id": rec.id}
