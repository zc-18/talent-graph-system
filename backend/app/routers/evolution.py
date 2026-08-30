"""既有岗位能力动态更新（演化）路由。"""
from __future__ import annotations
from collections import defaultdict
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from .. import models
from ..db import get_db
from ..schemas import EvolveRequest
from ..services import extraction, hallucination, evolution, graph_service, leveling
from ..guards import is_read_only, READ_ONLY_MESSAGE
from .. import clients
from ..auth import Actor, current_actor

router = APIRouter(prefix="/api/evolution", tags=["evolution"])


def _iso(value):
    return value.isoformat() if value else None


@router.get("/{job_id}/timeline")
def evolution_timeline(job_id: int, db: Session = Depends(get_db)):
    """Return factual corpus slices and append-only version/change history."""
    job = db.get(models.Job, job_id)
    if not job or job.status != "published":
        raise HTTPException(404, "岗位不存在")

    evidence_rows = (db.query(models.Evidence, models.RawJD)
                     .join(models.RawJD, models.RawJD.id == models.Evidence.raw_jd_id)
                     .join(models.JobSkill,
                           models.JobSkill.id == models.Evidence.job_skill_id)
                     .filter(models.JobSkill.job_id == job_id,
                             models.Evidence.source_type == "jd",
                             models.RawJD.is_duplicate == False,  # noqa: E712
                             models.RawJD.duplicate_of.is_(None),
                             models.RawJD.raw_text.isnot(None)).all())
    raw_by_id = {}
    urls_by_raw: dict[int, set[str]] = defaultdict(set)
    for evidence, raw in evidence_rows:
        raw_by_id.setdefault(raw.id, raw)
        url = (evidence.source_url or raw.source_url or "").strip()
        if url.startswith(("http://", "https://")):
            urls_by_raw[raw.id].add(url)

    employer_ids = {raw.employer_id for raw in raw_by_id.values() if raw.employer_id}
    employers = {row.id: row for row in db.query(models.Employer).filter(
        models.Employer.id.in_(employer_ids)).all()} if employer_ids else {}
    parent_ids = {row.parent_id for row in employers.values() if row.parent_id}
    if parent_ids:
        employers.update({row.id: row for row in db.query(models.Employer).filter(
            models.Employer.id.in_(parent_ids)).all()})

    slices: dict[int, dict] = {}
    observed_values = []
    evidenced_values = []
    for raw in raw_by_id.values():
        observed = raw.publish_date or raw.collected_at
        if not observed:
            continue
        observed_values.append(observed)
        if urls_by_raw.get(raw.id):
            evidenced_values.append(observed)
        bucket = slices.setdefault(observed.year, {
            "year": observed.year, "jd_ids": set(), "employer_ids": set(),
            "platforms": set(), "urls": set(), "urled_jd_ids": set(),
            "start": observed, "end": observed,
        })
        bucket["jd_ids"].add(raw.id)
        bucket["platforms"].add(raw.platform or raw.source or "未知来源")
        raw_urls = urls_by_raw.get(raw.id, set())
        bucket["urls"].update(raw_urls)
        if raw_urls:
            bucket["urled_jd_ids"].add(raw.id)
        bucket["start"] = min(bucket["start"], observed)
        bucket["end"] = max(bucket["end"], observed)
        employer = employers.get(raw.employer_id)
        if employer and employer.status == "active":
            unit_id = employer.parent_id or employer.id
            unit = employers.get(unit_id)
            if unit and unit.status == "active":
                bucket["employer_ids"].add(unit_id)

    corpus_slices = [{
        "year": year,
        "label": f"{year} 语料",
        "start_at": _iso(bucket["start"]),
        "end_at": _iso(bucket["end"]),
        "jd_count": len(bucket["jd_ids"]),
        "employer_count": len(bucket["employer_ids"]),
        "platforms": sorted(bucket["platforms"]),
        # 两个字段口径不同，别合并：valid_url_count 数的是去重后的 URL 条数；
        # url_coverage 是「有可核验 URL 的 JD 占比」，分子分母同为 JD 口径。
        # 用 URL 数除 JD 数会因一条 JD 挂多个 source_url 而超过 1（前端曾显示 URL 120%），
        # 多条 JD 共用同一 URL 时又会低报。
        "valid_url_count": len(bucket["urls"]),
        "url_coverage": round(len(bucket["urled_jd_ids"]) / max(1, len(bucket["jd_ids"])), 4),
    } for year, bucket in sorted(slices.items())]

    changes = db.query(models.CapabilityChange).filter(
        models.CapabilityChange.job_id == job_id).order_by(
        models.CapabilityChange.version, models.CapabilityChange.id).all()
    changes_by_version: dict[int, list] = defaultdict(list)
    for change in changes:
        changes_by_version[change.version].append(change)
    versions = db.query(models.JobVersion).filter(
        models.JobVersion.job_id == job_id).order_by(models.JobVersion.version).all()
    version_nodes = [{
        "id": version.id,
        "version": version.version,
        "status": version.status,
        "effective_at": _iso(version.effective_at or version.created_at),
        "summary": version.summary,
        "evidence_window": version.evidence_window or {},
        "change_count": len(changes_by_version.get(version.version, [])),
    } for version in versions]
    if not version_nodes:
        version_nodes = [{
            "id": None, "version": job.version or 1, "status": "published",
            "effective_at": _iso(job.created_at), "summary": job.summary,
            "evidence_window": {}, "change_count": len(changes),
        }]
    proposal_runs = db.query(models.EvolutionRun).filter(
        models.EvolutionRun.job_id == job_id).order_by(models.EvolutionRun.created_at).all()

    first_published = min(
        (value for value in [*(v.effective_at or v.created_at for v in versions),
                             job.created_at] if value), default=None)
    has_historical_slice = any(item["year"] < 2026 for item in corpus_slices)
    if not corpus_slices:
        coverage_note = "未发现可核验岗位语料；不生成历史变化。"
    elif job.is_new and not has_historical_slice:
        coverage_note = "仅展示首次观察、首次考证和首次发布；未生成虚假历史变化。"
    else:
        coverage_note = "时间切片仅由已关联、非重复且可追溯的真实 JD 生成。"
    return {
        "job_id": job.id,
        "job_name": job.name,
        "lifecycle_mode": ("first_observation" if job.is_new and not has_historical_slice
                           else "historical_evolution"),
        "first_observed_at": _iso(min(observed_values) if observed_values else None),
        "first_evidenced_at": _iso(min(evidenced_values) if evidenced_values else None),
        "first_published_at": _iso(first_published),
        "corpus_slices": corpus_slices,
        "version_nodes": version_nodes,
        "capability_changes": [{
            "version": row.version, "change_type": row.change_type,
            "skill_name": row.skill_name, "importance": row.importance,
            "old_value": row.old_value, "new_value": row.new_value,
            "reason": row.reason, "data_source": row.data_source or {},
            "confidence": row.confidence, "created_at": _iso(row.created_at),
        } for row in changes],
        "proposal_runs": [{
            "id": row.id, "from_version": row.from_version,
            "proposed_version": row.proposed_version, "status": row.status,
            "created_at": _iso(row.created_at),
        } for row in proposal_runs],
        "coverage_note": coverage_note,
    }


