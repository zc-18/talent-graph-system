"""新岗位发现与定义（RAG 接地，反幻觉）。

赛题核心功能①：识别萌芽/兴起中的新岗位并生成定义。
流程：候选发现 → Tavily 检索证据 → 大模型基于证据生成定义 → 交叉验证置信度。
"""
from __future__ import annotations
from datetime import datetime
from .. import clients
from .taxonomy import normalize_skill, clean_skill_name, skill_category, skill_type, CATEGORIES

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
    """多源检索某新兴岗位的网络证据（Tavily+Serper 独立来源），评估其新兴度。"""
    results = clients.multi_source_search(f"{keyword} 岗位 招聘 任职要求 2025 2026", max_results=max_results)
    news = clients.tavily_search(f"{keyword} 新兴职业 趋势", max_results=4, days=180)
    evidence = []
    for r in results:
        evidence.append({"title": r.get("title", ""), "url": r.get("url", ""),
                         "content": (r.get("content") or "")[:600], "provider": r.get("provider", "")})
    for r in news:
        evidence.append({"title": r.get("title", ""), "url": r.get("url", ""),
                         "content": (r.get("content") or "")[:600], "provider": "tavily-news"})
    # 多源交叉验证：独立来源(provider)数量越多，新兴度越可信
    providers = {e.get("provider", "") for e in evidence if e.get("provider")}
    emergence = min(1.0, 0.10 * len(results) + 0.15 * len(news) + 0.1 * len(providers))
    return {"keyword": keyword, "evidence": evidence, "emergence_score": round(emergence, 3),
            "evidence_count": len(evidence), "independent_sources": len(providers)}


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
    # 能力项交叉验证：技能名是否在证据文本中出现 → 提升置信度
    ev_blob = " ".join((e.get("content") or "") + " " + (e.get("title") or "") for e in evidence).lower()

    def make_cap(item, importance):
        name = clean_skill_name(item.get("name", "") if isinstance(item, dict) else item)
        if not name:
            return None
        in_evidence = name.lower() in ev_blob or any(
            w in ev_blob for w in name.lower().split() if len(w) > 2)
        base = 0.55 if importance == "required" else 0.45
        conf = min(0.95, base + (0.25 if in_evidence else 0.0) + min(0.15, ev_count * 0.02))
        return {
            "name": name, "importance": importance,
            "weight": 0.7 if importance == "required" else 0.4,
            "level_required": item.get("level", "familiar") if isinstance(item, dict) else "familiar",
            "category": skill_category(name), "skill_type": skill_type(name),
            "confidence": round(conf, 4), "source_count": max(1, ev_count // 2 + (1 if in_evidence else 0)),
            "support_ratio": round(0.5 + (0.3 if in_evidence else 0), 3),
            "web_verified": in_evidence, "status": "active" if conf >= 0.45 else "candidate",
            "evidence": ([{"source_type": "web", "snippet": f"证据支撑: {name}"}] if in_evidence else []),
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

    return {
        "job_title": (data.get("job_title") or keyword).strip(),
        "category": cat, "level": data.get("level", "middle"),
        "summary": data.get("summary", ""),
        "core_responsibilities": [r for r in data.get("core_responsibilities", []) if r][:8],
        "typical_scenarios": [s for s in data.get("typical_scenarios", []) if s][:6],
        "capabilities": caps,
        "emergence_score": min(1.0, 0.3 + ev_count * 0.05),
        "source_summary": {"evidence_count": ev_count,
                           "sources": [e.get("url", "") for e in evidence[:6] if e.get("url")],
                           "generated_at": datetime.utcnow().isoformat()},
    }
