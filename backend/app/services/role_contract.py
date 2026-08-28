"""Build the governed application projection consumed by matching and UI.

The evidence/knowledge graph remains complete.  A RoleContract only projects
validated skills into a small set of same-granularity capability clusters.
"""
from __future__ import annotations

import hashlib
from collections import defaultdict
from datetime import date

from .job_resolution import role_skill_conflict
from .taxonomy import capability_cluster, normalize_skill


MIN_EMPLOYERS = 2
MIN_CLUSTERS = 8
MAX_CLUSTERS = 12
MAX_REQUIRED = 10
MAX_BONUS = 4

_LEVEL_RANK = {"familiar": 1, "proficient": 2, "expert": 3}


def _level_max(values: list[str]) -> str:
    return max(values or ["familiar"], key=lambda x: _LEVEL_RANK.get(x, 1))


def _employer_count(capability: dict) -> int:
    """Read the new explicit count, with JobSkill.source_count compatibility."""
    value = (capability.get("employer_count") if "employer_count" in capability
             else capability.get("source_count"))
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def build_role_contract(
    capabilities: list[dict],
    *,
    job_id: int | None = None,
    job_name: str = "",
    seniority: str = "unspecified",
    recruitment_type: str = "unspecified",
    track: str = "software",
    industry: str = "general",
    evidence_window: dict | None = None,
    version: int = 1,
    min_employers: int = MIN_EMPLOYERS,
) -> dict:
    """Project active, employer-validated capabilities into 8-12 clusters.

    Evidence-poor roles return fewer clusters with ``evidence_insufficient``;
    synthetic filler is never added just to reach eight.
    """
    groups: dict[str, list[dict]] = defaultdict(list)
    rejected = {"inactive": 0, "employer_gate": 0, "track_conflict": 0}
    for cap in capabilities:
        if cap.get("status", "active") != "active":
            rejected["inactive"] += 1
            continue
        employers = _employer_count(cap)
        if employers < min_employers:
            rejected["employer_gate"] += 1
            continue
        name = normalize_skill(cap.get("name", ""))
        parent = cap.get("parent_name") or cap.get("parent")
        if not name:
            continue
        if (role_skill_conflict(job_name, track, name)
                or (parent and role_skill_conflict(job_name, track, parent))):
            rejected["track_conflict"] += 1
            continue
        groups[capability_cluster(name, parent)].append({**cap, "name": name,
                                                         "employer_count": employers})

    clusters = [_summarize_cluster(name, items) for name, items in groups.items()]
    required = sorted((c for c in clusters if c["importance"] == "required"),
                      key=_rank, reverse=True)
    bonus = sorted((c for c in clusters if c["importance"] == "bonus"),
                   key=_rank, reverse=True)

    # Reserve two bonus positions when available; otherwise required may use all 12.
    reserve = min(2, len(bonus))
    selected_required = required[:min(MAX_REQUIRED, MAX_CLUSTERS - reserve)]
    remaining = MAX_CLUSTERS - len(selected_required)
    selected_bonus = bonus[:min(MAX_BONUS, remaining)]
    selected = selected_required + selected_bonus

    status = "ready" if (len(selected) >= MIN_CLUSTERS and len(selected_required) >= 6) \
        else "evidence_insufficient"
    contract_key = "|".join((str(job_id or "transient"), job_name, seniority,
                             recruitment_type, track, industry, str(version)))
    contract_id = hashlib.sha256(contract_key.encode("utf-8")).hexdigest()[:20]
    window = evidence_window or {"start": None, "end": date.today().isoformat()}
    return {
        "contract_id": contract_id,
        "job_id": job_id,
        "job_name": job_name,
        "seniority": seniority,
        "recruitment_type": recruitment_type,
        "track": track,
        "industry": industry,
        "evidence_window": window,
        "version": version,
        "status": status,
        "clusters": selected,
        "summary": {
            "cluster_count": len(selected),
            "required_count": len(selected_required),
            "bonus_count": len(selected_bonus),
            "eligible_cluster_count": len(clusters),
            "rejected": rejected,
        },
    }


