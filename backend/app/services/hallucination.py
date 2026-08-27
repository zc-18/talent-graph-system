"""能力"幻觉"防控与交叉验证聚合。

赛题创新点②：能力幻觉防控，提升图谱构建科学性。
核心思想：单条来源（含大模型臆造）不可信；只有被≥k个独立来源交叉验证、
或有外部网络证据支撑的能力项，才以高置信度进入图谱。每个能力项保留可溯源证据。

置信度公式统一由 services.confidence 提供（线性加权和，可解释、可分解）。
"""
from __future__ import annotations
from collections import defaultdict
from .cleaning import freshness_weight
from .taxonomy import skill_category
from . import confidence as conf
from .employer_resolution import employer_independence_key

# 阈值
MIN_SOURCES_REQUIRED = 2      # 必备技能至少 2 个独立来源
CONFIDENCE_THRESHOLD = 0.45   # 进入图谱的最低置信度
HIGH_CONFIDENCE = 0.75


def _unique_valid_jds(parsed_jds: list[dict]) -> list[dict]:
    """Drop flagged duplicates and repeated persisted JD identities.

    Ingestion already performs URL/text similarity de-duplication. This guard
    prevents an accidentally repeated ORM row from inflating support counts in
    a recomputation. Rows without an identity are retained because there is no
    defensible way to assert they are the same evidence.
    """
    valid, seen_ids = [], set()
    for jd in parsed_jds:
        if jd.get("is_duplicate"):
            continue
        raw_jd_id = jd.get("raw_jd_id")
        if raw_jd_id not in (None, ""):
            if raw_jd_id in seen_ids:
                continue
            seen_ids.add(raw_jd_id)
        valid.append(jd)
    return valid


