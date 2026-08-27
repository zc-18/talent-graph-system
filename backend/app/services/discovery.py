"""新岗位发现与定义（RAG 接地，反幻觉）。

赛题核心功能①：识别萌芽/兴起中的新岗位并生成定义。
流程：候选发现 → Tavily 检索证据 → 大模型基于证据生成定义 → 交叉验证置信度。
"""
from __future__ import annotations
import json
from datetime import datetime
from pathlib import Path
from sqlalchemy.orm import Session

from .. import clients, models
from . import confidence as confmod
from .cleaning import freshness_weight
from .taxonomy import clean_skill_name, skill_category, skill_type, CATEGORIES
from .employer_resolution import employer_independence_key
from .job_resolution import resolve_job_query

# 权威佐证登记表（人社部新职业文件 / 头部机构报告），见 data/authority/
_AUTH_FILE = Path(__file__).resolve().parents[2] / "data" / "authority" / "authority_sources.json"


def authority_matches(keyword: str) -> list[dict]:
    """按关键词匹配权威佐证条目，返回证据格式（kind=policy/report 标记权威级）。"""
    try:
        data = json.loads(_AUTH_FILE.read_text("utf-8"))["sources"]
    except Exception:
        return []
    out = []
    for key, entries in data.items():
        if key in keyword or keyword in key:
            for e in entries:
                if e.get("emergence_type") == "faded":
                    continue  # 回落佐证不作为新兴证据
                out.append({
                    "title": e["title"], "url": e.get("url", ""),
                    "content": e.get("excerpt", ""), "provider": e.get("issuer", ""),
                    "kind": e.get("kind"), "publish_date": e.get("publish_date"),
                    "local_file": e.get("local_file", ""),
                    "emergence_type": e.get("emergence_type", "new"),
                })
    return out

# 新一代信息技术领域典型新兴岗位候选种子
EMERGING_SEEDS = [
    "提示词工程师 Prompt Engineer",
    "大模型应用开发工程师 LLM Application Engineer",
    "AI智能体开发工程师 AI Agent Engineer",
    "RAG检索增强工程师",
    "AIGC算法工程师",
    "具身智能工程师 Embodied AI",
    "MLOps工程师 大模型运维",
    "向量数据库工程师",
    "AI产品经理",
    "大模型评测工程师 LLM Evaluation",
    "多模态算法工程师",
    "AI数据标注与对齐工程师",
]


def discover_candidates(keyword: str, max_results: int = 6) -> dict:
    """Search evidence after the established-job veto and score emergence."""
    resolution = resolve_job_query(keyword)
    if resolution.requires_disambiguation:
        return {
            "keyword": keyword, "evidence": [], "emergence_score": 0.0,
            "evidence_count": 0, "independent_sources": 0,
            "employer_count": 0, "channel_count": 0, "authority_count": 0,
            "verdict": "AMBIGUOUS", "existing_job": None,
            "resolution": resolution.to_dict(),
            "signals": {"requires_disambiguation": True},
        }
    if resolution.is_established:
        return {
            "keyword": keyword, "evidence": [], "emergence_score": 0.0,
            "evidence_count": 0, "independent_sources": 0,
            "employer_count": 0, "channel_count": 0, "authority_count": 0,
            "verdict": "ESTABLISHED", "existing_job": resolution.canonical_title,
            "resolution": resolution.to_dict(),
            "signals": {"mature_occupation_veto": True},
        }
    authority = authority_matches(keyword)
    results = clients.multi_source_search(f"{keyword} 岗位 招聘 任职要求 2025 2026", max_results=max_results)
    news = clients.tavily_search(f"{keyword} 新兴职业 趋势", max_results=4, days=180)
    evidence = list(authority)  # 权威条目置顶
    for r in results:
        evidence.append({**r, "title": r.get("title", ""), "url": r.get("url", ""),
                         "content": (r.get("content") or "")[:600], "provider": r.get("provider", "")})
    for r in news:
        evidence.append({**r, "title": r.get("title", ""), "url": r.get("url", ""),
                         "content": (r.get("content") or "")[:600], "provider": "tavily-news"})
    scored = score_emergence(keyword, evidence)
    return {"keyword": keyword, "evidence": evidence,
            "emergence_score": scored["emergence_score"],
            "evidence_count": len(evidence),
            "independent_sources": scored["employer_count"],
            "employer_count": scored["employer_count"],
            "channel_count": scored["channel_count"],
            "authority_count": len(authority),
            "verdict": scored["verdict"], "existing_job": None,
            "resolution": resolution.to_dict(), "signals": scored["signals"],
            "emergence_type": next((e.get("emergence_type") for e in authority
                                    if e.get("emergence_type")), None)}


