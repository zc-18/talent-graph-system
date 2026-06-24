"""JD 结构化解析（基于 DeepSeek）。

赛题指标：JD 解析准确率 ≥ 90%。
反幻觉策略：JSON 强约束 + 低温度 + "仅抽取文本中明确出现的技能，禁止臆造"。
"""
from __future__ import annotations
from .. import clients
from .taxonomy import normalize_skill, skill_category, skill_type, CATEGORIES

_EXTRACT_SYS = """你是资深的招聘JD结构化解析专家，服务于新一代信息技术（人工智能、大数据、智能系统、物联网）领域的岗位能力图谱构建。
严格要求：
1. 只抽取JD原文中明确出现或强烈隐含的信息，禁止编造原文没有的技能（防止幻觉）。
2. 必备技能(required)：岗位"任职要求/岗位要求"中明确要求、或核心职责必须用到的技能。
3. 加分技能(bonus)：标注"加分项/优先/熟悉者优先/有...经验更佳"的技能。
4. 技能点要细粒度到具体技术（如"PyTorch""向量数据库""模型微调"），而非泛泛的"编程能力"。
5. level 取值: junior(初级)/middle(中级)/senior(高级)/expert(专家)。
6. category 从 [人工智能,大数据,智能系统,物联网,云计算与工程] 中选最贴切的一个。
只输出JSON。"""

_EXTRACT_TPL = """请解析以下招聘JD，输出JSON，字段：
{{
  "job_title": "规范岗位名称",
  "category": "技术栈分类",
  "level": "junior/middle/senior/expert",
  "core_responsibilities": ["职责1","职责2"],
  "required_skills": [{{"name":"技能点","level":"familiar/proficient/expert"}}],
  "bonus_skills": [{{"name":"技能点"}}],
  "typical_scenarios": ["典型行业应用场景1","场景2"]
}}

JD原文：
---
{jd}
---"""


def parse_jd(jd_text: str) -> dict:
    """解析单条 JD，返回结构化字典，并对技能做归一化。"""
    messages = [
        {"role": "system", "content": _EXTRACT_SYS},
        {"role": "user", "content": _EXTRACT_TPL.format(jd=jd_text[:4000])},
    ]
    data = clients.chat_json(messages, temperature=0.1, max_tokens=1500)
    return _postprocess(data)


def _postprocess(data: dict) -> dict:
    cat = data.get("category", "")
    if cat not in CATEGORIES:
        cat = "人工智能"
    req = _norm_skills(data.get("required_skills", []), "required")
    bonus = _norm_skills(data.get("bonus_skills", []), "bonus")
    # 去掉 bonus 中与 required 重复的
    req_names = {s["name"] for s in req}
    bonus = [s for s in bonus if s["name"] not in req_names]
    return {
        "job_title": (data.get("job_title") or "").strip(),
        "category": cat,
        "level": data.get("level", "middle"),
        "core_responsibilities": [r for r in data.get("core_responsibilities", []) if r][:8],
        "required_skills": req,
        "bonus_skills": bonus,
        "typical_scenarios": [s for s in data.get("typical_scenarios", []) if s][:6],
    }


def _norm_skills(items: list, importance: str) -> list[dict]:
    seen, out = set(), []
    for it in items:
        if isinstance(it, str):
            name, lvl = it, "familiar"
        elif isinstance(it, dict):
            name, lvl = it.get("name", ""), it.get("level", "familiar")
        else:
            continue
        norm = normalize_skill(name)
        if not norm or len(norm) > 40 or norm in seen:
            continue
        seen.add(norm)
        out.append({
            "name": norm,
            "raw": name,
            "importance": importance,
            "level": lvl if lvl in ("familiar", "proficient", "expert") else "familiar",
            "category": skill_category(norm),
            "skill_type": skill_type(norm),
        })
    return out


def parse_jd_rule_based(jd_text: str) -> dict:
    """无大模型时的兜底规则解析（关键词匹配），用于降级与离线测试。"""
    from .taxonomy import SYNONYMS
    text = (jd_text or "").lower()
    found = []
    for kw, norm in SYNONYMS.items():
        if kw in text and norm not in [f["name"] for f in found]:
            found.append({"name": norm, "raw": kw, "importance": "required",
                          "level": "familiar", "category": skill_category(norm),
                          "skill_type": skill_type(norm)})
    return {
        "job_title": "", "category": "人工智能", "level": "middle",
        "core_responsibilities": [], "required_skills": found,
        "bonus_skills": [], "typical_scenarios": [],
    }