def _summarize_cluster(name: str, items: list[dict]) -> dict:
    weight_sum = sum(max(0.01, float(i.get("weight", 0.5))) for i in items)
    confidence = sum(float(i.get("confidence", 0.0)) * max(0.01, float(i.get("weight", 0.5)))
                     for i in items) / max(0.01, weight_sum)
    required_score = sum(
        max(0.01, float(i.get("weight", 0.5))) * max(0.01, float(i.get("support_ratio", 0.0)))
        for i in items if i.get("importance") == "required")
    bonus_score = sum(
        max(0.01, float(i.get("weight", 0.5))) * max(0.01, float(i.get("support_ratio", 0.0)))
        for i in items if i.get("importance") != "required")
    importance = "required" if required_score and required_score >= bonus_score else "bonus"
    ordered = sorted(items, key=lambda i: (
        i.get("importance") == "required", float(i.get("weight", 0)),
        float(i.get("confidence", 0))), reverse=True)
    seen, skills = set(), []
    for item in ordered:
        dedup_key = item["name"].casefold()
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        skills.append({
            "name": item["name"],
            "parent": item.get("parent_name") or item.get("parent"),
            "granularity": item.get("granularity", "coarse"),
            "importance": item.get("importance", "bonus"),
            "weight": round(float(item.get("weight", 0.5)), 4),
            "confidence": round(float(item.get("confidence", 0.0)), 4),
            "employer_count": _employer_count(item),
            "jd_support_count": int(item.get("jd_support_count") or item.get("mention_count") or 0),
        })
    return {
        "name": name,
        "importance": importance,
        "weight": round(min(1.0, max(float(i.get("weight", 0.5)) for i in items)), 4),
        "confidence": round(confidence, 4),
        "level_required": _level_max([i.get("level_required", "familiar") for i in items]),
        "support_ratio": round(max(float(i.get("support_ratio", 0.0)) for i in items), 4),
        "employer_count": max(_employer_count(i) for i in items),
        "importance_evidence": {
            "required_score": round(required_score, 4),
            "bonus_score": round(bonus_score, 4),
        },
        "skills": skills,
    }


def _rank(cluster: dict) -> tuple[float, float, float]:
    return (float(cluster["weight"]), float(cluster["confidence"]),
            float(cluster["support_ratio"]))