@router.get("/{job_id}/changes")
def change_history(job_id: int, page: int = 1, size: int = 20,
                   db: Session = Depends(get_db)):
    """岗位能力变更历史（新增/删除/修改）。"""
    page, size = max(1, page), min(100, max(1, size))
    query = db.query(models.CapabilityChange).filter(
        models.CapabilityChange.job_id == job_id)
    total = query.count()
    changes = query.order_by(models.CapabilityChange.created_at.desc(),
                             models.CapabilityChange.id.desc()).offset(
        (page - 1) * size).limit(size).all()
    return {"job_id": job_id, "items": [{
        "version": c.version, "change_type": c.change_type, "skill_name": c.skill_name,
        "importance": c.importance, "old_value": c.old_value, "new_value": c.new_value,
        "reason": c.reason, "data_source": c.data_source, "confidence": c.confidence,
        "created_at": c.created_at.isoformat() if c.created_at else None} for c in changes],
            "total": total, "page": page, "size": size}


@router.get("/{job_id}/levels")
def level_profiles(job_id: int, db: Session = Depends(get_db)):
    """岗位分级能力画像（初/中/高级）。"""
    job = db.query(models.Job).get(job_id)
    if not job:
        raise HTTPException(404, "岗位不存在")
    rows = db.query(models.JobLevelSkill, models.Skill).join(
        models.Skill, models.JobLevelSkill.skill_id == models.Skill.id).filter(
        models.JobLevelSkill.job_id == job_id).all()
    levels: dict = {}
    for jls, sk in rows:
        lv = levels.setdefault(jls.level, {"jd_count": jls.jd_count, "skills": []})
        lv["skills"].append({
            "skill_id": sk.id, "name": sk.name, "importance": jls.importance,
            "weight": jls.weight, "level_required": jls.level_required,
            "confidence": jls.confidence, "factors": jls.factors,
            "source_count": jls.source_count})
    for lv in levels.values():
        lv["skills"].sort(key=lambda s: (s["importance"] == "required", s["weight"]),
                          reverse=True)
    order = {"junior": 0, "middle": 1, "senior": 2}
    available = sorted(levels.keys(), key=lambda x: order.get(x, 9))
    return {"job_id": job_id, "available": available, "levels": levels}


