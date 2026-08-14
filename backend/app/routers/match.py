"""简历解析、人岗匹配与差距分析路由。"""
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session
from .. import models
from ..db import get_db
from ..guards import is_read_only
from ..schemas import MatchRequest
from ..services import resume as resume_svc, matching, graph_service

router = APIRouter(prefix="/api/match", tags=["match"])


def _persist(db: Session, row):
    """只读演示站不落这类分析留痕。

    这三个接口不碰知识图谱，所以不该进 `require_write` 那道硬闸——简历解析和人岗匹配
    正是要给评委演示的功能。但它们每次请求都会写一行 `Resume`/`MatchResult`，而公网
    演示站没有任何频率限制，等于把生产库的行数交给访客决定。查过全仓：没有任何展示
    接口读这两张表（人才盘点走的是 `ResumeBatch`），留痕纯属自用，只读模式下直接不写。

    返回 row.id 或 None——前端不消费 resume_id/match_id，返回 None 不影响任何页面。
    """
    if is_read_only():
        return None
    db.add(row)
    db.commit()
    return row.id


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
    # 合规·隐私最小化：原始简历全文与姓名仅内存解析、即时返回本人，不落库；
    # 服务端仅留存脱敏后的技能要素用于分析。
    row = models.Resume(filename="(已脱敏)", candidate_name="", raw_text=None,
                        extracted=resume_svc.redact_for_storage(parsed),
                        skills=parsed.get("skills", []),
                        years_experience=parsed.get("years_experience", 0))
    return {"resume_id": _persist(db, row), "filename": file.filename, "extracted": parsed,
            "skill_count": len(parsed.get("skills", [])),
            "privacy_notice": "原始简历与姓名等个人信息仅用于本次解析，不在服务端留存"}


@router.post("/resume/text")
def parse_resume_text(payload: dict, db: Session = Depends(get_db)):
    """直接提交简历文本解析。"""
    text = payload.get("text", "")
    if not text.strip():
        raise HTTPException(400, "文本为空")
    parsed = resume_svc.parse_resume(text)
    # 合规·隐私最小化：不持久化原始简历与姓名，仅留存脱敏技能要素
    row = models.Resume(filename="text-input", candidate_name="", raw_text=None,
                        extracted=resume_svc.redact_for_storage(parsed),
                        skills=parsed.get("skills", []),
                        years_experience=parsed.get("years_experience", 0))
    return {"resume_id": _persist(db, row), "extracted": parsed,
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

    return {"job": {"id": job.id, "name": job.name, "category": job.category},
            "result": result, "learning_path": learning_path, "suggestions": suggestions,
            "match_id": _persist(db, rec)}
