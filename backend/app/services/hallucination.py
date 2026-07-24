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

# 阈值
MIN_SOURCES_REQUIRED = 2      # 必备技能至少 2 个独立来源
CONFIDENCE_THRESHOLD = 0.45   # 进入图谱的最低置信度
HIGH_CONFIDENCE = 0.75


def aggregate_capabilities(
    parsed_jds: list[dict],
    web_evidence_skills: set[str] | None = None,
    source_meta: dict[int, dict] | None = None,
) -> dict:
    """聚合同一岗位的多条 JD 解析结果，输出交叉验证后的能力项。

    parsed_jds: [{"required_skills":[...], "bonus_skills":[...], "lag_days":int,
                  "is_duplicate":bool, "raw_jd_id":int, "source":str}]
    web_evidence_skills: 通过 Tavily 等外部检索确认存在的技能名集合（增强可信度）。
    source_meta: raw_jd_id -> {"platform": str, "authority": float}，真实采集数据的
                 来源元信息（平台多样性与来源权威度因子）；缺省时回退到 jd["source"]。

    返回 {"capabilities": [...], "stats": {...}}，每个 capability 含
    confidence/factors/source_count/evidence。
    """
    web_evidence_skills = web_evidence_skills or set()
    source_meta = source_meta or {}
    # 只用非重复（去抄袭）来源参与交叉验证
    valid_jds = [j for j in parsed_jds if not j.get("is_duplicate")]
    total = max(1, len(valid_jds))

    # 技能 -> 统计
    agg: dict[str, dict] = defaultdict(lambda: {
        "required_votes": 0, "bonus_votes": 0, "fresh_sum": 0.0,
        "levels": [], "categories": [], "skill_types": [],
        "evidence": [], "raw_names": set(),
        "platforms": set(), "authority_sum": 0.0,
    })

    for jd in valid_jds:
        fw = freshness_weight(jd.get("lag_days", 0))
        meta = source_meta.get(jd.get("raw_jd_id") or -1, {})
        platform = meta.get("platform") or jd.get("source") or "unknown"
        authority = float(meta.get("authority", conf.SOURCE_AUTHORITY["web"]))
        for imp_key, items in (("required", jd.get("required_skills", [])),
                                ("bonus", jd.get("bonus_skills", []))):
            for s in items:
                name = s["name"]
                a = agg[name]
                if imp_key == "required":
                    a["required_votes"] += 1
                else:
                    a["bonus_votes"] += 1
                a["fresh_sum"] += fw
                a["platforms"].add(platform)
                a["authority_sum"] += authority
                a["levels"].append(s.get("level", "familiar"))
                a["categories"].append(s.get("category", "其他"))
                a["skill_types"].append(s.get("skill_type", "hard"))
                a["raw_names"].add(s.get("raw", name))
                if jd.get("raw_jd_id"):
                    a["evidence"].append({
                        "raw_jd_id": jd.get("raw_jd_id"),
                        "source_type": "jd",
                        "source": platform,
                        "snippet": s.get("raw", name),
                    })

    capabilities = []
    for name, a in agg.items():
        source_count = a["required_votes"] + a["bonus_votes"]
        support_ratio = source_count / total
        has_web = name in web_evidence_skills
        # 统一置信度公式（services.confidence）：
        # C = 0.35·支持率 + 0.20·来源多样性 + 0.15·时效性 + 0.20·来源权威度 + 0.10·外部验证
        factors = conf.factors_from_jd(
            support_ratio=support_ratio,
            platforms=a["platforms"],
            avg_freshness=a["fresh_sum"] / max(1, source_count),
            avg_authority=a["authority_sum"] / max(1, source_count),
            has_web=has_web,
        )
        confidence = conf.compute(factors)

        importance = "required" if a["required_votes"] >= a["bonus_votes"] else "bonus"
        # 必备项要求更强的交叉验证；不足则降级为加分或丢弃
        if importance == "required" and source_count < MIN_SOURCES_REQUIRED and not has_web:
            importance = "bonus"

        if confidence < CONFIDENCE_THRESHOLD and not has_web:
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
            "source_count": source_count,
            "support_ratio": round(support_ratio, 4),
            "web_verified": has_web,
            "status": status,
            "evidence": evidence,
        })

    # ---------- 细粒度技能层（两级共存：粗层驱动匹配/评测，细层做具体化展示） ----------
    fine_agg: dict[str, dict] = defaultdict(lambda: {
        "required_votes": 0, "bonus_votes": 0, "fresh_sum": 0.0,
        "levels": [], "parents": [], "evidence": [],
        "platforms": set(), "authority_sum": 0.0,
    })
    for jd in valid_jds:
        fw = freshness_weight(jd.get("lag_days", 0))
        meta = source_meta.get(jd.get("raw_jd_id") or -1, {})
        platform = meta.get("platform") or jd.get("source") or "unknown"
        authority = float(meta.get("authority", conf.SOURCE_AUTHORITY["web"]))
        for s in jd.get("fine_skills", []):
            fa = fine_agg[s["name"]]
            if s.get("importance") == "required":
                fa["required_votes"] += 1
            else:
                fa["bonus_votes"] += 1
            fa["fresh_sum"] += fw
            fa["platforms"].add(platform)
            fa["authority_sum"] += authority
            fa["levels"].append(s.get("level", "familiar"))
            fa["parents"].append(s.get("parent"))
            if jd.get("raw_jd_id"):
                fa["evidence"].append({
                    "raw_jd_id": jd.get("raw_jd_id"), "source_type": "jd",
                    "source": platform, "snippet": s.get("raw", s["name"]),
                })

    for name, fa in fine_agg.items():
        sc = fa["required_votes"] + fa["bonus_votes"]
        parent = _mode([p for p in fa["parents"] if p])
        factors = conf.factors_from_jd(
            support_ratio=sc / total, platforms=fa["platforms"],
            avg_freshness=fa["fresh_sum"] / max(1, sc),
            avg_authority=fa["authority_sum"] / max(1, sc),
            has_web=name in web_evidence_skills)
        # 细粒度天然低频：≥1 个独立来源即可展示（active），但标 required 仍需 ≥2 来源
        importance = "required" if (fa["required_votes"] >= fa["bonus_votes"]
                                    and sc >= MIN_SOURCES_REQUIRED) else "bonus"
        capabilities.append({
            "name": name, "importance": importance,
            "weight": round(min(1.0, sc / total + 0.1), 4),
            "level_required": _mode(fa["levels"]) or "familiar",
            "category": skill_category(parent) if parent else "其他", "skill_type": "hard",
            "confidence": conf.compute(factors), "factors": factors,
            "source_count": sc, "support_ratio": round(sc / total, 4),
            "web_verified": name in web_evidence_skills,
            "status": "active" if sc >= 1 else "candidate",
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