def score_emergence(keyword: str, evidence: list[dict], history: dict | None = None) -> dict:
    """Score occupation emergence using signals that search volume cannot inflate."""
    resolution = resolve_job_query(keyword)
    channels = {e.get("provider") for e in evidence if e.get("provider")}
    employers = {employer_independence_key(e) for e in evidence
                 if employer_independence_key(e)}
    regions = {e.get("region") or e.get("location") for e in evidence
               if e.get("region") or e.get("location")}
    industries = {e.get("industry") for e in evidence if e.get("industry")}
    kinds = {e.get("kind") for e in evidence}
    if resolution.is_established:
        return {"emergence_score": 0.0, "verdict": "ESTABLISHED",
                "employer_count": len(employers), "channel_count": len(channels),
                "signals": {"mature_occupation_veto": True}}

    history_available = history is not None
    history = history or {}
    old_count = int(history.get("2018", 0)) + int(history.get("2024", 0))
    current_count = int(history.get("2026", 0))
    historical_novelty = (1.0 if current_count and old_count == 0 else
                          min(1.0, max(0.0, (current_count - old_count) / max(1, old_count)))) \
        if history_available and current_count else 0.0
    authority_strength = 1.0 if "policy" in kinds else (0.75 if "report" in kinds else 0.0)
    employer_diffusion = min(1.0, len(employers) / 3.0)
    market_spread = min(1.0, (len(regions) + len(industries)) / 4.0)
    novelty_markers = ("智能体", "具身智能", "大模型", "aigc", "数字人", "生成式人工智能")
    naming_novelty = 1.0 if any(x in keyword.casefold() for x in novelty_markers) else 0.35
    score = round(0.35 * authority_strength + 0.25 * historical_novelty
                  + 0.20 * employer_diffusion + 0.10 * market_spread
                  + 0.10 * naming_novelty, 3)
    verdict = "EMERGING" if score >= 0.6 and len(employers) >= 2 else "INSUFFICIENT_EVIDENCE"
    return {
        "emergence_score": score, "verdict": verdict,
        "employer_count": len(employers), "channel_count": len(channels),
        "signals": {
            "mature_occupation_veto": False,
            "authority_strength": authority_strength,
            "historical_novelty": round(historical_novelty, 3),
            "history_available": history_available,
            "employer_diffusion": round(employer_diffusion, 3),
            "market_spread": round(market_spread, 3),
            "naming_novelty": naming_novelty,
            "old_corpus_count": old_count,
            "current_corpus_count": current_count,
        },
    }


_DEFINE_SYS = """你是新兴岗位研究专家，专注新一代信息技术领域(人工智能/大数据/智能系统/物联网)。
基于提供的网络证据材料，定义一个正在兴起的新岗位。严格要求：
1. 岗位定义必须基于证据材料，不臆造证据中没有依据的内容（防幻觉）。
2. 技能点要细粒度、具体、可验证，且**必须是简洁的单个技术名词**（如"LangChain""提示工程""向量数据库""模型微调"），不超过8个字，禁止用斜杠/逗号罗列多个、禁止加括号说明、禁止写成一句话。
3. 必备技能(required)与加分技能(bonus)要区分清楚。
只输出JSON。"""

_DEFINE_TPL = """目标新兴岗位：{keyword}

网络证据材料：
{evidence}

请基于以上证据定义该岗位，输出JSON：
{{
  "job_title": "规范岗位名称",
  "category": "从[人工智能,大数据,智能系统,物联网,云计算与工程]选一个",
  "level": "junior/middle/senior",
  "summary": "岗位简介(2-3句)",
  "core_responsibilities": ["核心职责1","职责2","职责3","职责4"],
  "required_skills": [{{"name":"必备技能点","level":"familiar/proficient/expert","reason":"证据依据"}}],
  "bonus_skills": [{{"name":"加分技能点"}}],
  "typical_scenarios": ["典型行业应用场景1","场景2","场景3"]
}}"""


def define_new_job(keyword: str, evidence: list[dict]) -> dict:
    """基于证据生成新岗位定义，并对能力项做交叉验证置信度评估。"""
    ev_text = "\n\n".join(
        f"[{i+1}] {e['title']}\n{e['content']}" for i, e in enumerate(evidence[:8])) or "（暂无外部证据，依据领域常识谨慎定义）"
    messages = [
        {"role": "system", "content": _DEFINE_SYS},
        {"role": "user", "content": _DEFINE_TPL.format(keyword=keyword, evidence=ev_text[:5000])},
    ]
    data = clients.chat_json(messages, temperature=0.3, max_tokens=2000)
    return _postprocess_definition(data, keyword, evidence)