def build_contract_from_job(db, job, **overrides) -> dict:
    """Build a contract from the current persisted Job/JobSkill snapshot."""
    from .. import models
    from .graph_service import job_to_dict

    detail = job_to_dict(db, job, include_candidates=True)
    source = detail.get("source_summary") or {}
    seniority = overrides.get("seniority", job.level or "unspecified")
    recruitment_type = overrides.get(
        "recruitment_type", getattr(job, "recruitment_type", None)
        or source.get("recruitment_type", "unspecified"))
    track = overrides.get("track", getattr(job, "track", None)
                          or source.get("track", "software"))
    industry = overrides.get("industry", getattr(job, "industry", None)
                             or source.get("industry", "general"))
    level_rows = []
    selected_slice = None
    if seniority in {"junior", "middle", "senior"}:
        recruitment_options = ({recruitment_type, "unspecified"}
                               if recruitment_type in {"campus", "social"}
                               else {"unspecified"})
        track_options = {track, "unspecified"}
        industry_options = {industry, "general"}
        candidate_rows = db.query(models.JobLevelSkill, models.Skill).join(
            models.Skill, models.Skill.id == models.JobLevelSkill.skill_id).filter(
                models.JobLevelSkill.job_id == job.id,
                models.JobLevelSkill.level == seniority,
                models.JobLevelSkill.recruitment_type.in_(recruitment_options),
                models.JobLevelSkill.track.in_(track_options),
                models.JobLevelSkill.industry.in_(industry_options)).all()
        grouped: dict[tuple[str, str, str], list] = defaultdict(list)
        for row in candidate_rows:
            grouped[(row[0].recruitment_type, row[0].track, row[0].industry)].append(row)
        if grouped:
            def slice_rank(key: tuple[str, str, str]) -> tuple[int, int, int, int, int]:
                # Track is the strongest boundary: software and hardware test
                # evidence must never trade places merely because one bucket is
                # larger. Recruitment and industry then refine the slice.
                specificity = sum((key[0] != "unspecified", key[1] != "unspecified",
                                   key[2] not in {"general", "unspecified"}))
                return (key[1] == track, key[0] == recruitment_type,
                        key[2] == industry, specificity, len(grouped[key]))
            selected_slice = max(grouped, key=slice_rank)
            level_rows = grouped[selected_slice]
    if level_rows:
        caps = [{
            "name": skill.name, "category": skill.category,
            "skill_type": skill.skill_type, "importance": row.importance,
            "weight": row.weight, "level_required": row.level_required,
            "confidence": row.confidence, "factors": row.factors,
            "source_count": row.source_count, "employer_count": row.source_count,
            "jd_support_count": row.jd_count, "status": "active",
            "granularity": "coarse",
        } for row, skill in level_rows]
        slice_source = "job_level_skill"
    else:
        caps = detail.get("required_skills", []) + detail.get("bonus_skills", [])
        slice_source = "job_skill_fallback"
    contract = build_role_contract(
        caps,
        job_id=job.id,
        job_name=job.name,
        seniority=seniority,
        recruitment_type=recruitment_type,
        track=track,
        industry=industry,
        evidence_window=overrides.get("evidence_window", source.get("evidence_window")),
        version=job.version or 1,
    )
    contract["slice_source"] = slice_source
    requested_slice = {
        "seniority": seniority, "recruitment_type": recruitment_type,
        "track": track, "industry": industry,
    }
    selected_slice_dict = ({
        "seniority": seniority, "recruitment_type": selected_slice[0],
        "track": selected_slice[1], "industry": selected_slice[2],
    } if selected_slice else None)
    fallback_dimensions = [
        field for field, requested in requested_slice.items()
        if selected_slice_dict is None or selected_slice_dict[field] != requested
    ]
    contract["slice_resolution"] = {
        "requested": requested_slice,
        "selected": selected_slice_dict,
        "exact": not fallback_dimensions,
        "fallback_dimensions": fallback_dimensions,
    }
    return contract