def aggregate_capabilities(
    parsed_jds: list[dict],
    web_evidence_skills: set[str] | None = None,
    source_meta: dict[int, dict] | None = None,
    min_employers: int = MIN_SOURCES_REQUIRED,
    high_risk_skills: set[str] | None = None,
) -> dict:
    """聚合同一岗位的多条 JD 解析结果，输出交叉验证后的能力项。

    parsed_jds: [{"required_skills":[...], "bonus_skills":[...], "lag_days":int,
                  "is_duplicate":bool, "raw_jd_id":int, "source":str}]
    web_evidence_skills: 通过 Tavily 等外部检索确认存在的技能名集合（增强可信度）。
    source_meta: raw_jd_id -> {"employer_id": int, "company": str,
                 "platform": str, "authority": float}。不同平台只是渠道，只有不同
                 雇主实体才能通过交叉验证；未知雇主不能撑过多雇主门。

    返回 {"capabilities": [...], "stats": {...}}，每个 capability 含
    confidence/factors/source_count/evidence。
    """
    web_evidence_skills = web_evidence_skills or set()
    source_meta = source_meta or {}
    high_risk_skills = high_risk_skills or set()
    # 只用非重复（去抄袭）来源参与交叉验证
    valid_jds = _unique_valid_jds(parsed_jds)
    total = max(1, len(valid_jds))

    # 技能 -> 统计
    agg: dict[str, dict] = defaultdict(lambda: {
        "required_votes": 0, "bonus_votes": 0, "fresh_sum": 0.0,
        "levels": [], "categories": [], "skill_types": [],
        "evidence": [], "raw_names": set(),
        "employers": set(), "channels": set(), "authority_sum": 0.0,
    })

    for jd in valid_jds:
        fw = freshness_weight(jd.get("lag_days", 0))
        meta = source_meta.get(jd.get("raw_jd_id") or -1, {})
        channel = meta.get("platform") or jd.get("source") or "unknown"
        employer_key = employer_independence_key({**jd, **meta})
        authority = float(meta.get("authority", conf.SOURCE_AUTHORITY["web"]))
        seen_in_jd: set[str] = set()
        for imp_key, items in (("required", jd.get("required_skills", [])),
                                ("bonus", jd.get("bonus_skills", []))):
            for s in items:
                name = s["name"]
                if name in seen_in_jd:
                    continue
                seen_in_jd.add(name)
                a = agg[name]
                if imp_key == "required":
                    a["required_votes"] += 1
                else:
                    a["bonus_votes"] += 1
                a["fresh_sum"] += fw
                if employer_key:
                    a["employers"].add(employer_key)
                a["channels"].add(channel)
                a["authority_sum"] += authority
                a["levels"].append(s.get("level", "familiar"))
                a["categories"].append(s.get("category", "其他"))
                a["skill_types"].append(s.get("skill_type", "hard"))
                a["raw_names"].add(s.get("raw", name))
                if jd.get("raw_jd_id"):
                    a["evidence"].append({
                        "raw_jd_id": jd.get("raw_jd_id"),
                        "source_type": "jd",
                        "source": channel,
                        "employer_id": employer_key,
                        "snippet": s.get("raw", name),
                    })

    capabilities = []
    for name, a in agg.items():
        source_count = a["required_votes"] + a["bonus_votes"]
        employer_count = len(a["employers"])
        validation_threshold = 3 if name in high_risk_skills else min_employers
        support_ratio = source_count / total
        has_web = name in web_evidence_skills
        # 统一置信度公式（services.confidence）：
        # C = 0.35·支持率 + 0.20·来源多样性 + 0.15·时效性 + 0.20·来源权威度 + 0.10·外部验证
        factors = conf.factors_from_jd(
            support_ratio=support_ratio,
            # confidence.py remains the one formula entry.  Its legacy
            # parameter name is ``platforms``; the values are employer keys.
            platforms=a["employers"],
            avg_freshness=a["fresh_sum"] / max(1, source_count),
            avg_authority=a["authority_sum"] / max(1, source_count),
            has_web=has_web,
        )
        confidence = conf.compute(factors)

        importance = "required" if a["required_votes"] >= a["bonus_votes"] else "bonus"
        # 必备项要求更强的交叉验证；不足则降级为加分或丢弃
        if importance == "required" and employer_count < validation_threshold:
            importance = "bonus"

        if confidence < CONFIDENCE_THRESHOLD or employer_count < validation_threshold:
            # 低于阈值视为潜在幻觉/噪音，标记但不进入正式图谱
            status = "candidate"
        else:
            status = "active"

        evidence = a["evidence"][:8]
        if has_web:
            evidence.append({"source_type": "web", "snippet": f"外部检索确认: {name}"})

        capabilities.append({
            "name": name,
            "importance": importance,
            "weight": round(min(1.0, support_ratio + 0.1), 4),
            "level_required": _mode(a["levels"]) or "familiar",
            "category": _mode(a["categories"]) or "其他",
            "skill_type": _mode(a["skill_types"]) or "hard",
            "confidence": confidence,
            "factors": factors,
            # source_count historically meant independent sources.  It now
            # intentionally stores the independent-employer count; raw JD
            # support remains separately available for audit and confidence.
            "source_count": employer_count,
            "employer_count": employer_count,
            "jd_support_count": source_count,
            "channel_count": len(a["channels"]),
            "channels": sorted(a["channels"]),
            "validation_threshold": validation_threshold,
            "support_ratio": round(support_ratio, 4),
            "web_verified": has_web,
            "status": status,
            "evidence": evidence,
        })

    # ---------- 细粒度技能层（两级共存：粗层驱动匹配/评测，细层做具体化展示） ----------
    fine_agg: dict[str, dict] = defaultdict(lambda: {
        "required_votes": 0, "bonus_votes": 0, "fresh_sum": 0.0,
        "levels": [], "parents": [], "evidence": [],
        "employers": set(), "channels": set(), "authority_sum": 0.0,
    })
    for jd in valid_jds:
        fw = freshness_weight(jd.get("lag_days", 0))
        meta = source_meta.get(jd.get("raw_jd_id") or -1, {})
        channel = meta.get("platform") or jd.get("source") or "unknown"
        employer_key = employer_independence_key({**jd, **meta})
        authority = float(meta.get("authority", conf.SOURCE_AUTHORITY["web"]))
        seen_fine: set[str] = set()
        for s in jd.get("fine_skills", []):
            if s["name"] in seen_fine:
                continue
            seen_fine.add(s["name"])
            fa = fine_agg[s["name"]]
            if s.get("importance") == "required":
                fa["required_votes"] += 1
            else:
                fa["bonus_votes"] += 1
            fa["fresh_sum"] += fw
            if employer_key:
                fa["employers"].add(employer_key)
            fa["channels"].add(channel)
            fa["authority_sum"] += authority
            fa["levels"].append(s.get("level", "familiar"))
            fa["parents"].append(s.get("parent"))
            if jd.get("raw_jd_id"):
                fa["evidence"].append({
                    "raw_jd_id": jd.get("raw_jd_id"), "source_type": "jd",
                    "source": channel, "employer_id": employer_key,
                    "snippet": s.get("raw", s["name"]),
                })

    for name, fa in fine_agg.items():
        sc = fa["required_votes"] + fa["bonus_votes"]
        employer_count = len(fa["employers"])
        validation_threshold = 3 if name in high_risk_skills else min_employers
        parent = _mode([p for p in fa["parents"] if p])
        factors = conf.factors_from_jd(
            support_ratio=sc / total, platforms=fa["employers"],
            avg_freshness=fa["fresh_sum"] / max(1, sc),
            avg_authority=fa["authority_sum"] / max(1, sc),
            has_web=name in web_evidence_skills)
        # 细粒度技能点同样必须通过交叉验证：单一 JD 提及的表述不进入岗位能力集。
        # 单来源恰恰是抄袭/通胀/幻觉最难排除的情形——实测单来源细粒度里大量是 JD 短语
        # 碎片（"顶会论文发表"、"智能客服项目经验"）而非技能点。降级为 candidate 而非
        # 删除：保留观察、可审计，与演化判据④「降级为候选能力项」一致。
        importance = "required" if (fa["required_votes"] >= fa["bonus_votes"]
                                    and employer_count >= validation_threshold) else "bonus"
        capabilities.append({
            "name": name, "importance": importance,
            "weight": round(min(1.0, sc / total + 0.1), 4),
            "level_required": _mode(fa["levels"]) or "familiar",
            "category": skill_category(parent) if parent else "其他", "skill_type": "hard",
            "confidence": conf.compute(factors), "factors": factors,
            "source_count": employer_count, "employer_count": employer_count,
            "jd_support_count": sc, "channel_count": len(fa["channels"]),
            "channels": sorted(fa["channels"]),
            "validation_threshold": validation_threshold,
            "support_ratio": round(sc / total, 4),
            "web_verified": name in web_evidence_skills,
            "status": "active" if (employer_count >= validation_threshold
                                   and (conf.compute(factors) >= CONFIDENCE_THRESHOLD
                                        or name in web_evidence_skills)) else "candidate",
            "granularity": "fine", "parent": parent,
            "evidence": fa["evidence"][:6],
        })

    # 排序：active 在前，按置信度降序
    capabilities.sort(key=lambda c: (c["status"] == "active", c["confidence"]), reverse=True)
    active = [c for c in capabilities if c["status"] == "active"]
    coarse_active = [c for c in active if c.get("granularity") != "fine"]
    stats = {
        "total_jds": len(parsed_jds),
        "valid_jds": len(valid_jds),
        "duplicate_jds": len(parsed_jds) - len(valid_jds),
        "raw_skill_terms": len(agg) + len(fine_agg),
        "fine_skill_terms": len(fine_agg),
        "confirmed_capabilities": len(coarse_active),
        "filtered_low_confidence": len(capabilities) - len(active),
        "unique_employers": len({employer_independence_key({**j, **source_meta.get(j.get("raw_jd_id") or -1, {})})
                                 for j in valid_jds
                                 if employer_independence_key({**j, **source_meta.get(j.get("raw_jd_id") or -1, {})})}),
        "unknown_employer_jds": sum(
            1 for j in valid_jds
            if not employer_independence_key({**j, **source_meta.get(j.get("raw_jd_id") or -1, {})})),
        "employer_validated_capabilities": len(active),
        "candidate_capabilities": len(capabilities) - len(active),
        "avg_confidence": round(sum(c["confidence"] for c in coarse_active) / max(1, len(coarse_active)), 4),
    }
    return {"capabilities": capabilities, "stats": stats}


def _mode(items: list):
    if not items:
        return None
    counts = defaultdict(int)
    for x in items:
        counts[x] += 1
    return max(counts.items(), key=lambda kv: kv[1])[0]


def job_confidence(capabilities: list[dict]) -> float:
    """岗位定义整体置信度 = 粗粒度 active 能力项置信度的**权重加权**平均。

    细粒度技能点天然低频（支持率低），不纳入岗位级均值；按技能权重加权使
    核心必备技能主导岗位置信度，长尾低频加分项不拖累整体（2026-07 校准）。
    """
    active = [c for c in capabilities
              if c["status"] == "active" and c.get("granularity") != "fine"]
    if not active:
        return 0.0
    wsum = sum(max(0.05, c.get("weight", 0.5)) for c in active)
    return round(sum(c["confidence"] * max(0.05, c.get("weight", 0.5)) for c in active) / wsum, 4)
