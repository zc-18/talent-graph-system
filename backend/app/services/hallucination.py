"""能力"幻觉"防控与交叉验证聚合。

赛题创新点②：能力幻觉防控，提升图谱构建科学性。
核心思想：单条来源（含大模型臆造）不可信；只有被≥k个独立来源交叉验证、
或有外部网络证据支撑的能力项，才以高置信度进入图谱。每个能力项保留可溯源证据。

confidence = sigmoid 加权( 来源支持率, 独立来源数, 新鲜度, 外部证据 )
"""
from __future__ import annotations
import math
from collections import defaultdict
from .cleaning import freshness_weight

# 阈值
MIN_SOURCES_REQUIRED = 2      # 必备技能至少 2 个独立来源
CONFIDENCE_THRESHOLD = 0.45   # 进入图谱的最低置信度
HIGH_CONFIDENCE = 0.75


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def aggregate_capabilities(parsed_jds: list[dict], web_evidence_skills: set[str] | None = None) -> dict:
    """聚合同一岗位的多条 JD 解析结果，输出交叉验证后的能力项。

    parsed_jds: [{"required_skills":[...], "bonus_skills":[...], "lag_days":int,
                  "is_duplicate":bool, "raw_jd_id":int, "source":str}]
    web_evidence_skills: 通过 Tavily 等外部检索确认存在的技能名集合（增强可信度）。

    返回 {"capabilities": [...], "stats": {...}}，每个 capability 含 confidence/source_count/evidence。
    """
    web_evidence_skills = web_evidence_skills or set()
    # 只用非重复（去抄袭）来源参与交叉验证
    valid_jds = [j for j in parsed_jds if not j.get("is_duplicate")]
    total = max(1, len(valid_jds))

    # 技能 -> 统计
    agg: dict[str, dict] = defaultdict(lambda: {
        "required_votes": 0, "bonus_votes": 0, "fresh_sum": 0.0,
        "levels": [], "categories": [], "skill_types": [],
        "evidence": [], "raw_names": set(),
    })

    for jd in valid_jds:
        fw = freshness_weight(jd.get("lag_days", 0))
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
                a["levels"].append(s.get("level", "familiar"))
                a["categories"].append(s.get("category", "其他"))
                a["skill_types"].append(s.get("skill_type", "hard"))
                a["raw_names"].add(s.get("raw", name))
                if jd.get("raw_jd_id"):
                    a["evidence"].append({
                        "raw_jd_id": jd.get("raw_jd_id"),
                        "source_type": "jd",
                        "source": jd.get("source", ""),
                        "snippet": s.get("raw", name),
                    })

    capabilities = []
    for name, a in agg.items():
        source_count = a["required_votes"] + a["bonus_votes"]
        support_ratio = source_count / total
        has_web = name in web_evidence_skills
        # 置信度：来源支持率(主) + 独立来源数 + 新鲜度 + 外部证据加成
        z = (2.4 * support_ratio
             + 0.45 * min(source_count, 5)
             + 0.6 * (a["fresh_sum"] / max(1, source_count))
             + (1.1 if has_web else 0.0)
             - 1.7)
        confidence = round(_sigmoid(z), 4)

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
            "source_count": source_count,
            "support_ratio": round(support_ratio, 4),
            "web_verified": has_web,
            "status": status,
            "evidence": evidence,
        })

    # 排序：active 在前，按置信度降序
    capabilities.sort(key=lambda c: (c["status"] == "active", c["confidence"]), reverse=True)
    active = [c for c in capabilities if c["status"] == "active"]
    stats = {
        "total_jds": len(parsed_jds),
        "valid_jds": len(valid_jds),
        "duplicate_jds": len(parsed_jds) - len(valid_jds),
        "raw_skill_terms": len(agg),
        "confirmed_capabilities": len(active),
        "filtered_low_confidence": len(capabilities) - len(active),
        "avg_confidence": round(sum(c["confidence"] for c in active) / max(1, len(active)), 4),
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
    """岗位定义整体置信度 = active 能力项置信度均值。"""
    active = [c for c in capabilities if c["status"] == "active"]
    if not active:
        return 0.0
    return round(sum(c["confidence"] for c in active) / len(active), 4)