def contract_summaries_for_jobs(db, jobs: list) -> dict[int, dict]:
    """Build list-card contract summaries in a bounded number of queries.

    The full contract endpoint may resolve a seniority slice for one role.  A
    job-library page needs the current graph projection for many roles at once,
    so this helper batches capabilities and evidence-backed employer units.
    """
    from .. import models

    job_ids = {job.id for job in jobs}
    if not job_ids:
        return {}
    relation_rows = (db.query(models.JobSkill, models.Skill)
                     .join(models.Skill, models.Skill.id == models.JobSkill.skill_id)
                     .filter(models.JobSkill.job_id.in_(job_ids),
                             models.JobSkill.status == "active").all())
    parent_ids = {skill.parent_id for _, skill in relation_rows if skill.parent_id}
    parent_names = dict(db.query(models.Skill.id, models.Skill.name).filter(
        models.Skill.id.in_(parent_ids)).all()) if parent_ids else {}

    capabilities: dict[int, list[dict]] = defaultdict(list)
    relation_ids: dict[int, int] = {}
    for relation, skill in relation_rows:
        relation_ids[relation.id] = relation.job_id
        factors = relation.factors or {}
        capabilities[relation.job_id].append({
            "name": skill.name,
            "parent_name": parent_names.get(skill.parent_id),
            "category": skill.category,
            "skill_type": skill.skill_type,
            "importance": relation.importance,
            "weight": relation.weight,
            "level_required": relation.level_required,
            "confidence": relation.confidence,
            "factors": factors,
            "support_ratio": float(factors.get("support", 0.0) or 0.0),
            "source_count": relation.source_count,
            "employer_count": relation.source_count,
            "status": relation.status,
            "granularity": "fine" if skill.parent_id else "coarse",
        })

    employer_rows = (db.query(models.Evidence.job_skill_id, models.RawJD.employer_id)
                     .join(models.RawJD, models.RawJD.id == models.Evidence.raw_jd_id)
                     .filter(models.Evidence.job_skill_id.in_(relation_ids),
                             models.Evidence.source_type == "jd",
                             models.RawJD.is_duplicate == False,  # noqa: E712
                             models.RawJD.duplicate_of.is_(None),
                             models.RawJD.employer_id.isnot(None)).all()) if relation_ids else []
    employer_ids = {employer_id for _, employer_id in employer_rows if employer_id}
    employers = {row.id: row for row in db.query(models.Employer).filter(
        models.Employer.id.in_(employer_ids)).all()} if employer_ids else {}
    parent_employer_ids = {row.parent_id for row in employers.values() if row.parent_id}
    if parent_employer_ids:
        employers.update({row.id: row for row in db.query(models.Employer).filter(
            models.Employer.id.in_(parent_employer_ids)).all()})
    employer_units: dict[int, set[int]] = defaultdict(set)
    for relation_id, employer_id in employer_rows:
        employer = employers.get(employer_id)
        if not employer or employer.status != "active":
            continue
        unit_id = employer.parent_id or employer.id
        parent = employers.get(unit_id)
        if parent and parent.status == "active":
            employer_units[relation_ids[relation_id]].add(unit_id)

    result = {}
    for job in jobs:
        source = job.source_summary or {}
        contract = build_role_contract(
            capabilities.get(job.id, []),
            job_id=job.id,
            job_name=job.name,
            seniority=job.level or "unspecified",
            recruitment_type=job.recruitment_type or source.get(
                "recruitment_type", "unspecified"),
            track=job.track or source.get("track", "software"),
            industry=job.industry or source.get("industry", "general"),
            evidence_window=source.get("evidence_window"),
            version=job.version or 1,
        )
        result[job.id] = {
            "required_count": len(contract["clusters"]),
            "contract_status": contract["status"],
            "employer_count": len(employer_units.get(job.id, set())),
        }
    return result