@router.get("/{job_id}/level-diff")
def level_diff(job_id: int, frm: str, to: str, db: Session = Depends(get_db)):
    """两级能力画像对比（晋升视角的新增/强化/默认前提）。"""
    if frm not in leveling.LEVELS or to not in leveling.LEVELS:
        raise HTTPException(400, "级别必须是 junior/middle/senior")
    job = db.query(models.Job).get(job_id)
    if not job:
        raise HTTPException(404, "岗位不存在")
    return leveling.level_diff(db, job_id, frm, to)


@router.post("/update")
def update_job(payload: EvolveRequest, actor: Actor = Depends(current_actor),
               db: Session = Depends(get_db)):
    """用新 JD 驱动既有岗位能力演化：识别变化→标注增删改→落库。"""
    job = db.query(models.Job).get(payload.job_id)
    if not job:
        raise HTTPException(404, "岗位不存在")
    if not is_read_only() and actor.role != "admin":
        raise HTTPException(403, "只有管理员可发布公共岗位演化")

    # 旧能力项快照（技能名一次批量取，历史实现逐条 Skill.get 是 N+1）
    old_js = db.query(models.JobSkill).filter(models.JobSkill.job_id == job.id,
                                              models.JobSkill.status == "active").all()
    sk_name = dict(db.query(models.Skill.id, models.Skill.name).filter(
        models.Skill.id.in_({j.skill_id for j in old_js})).all()) if old_js else {}
    old_caps = []
    for j in old_js:
        nm = sk_name.get(j.skill_id)
        if nm is not None:
            old_caps.append({"name": nm, "importance": j.importance, "weight": j.weight,
                             "level_required": j.level_required, "confidence": j.confidence,
                             "source_count": j.source_count})

    # 解析新 JD → 聚合
    agg_input = []
    parsed_new_jds = []
    for i, jd_text in enumerate(payload.new_jds):
        parsed = extraction.parse_jd(jd_text)
        parsed_new_jds.append(parsed)
        agg_input.append({"required_skills": parsed.get("required_skills", []),
                          "bonus_skills": parsed.get("bonus_skills", []),
                          # 细粒度技能点也参与演化聚合：漏了它，演化只会更新粗粒度大概念，
                          # 而赛题要求颗粒度到技能点，岗位跑过演化反而比新建时更粗。
                          "fine_skills": parsed.get("fine_skills", []),
                          "lag_days": 0, "is_duplicate": False, "raw_jd_id": None,
                          "source": "evolution-input"})
    if not agg_input:
        raise HTTPException(400, "请提供至少一条新 JD 文本")

    # 与历史能力合并交叉验证（旧能力作为先验来源之一）
    for c in old_caps:
        agg_input.append({"required_skills": [{"name": c["name"], "importance": "required",
                                               "level": c["level_required"], "category": "",
                                               "skill_type": "hard", "raw": c["name"]}]
                          if c["importance"] == "required" else [],
                          "bonus_skills": [{"name": c["name"], "importance": "bonus",
                                            "level": "familiar", "category": "", "skill_type": "hard",
                                            "raw": c["name"]}] if c["importance"] == "bonus" else [],
                          "lag_days": 120, "is_duplicate": False, "raw_jd_id": None,
                          "source": "history"})

    web_skills = set()
    if payload.use_web:
        res = clients.multi_source_search(f"{job.name} 最新 技能要求 2026", max_results=4)
        blob = " ".join((r.get("content") or "") for r in res).lower()
        from ..services.taxonomy import SYNONYMS
        for kw, nm in SYNONYMS.items():
            if kw in blob:
                web_skills.add(nm)

    agg = hallucination.aggregate_capabilities(agg_input, web_evidence_skills=web_skills)
    changes = evolution.compute_changes(old_caps, agg["capabilities"])
    current = evolution.current_capabilities(db, job)
    proposal_by_name = {item["name"]: item for item in current}
    aggregated_by_name = {
        item["name"]: item for item in agg["capabilities"]
        if item.get("status", "active") == "active"
    }
    for change in changes:
        name = change["skill_name"]
        new_value = change.get("new_value") or {}
        if change["change_type"] == "delete" or new_value.get("status") == "candidate":
            proposal_by_name.pop(name, None)
        elif name in aggregated_by_name:
            proposal_by_name[name] = aggregated_by_name[name]
    level_proposal, level_changes = evolution.apply_review_level_overrides(
        list(proposal_by_name.values()), parsed_new_jds)
    proposal_by_name = {item["name"]: item for item in level_proposal}
    for level_change in level_changes:
        existing = next((item for item in changes
                         if item["skill_name"] == level_change["skill_name"]), None)
        if not existing:
            changes.append(level_change)
            continue
        existing["old_value"] = {
            **(existing.get("old_value") or {}),
            **level_change["old_value"],
        }
        existing["new_value"] = {
            **(existing.get("new_value") or {}),
            **level_change["new_value"],
        }
        existing["reason"] = f'{existing.get("reason") or "能力字段变化"}；{level_change["reason"]}'
        existing["data_source"] = {
            **(existing.get("data_source") or {}),
            **level_change["data_source"],
        }
    proposed_snapshot = {
        "job_id": job.id,
        "from_version": job.version or 1,
        "version": (job.version or 1) + 1,
        "capabilities": [proposal_by_name[name] for name in sorted(proposal_by_name)],
    }
    # Compatibility endpoint is permanently preview-only. Public publication must pass
    # through EvolutionRun proposal, append-only review, and reconciled transaction.
    return {"ok": True, "job_id": job.id, "dry_run": True,
            "proposal_required": True,
            "admin_evolution_runs_endpoint": "/api/admin/evolution-runs",
            "evolution": {"version": job.version, "changes_applied": 0,
                          "added": sum(1 for c in changes if c["change_type"] == "add"),
                          "deleted": sum(1 for c in changes if c["change_type"] == "delete"),
                          "modified": sum(1 for c in changes if c["change_type"] == "modify")},
            "changes": changes, "stats": agg["stats"],
            "proposed_snapshot": proposed_snapshot,
            "job": graph_service.job_to_dict(db, job),
            "notice": (READ_ONLY_MESSAGE if is_read_only()
                       else "预览不会修改公共图谱；请在管理员演化任务中提交、审核并发布")}