def _postprocess_definition(data: dict, keyword: str, evidence: list[dict]) -> dict:
    cat = data.get("category", "")
    if cat not in CATEGORIES:
        cat = "人工智能"
    ev_count = len(evidence)

    def make_cap(item, importance):
        name = clean_skill_name(item.get("name", "") if isinstance(item, dict) else item)
        if not name:
            return None
        supporting = [e for e in evidence if _evidence_supports_skill(e, name)]
        in_evidence = bool(supporting)
        employers = {employer_independence_key(e) for e in supporting
                     if employer_independence_key(e)}
        channels = {e.get("provider", "") for e in supporting if e.get("provider")}
        has_authority = any(e.get("kind") in ("policy", "report") for e in supporting)
        # 统一置信度公式（services.confidence，与既有岗位同一公式，只是因子来源不同）
        factors = confmod.factors_from_web(
            # Legacy parameter name; diversity is employer entities, not channels.
            in_evidence=in_evidence, providers=employers, ev_count=0,
            has_authority_doc=has_authority)
        conf = confmod.compute(factors)
        employer_count = len(employers)
        active = conf >= 0.45 and employer_count >= 2
        return {
            "name": name, "importance": importance,
            "weight": 0.7 if importance == "required" else 0.4,
            "level_required": item.get("level", "familiar") if isinstance(item, dict) else "familiar",
            "category": skill_category(name), "skill_type": skill_type(name),
            "confidence": conf, "factors": factors,
            "source_count": employer_count, "employer_count": employer_count,
            "jd_support_count": len(supporting), "channel_count": len(channels),
            "support_ratio": round(len(supporting) / max(1, ev_count), 3),
            "web_verified": in_evidence, "status": "active" if active else "candidate",
            "evidence": [{"source_type": "web", "snippet": f"证据支撑: {name}",
                          "url": e.get("url", ""),
                          "employer_id": employer_independence_key(e)}
                         for e in supporting[:6]],
        }

    caps = []
    for it in data.get("required_skills", []):
        c = make_cap(it, "required")
        if c:
            caps.append(c)
    req_names = {c["name"] for c in caps}
    for it in data.get("bonus_skills", []):
        c = make_cap(it, "bonus")
        if c and c["name"] not in req_names:
            caps.append(c)

    resolution = resolve_job_query(data.get("job_title") or keyword)
    scored = score_emergence(keyword, evidence)
    return {
        "job_title": (data.get("job_title") or keyword).strip(),
        "category": cat, "level": data.get("level", "middle"),
        "track": data.get("track") or resolution.track,
        "industry": data.get("industry") or resolution.industry,
        "recruitment_type": data.get("recruitment_type") or (
            "mixed" if resolution.recruitment_type == "unspecified" else resolution.recruitment_type),
        "summary": data.get("summary", ""),
        "core_responsibilities": [r for r in data.get("core_responsibilities", []) if r][:8],
        "typical_scenarios": [s for s in data.get("typical_scenarios", []) if s][:6],
        "capabilities": caps,
        "emergence_score": scored["emergence_score"],
        "emergence_verdict": scored["verdict"],
        "source_summary": {"evidence_count": ev_count,
                           "sources": [e.get("url", "") for e in evidence[:6] if e.get("url")],
                           "generated_at": datetime.utcnow().isoformat()},
    }


def _evidence_supports_skill(evidence: dict, skill_name: str) -> bool:
    blob = f"{evidence.get('title', '')} {evidence.get('content', '')}".casefold()
    name = skill_name.casefold()
    if name in blob:
        return True
    tokens = [token for token in name.replace("-", " ").split() if len(token) > 2]
    return any(token in blob for token in tokens)


