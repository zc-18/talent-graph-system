"""新岗位发现与定义路由。"""
from __future__ import annotations
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from ..db import get_db
from .. import models
from ..schemas import DiscoverRequest, DefineRequest
from ..services import discovery, graph_service

router = APIRouter(prefix="/api/discovery", tags=["discovery"])


def _save_if_absent(db: Session, definition: dict) -> tuple[dict | None, dict | None]:
    """岗位已在图谱中则**拒绝落库**，返回冲突说明而不是覆盖。

    `graph_service.upsert_job` 会先清空该岗位的 JobSkill/Evidence 再按传入能力项重建
    ——这对「全量语料重跑管线」是对的，但发现接口传进去的是 LLM 根据几条检索证据
    现生成的十来项能力。对一个已经由 2306 条真实 JD 交叉验证建好的岗位调用它，
    等于用弱证据抹掉强证据：实测线上点了两次「发现」，提示词工程师 301 条能力关系
    连同证据被物理删除、只剩 7 条，且 is_new 被翻成 True，新兴岗位数从 6 变 7，
    交付文档里的口径当场对不上。

    语义上也说得通：已经在图谱里的岗位本就不是「新发现」。此时返回定义预览 +
    冲突提示，既不破坏数据，也让「系统能识别岗位已存在、避免重复建岗」这件事
    在演示中可见。要更新既有岗位能力请走演化接口。
    """
    slug = graph_service.slugify(definition["job_title"])
    existing = db.query(models.Job).filter(models.Job.slug == slug).first()
    if existing:
        n_caps = db.query(models.JobSkill).filter(
            models.JobSkill.job_id == existing.id,
            models.JobSkill.status == "active").count()
        return None, {
            "reason": "already_exists",
            "job_id": existing.id, "job_name": existing.name,
            "is_new": bool(existing.is_new), "version": existing.version,
            "active_capabilities": n_caps,
            "message": (f"「{existing.name}」已在图谱中（v{existing.version}，"
                        f"{n_caps} 项已验证能力），未覆盖。"
                        "如需更新其能力项，请使用「岗位能力演化」。"),
        }
    job = graph_service.upsert_job(
        db, job_title=definition["job_title"], category=definition["category"],
        level=definition["level"], responsibilities=definition["core_responsibilities"],
        scenarios=definition["typical_scenarios"], capabilities=definition["capabilities"],
        is_new=True, summary=definition["summary"],
        source_summary=definition["source_summary"],
        emergence_score=definition["emergence_score"], with_embedding=False)
    return graph_service.job_to_dict(db, job), None


@router.get("/seeds")
def seeds():
    """内置新兴岗位候选种子。"""
    return {"seeds": discovery.EMERGING_SEEDS}


@router.post("/discover")
def discover(payload: DiscoverRequest, db: Session = Depends(get_db)):
    """检索某新兴岗位证据并生成定义；save=True 且岗位尚不存在时落库。"""
    cand = discovery.discover_candidates(payload.keyword)
    definition = discovery.define_new_job(payload.keyword, cand["evidence"])
    definition["emergence_score"] = max(definition.get("emergence_score", 0), cand["emergence_score"])
    saved, conflict = _save_if_absent(db, definition) if payload.save else (None, None)
    return {"candidate": {"keyword": cand["keyword"], "emergence_score": cand["emergence_score"],
                          "evidence_count": cand["evidence_count"],
                          "independent_sources": cand.get("independent_sources", 0),
                          "evidence": cand["evidence"][:6]},
            "definition": definition, "saved": saved, "conflict": conflict}


@router.post("/define")
def define(payload: DefineRequest, db: Session = Depends(get_db)):
    """基于给定证据(可人工补充)生成/保存新岗位定义。"""
    evidence = payload.evidence
    if not evidence:
        evidence = discovery.discover_candidates(payload.keyword)["evidence"]
    definition = discovery.define_new_job(payload.keyword, evidence)
    saved, conflict = _save_if_absent(db, definition) if payload.save else (None, None)
    return {"definition": definition, "saved": saved, "conflict": conflict}