def build_contract_from_version(db, job, job_version) -> dict:
    """Build a contract from immutable version rows and their valid evidence."""
    from .. import models

    rows = (db.query(models.JobVersionSkill, models.Skill)
            .join(models.Skill, models.Skill.id == models.JobVersionSkill.skill_id)
            .filter(models.JobVersionSkill.job_version_id == job_version.id).all())

    def referenced_ids(row) -> set[int]:
        values = set()
        for ref in row.evidence_refs or []:
            if not isinstance(ref, dict):
                continue
            try:
                raw_id = int(ref.get("raw_jd_id"))
            except (TypeError, ValueError):
                continue
            if raw_id > 0:
                values.add(raw_id)
        return values

    refs_by_row = {row.id: referenced_ids(row) for row, _ in rows}
    raw_ids = set().union(*refs_by_row.values()) if refs_by_row else set()
    raw_jds = {raw.id: raw for raw in db.query(models.RawJD).filter(
        models.RawJD.id.in_(raw_ids)).all()} if raw_ids else {}
    employer_ids = {raw.employer_id for raw in raw_jds.values() if raw.employer_id}
    employers = {}
    pending_ids = set(employer_ids)
    while pending_ids:
        fetched = {row.id: row for row in db.query(models.Employer).filter(
            models.Employer.id.in_(pending_ids)).all()}
        employers.update(fetched)
        pending_ids = {row.parent_id for row in fetched.values()
                       if row.parent_id and row.parent_id not in employers}

    def active_employer_unit(employer_id: int | None) -> int | None:
        current_id, visited = employer_id, set()
        while current_id is not None:
            if current_id in visited:
                return None
            visited.add(current_id)
            employer = employers.get(current_id)
            if not employer or employer.status != "active":
                return None
            if employer.parent_id is None:
                return employer.id
            current_id = employer.parent_id
        return None

    valid_raw_units = {}
    for raw_id, raw in raw_jds.items():
        if raw.is_duplicate or raw.duplicate_of is not None:
            continue
        employer_unit = active_employer_unit(raw.employer_id)
        if employer_unit is not None:
            valid_raw_units[raw_id] = employer_unit

    existing_window = dict(job_version.evidence_window or {})
    stored_dimensions = existing_window.get("dimensions")
    stored_dimensions = stored_dimensions if isinstance(stored_dimensions, dict) else {}
    prior_contract = job_version.contract_snapshot or {}
    dimensions = {
        "job_name": stored_dimensions.get("job_name") or prior_contract.get("job_name")
                    or job.name,
        "seniority": stored_dimensions.get("seniority") or prior_contract.get("seniority")
                     or job.level or "unspecified",
        "recruitment_type": stored_dimensions.get("recruitment_type")
                            or prior_contract.get("recruitment_type")
                            or job.recruitment_type or "unspecified",
        "track": stored_dimensions.get("track") or prior_contract.get("track")
                 or job.track or "software",
        "industry": stored_dimensions.get("industry") or prior_contract.get("industry")
                    or job.industry or "general",
    }
    active_raw_ids = set().union(*(
        refs_by_row[row.id] for row, _ in rows if row.status == "active"
    )) if any(row.status == "active" for row, _ in rows) else set()
    valid_active_raw_ids = active_raw_ids.intersection(valid_raw_units)
    evidence_window = {
        **existing_window,
        "jd_count": len(valid_active_raw_ids),
        "employer_count": len({valid_raw_units[raw_id] for raw_id in valid_active_raw_ids}),
        "dimensions": dimensions,
    }
    job_version.evidence_window = evidence_window

    capabilities = []
    for row, skill in rows:
        referenced_raw_ids = refs_by_row[row.id]
        valid_raw_ids = referenced_raw_ids.intersection(valid_raw_units)
        employer_count = len({valid_raw_units[raw_id] for raw_id in valid_raw_ids})
        factors = row.factors or {}
        parent = row.capability_cluster if row.capability_cluster != skill.name else None
        capabilities.append({
            "name": skill.name,
            "parent": parent,
            "category": skill.category,
            "skill_type": skill.skill_type,
            "importance": row.importance,
            "status": row.status,
            "weight": row.weight,
            "confidence": row.confidence,
            "level_required": row.level_required,
            "factors": factors,
            "support_ratio": len(valid_raw_ids) / max(1, len(referenced_raw_ids)),
            "employer_count": employer_count,
            "jd_support_count": len(valid_raw_ids),
            "granularity": "coarse",
        })

    requested_slice = {
        key: dimensions[key]
        for key in ("seniority", "recruitment_type", "track", "industry")
    }
    contract = build_role_contract(
        capabilities,
        job_id=job.id,
        job_name=dimensions["job_name"],
        evidence_window=evidence_window,
        version=job_version.version,
        **requested_slice,
    )
    contract["slice_source"] = "job_version_skill"
    contract["slice_resolution"] = {
        "requested": requested_slice,
        "selected": requested_slice,
        "exact": True,
        "fallback_dimensions": [],
    }
    return contract


def matching_capabilities(contract: dict) -> list[dict]:
    """Adapt contract clusters to the existing matching service input shape."""
    return [{
        "name": cluster["name"],
        "importance": cluster["importance"],
        "weight": cluster["weight"],
        "level_required": cluster["level_required"],
        "confidence": cluster["confidence"],
        "category": cluster["name"],
        "status": "active",
        "skills": cluster["skills"],
    } for cluster in contract.get("clusters", [])]