def candidate_publishability(
    db: Session,
    candidate: models.JobCandidate,
    definition: dict,
) -> dict:
    """Rebuild candidate publication facts from persisted discovery and JD data."""
    from .role_contract import build_role_contract

    run = db.get(models.DiscoveryRun, candidate.discovery_run_id) \
        if candidate.discovery_run_id else None
    signals = (run.signal_snapshot or {}) if run else {}
    run_evidence = (run.evidence_snapshot or []) if run else []
    has_authority_evidence = any(
        isinstance(item, dict) and item.get("kind") in {"policy", "report"}
        for item in run_evidence)
    emergence_confirmed = bool(
        run and run.owner_user_id == candidate.owner_user_id
        and run.organization_id == candidate.organization_id
        and run.conclusion == "NEW"
        and str(signals.get("source_verdict") or "").upper() == "EMERGING")

    proposed = [cap for cap in definition.get("capabilities", []) or []
                if isinstance(cap, dict) and cap.get("status") == "active"]
    evidence_ids: set[int] = set()
    cap_evidence_ids: list[tuple[dict, list[int]]] = []
    for cap in proposed:
        ids = []
        for evidence in cap.get("evidence", []) or []:
            if not isinstance(evidence, dict):
                continue
            try:
                raw_jd_id = int(evidence.get("raw_jd_id"))
            except (TypeError, ValueError):
                continue
            if raw_jd_id <= 0 or raw_jd_id in ids:
                continue
            ids.append(raw_jd_id)
            evidence_ids.add(raw_jd_id)
        cap_evidence_ids.append((cap, ids))

    raw_jds = {row.id: row for row in db.query(models.RawJD).filter(
        models.RawJD.id.in_(evidence_ids)).all()} if evidence_ids else {}
    employer_ids = {row.employer_id for row in raw_jds.values() if row.employer_id}
    employers = {row.id: row for row in db.query(models.Employer).filter(
        models.Employer.id.in_(employer_ids)).all()} if employer_ids else {}
    parent_ids = {row.parent_id for row in employers.values() if row.parent_id}
    if parent_ids:
        employers.update({row.id: row for row in db.query(models.Employer).filter(
            models.Employer.id.in_(parent_ids)).all()})

    active = []
    supporting_dates = []
    for cap, raw_jd_ids in cap_evidence_ids:
        skill_name = str(cap.get("name") or "").strip()
        supporting = []
        independent_employers: set[int] = set()
        freshness_values = []
        authority_values = []
        for raw_jd_id in raw_jd_ids:
            raw_jd = raw_jds.get(raw_jd_id)
            if (not raw_jd or raw_jd.is_duplicate or raw_jd.duplicate_of is not None
                    or not raw_jd.employer_id):
                continue
            employer = employers.get(raw_jd.employer_id)
            parent = employers.get(employer.parent_id) if employer and employer.parent_id else None
            if (not employer or employer.status != "active"
                    or (employer.parent_id and (not parent or parent.status != "active"))):
                continue
            if not skill_name or not _evidence_supports_skill(
                    {"title": raw_jd.job_title or "", "content": raw_jd.raw_text or ""},
                    skill_name):
                continue
            independent_employers.add(employer.parent_id or employer.id)
            freshness_values.append(freshness_weight(max(0, int(raw_jd.lag_days or 0))))
            authority_values.append(float(
                raw_jd.source_authority if raw_jd.source_authority is not None else 0.6))
            if raw_jd.publish_date:
                supporting_dates.append(raw_jd.publish_date.date())
            supporting.append({
                "raw_jd_id": raw_jd.id,
                "source_type": "jd",
                "source": raw_jd.platform or raw_jd.source or "",
                "source_url": raw_jd.source_url or "",
                "snippet": (raw_jd.raw_text or raw_jd.job_title or "")[:500],
            })
        employer_count = len(independent_employers)
        if employer_count < 2:
            continue
        support_ratio = len(supporting) / max(1, len(raw_jd_ids))
        factors = confmod.factors_from_jd(
            support_ratio=support_ratio,
            platforms={str(value) for value in independent_employers},
            avg_freshness=sum(freshness_values) / max(1, len(freshness_values)),
            avg_authority=sum(authority_values) / max(1, len(authority_values)),
            has_web=has_authority_evidence,
        )
        active.append({
            **cap,
            "status": "active",
            "confidence": confmod.compute(factors),
            "factors": factors,
            "employer_count": employer_count,
            "source_count": employer_count,
            "jd_support_count": len(supporting),
            "support_ratio": round(support_ratio, 4),
            "evidence": supporting,
        })

    reasons = []
    if not emergence_confirmed:
        reasons.append("emergence_not_confirmed")
    if not active:
        reasons.append("no_employer_validated_capability")
    contract = build_role_contract(
        active, job_name=definition.get("job_title", ""),
        seniority=definition.get("level", "unspecified"),
        recruitment_type=definition.get("recruitment_type", "mixed"),
        track=definition.get("track", "product"),
        industry=definition.get("industry", "general"))
    if contract["status"] != "ready":
        reasons.append("core_contract_insufficient")
    authority_evidence = [
        {
            "kind": item.get("kind"),
            "title": item.get("title") or "",
            "issuer": item.get("issuer") or item.get("provider") or "",
            "url": item.get("url") or "",
            "excerpt": item.get("excerpt") or item.get("content") or "",
            "local_file": item.get("local_file") or None,
        }
        for item in run_evidence
        if isinstance(item, dict) and item.get("kind") in {"policy", "report"}
    ]
    evidence_window = {
        "start": min(supporting_dates).isoformat() if supporting_dates else None,
        "end": max(supporting_dates).isoformat() if supporting_dates else None,
    }
    return {"publishable": not reasons, "reasons": reasons,
            "active_capability_count": len(active),
            "contract_cluster_count": contract["summary"]["cluster_count"],
            "emergence_score": float(signals.get("emergence_score") or 0),
            "validated_capabilities": active,
            "validated_authority_evidence": authority_evidence,
            "evidence_window": evidence_window,
            "discovery_evidence_count": len(run_evidence)}
