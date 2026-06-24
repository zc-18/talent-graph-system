"""新岗位发现与定义路由。"""
from __future__ import annotations
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from ..db import get_db
from ..schemas import DiscoverRequest, DefineRequest
from ..services import discovery, graph_service

router = APIRouter(prefix="/api/discovery", tags=["discovery"])


@router.get("/seeds")
def seeds():
    """内置新兴岗位候选种子。"""
    return {"seeds": discovery.EMERGING_SEEDS}


@router.post("/discover")
def discover(payload: DiscoverRequest, db: Session = Depends(get_db)):
    """检索某新兴岗位证据并生成定义；save=True 时落库。"""
    cand = discovery.discover_candidates(payload.keyword)
    definition = discovery.define_new_job(payload.keyword, cand["evidence"])
    definition["emergence_score"] = max(definition.get("emergence_score", 0), cand["emergence_score"])
    saved = None
    if payload.save:
        job = graph_service.upsert_job(
            db, job_title=definition["job_title"], category=definition["category"],
            level=definition["level"], responsibilities=definition["core_responsibilities"],
            scenarios=definition["typical_scenarios"], capabilities=definition["capabilities"],
            is_new=True, summary=definition["summary"],
            source_summary=definition["source_summary"],
            emergence_score=definition["emergence_score"], with_embedding=False)
        saved = graph_service.job_to_dict(db, job)
    return {"candidate": {"keyword": cand["keyword"], "emergence_score": cand["emergence_score"],
                          "evidence_count": cand["evidence_count"],
                          "independent_sources": cand.get("independent_sources", 0),
                          "evidence": cand["evidence"][:6]},
            "definition": definition, "saved": saved}


@router.post("/define")
def define(payload: DefineRequest, db: Session = Depends(get_db)):
    """基于给定证据(可人工补充)生成/保存新岗位定义。"""
    evidence = payload.evidence
    if not evidence:
        evidence = discovery.discover_candidates(payload.keyword)["evidence"]
    definition = discovery.define_new_job(payload.keyword, evidence)
    saved = None
    if payload.save:
        job = graph_service.upsert_job(
            db, job_title=definition["job_title"], category=definition["category"],
            level=definition["level"], responsibilities=definition["core_responsibilities"],
            scenarios=definition["typical_scenarios"], capabilities=definition["capabilities"],
            is_new=True, summary=definition["summary"],
            source_summary=definition["source_summary"],
            emergence_score=definition["emergence_score"], with_embedding=False)
        saved = graph_service.job_to_dict(db, job)
    return {"definition": definition, "saved": saved}
