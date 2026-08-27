"""JD 结构化解析（基于 DeepSeek）。

赛题指标：JD 解析准确率 ≥ 90%。
反幻觉策略：JSON 强约束 + 低温度 + "仅抽取文本中明确出现的技能，禁止臆造"。
"""
from __future__ import annotations
from .. import clients
from .taxonomy import (normalize_skill, skill_category, skill_type, CATEGORIES,
                       normalize_fine_skill, parent_of, SKILL_CATEGORY)

_COARSE_HINT = "、".join(sorted(SKILL_CATEGORY)[:60])

_EXTRACT_SYS = """你是资深的招聘JD结构化解析专家，服务于新一代信息技术（人工智能、大数据、智能系统、物联网）领域的岗位能力图谱构建。
严格要求：
1. 只抽取JD原文中明确出现或强烈隐含的信息，禁止编造原文没有的技能（防止幻觉）。
2. 必备技能(required)：岗位"任职要求/岗位要求"中明确要求、或核心职责必须用到的技能。
3. 加分技能(bonus)：标注"加分项/优先/熟悉者优先/有...经验更佳"的技能。
4. 每个技能输出两层：
   - name：**细粒度技能点**，保留原文具体技术词（如"LoRA微调""vLLM推理部署""Flink CDC""BEV感知"），≤20字，不要泛化；
   - parent：该技能所属的**粗粒度规范技能**，从常见规范名中选（如：{coarse}…）；若 name 本身已是粗粒度规范名，parent 与 name 相同即可。
5. level 取值: junior(初级)/middle(中级)/senior(高级)/expert(专家)。
6. category 从 [人工智能,大数据,智能系统,物联网,云计算与工程] 中选最贴切的一个。
7. 若JD写明经验年限（如"3-5年"），输出 experience_req 原文。
只输出JSON。""".format(coarse=_COARSE_HINT[:400])

_EXTRACT_TPL = """请解析以下招聘JD，输出JSON，字段：
{{
  "job_title": "规范岗位名称",
  "category": "技术栈分类",
  "level": "junior/middle/senior/expert",
  "experience_req": "经验年限原文，没有则空串",
  "core_responsibilities": ["职责1","职责2"],
  "required_skills": [{{"name":"细粒度技能点","parent":"粗粒度规范技能","level":"familiar/proficient/expert"}}],
  "bonus_skills": [{{"name":"细粒度技能点","parent":"粗粒度规范技能"}}],
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
    # 细粒度层：从同一次解析结果生成（不重复调用 LLM）
    fine = _fine_skills(data.get("required_skills", []), "required") + \
        _fine_skills(data.get("bonus_skills", []), "bonus")
    return {
        "job_title": (data.get("job_title") or "").strip(),
        "category": cat,
        "level": data.get("level", "middle"),
        "experience_req": (data.get("experience_req") or "").strip()[:32],
        "core_responsibilities": [r for r in data.get("core_responsibilities", []) if r][:8],
        "required_skills": req,
        "bonus_skills": bonus,
        "fine_skills": fine,
        "typical_scenarios": [s for s in data.get("typical_scenarios", []) if s][:6],
    }


def _norm_skills(items: list, importance: str) -> list[dict]:
    """粗粒度层：优先用 LLM 给的 parent（粗粒度规范名）归一化，兜底用 name。
    输出形状与旧版完全一致（匹配/评测/聚合逻辑不变）。"""
    seen, out = set(), []
    for it in items:
        if isinstance(it, str):
            name, parent, lvl = it, None, "familiar"
        elif isinstance(it, dict):
            name = it.get("name", "")
            parent = it.get("parent") or None
            lvl = it.get("level", "familiar")
        else:
            continue
        norm = normalize_skill(parent) if parent else ""
        if not norm or norm not in SKILL_CATEGORY:
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


def _fine_skills(items: list, importance: str) -> list[dict]:
    """细粒度层：保留原文技术词的具体技能点，挂到粗粒度父技能（两级共存）。
    与粗粒度名相同的条目不重复输出。"""
    seen, out = set(), []
    for it in items:
        if not isinstance(it, dict):
            continue
        fine = normalize_fine_skill(it.get("name", ""))
        if not fine or fine in seen:
            continue
        parent = parent_of(fine, it.get("parent"))
        if not parent or parent == fine:
            continue  # 无父级 → 该技能已按粗粒度层处理，不产生细粒度条目
        if normalize_skill(fine) in SKILL_CATEGORY:
            continue  # 细名是粗粒度规范名的别名（如 LLM→大语言模型、RAG→检索增强生成）→ 归粗层
        seen.add(fine)
        out.append({
            "name": fine,
            "raw": it.get("name", fine),
            "parent": parent,
            "importance": importance,
            "level": it.get("level", "familiar"),
            "category": skill_category(parent),
            "skill_type": "hard",
        })
    return out


_RULE_LEVEL_MARKERS = (
    ("精通", "expert"),
    ("熟练掌握", "proficient"),
    ("熟练使用", "proficient"),
    ("熟悉", "proficient"),
    ("掌握", "proficient"),
    ("了解", "familiar"),
)


def _rule_level(text: str, keyword: str) -> str:
    """Infer an explicitly stated skill level within the current JD clause."""
    position = text.find(keyword.casefold())
    if position < 0:
        return "familiar"
    clause_start = max(text.rfind(delimiter, 0, position)
                       for delimiter in ("。", "；", ";", "！", "？", "\n")) + 1
    prefix = text[clause_start:position]
    matches = [(prefix.rfind(marker), level) for marker, level in _RULE_LEVEL_MARKERS]
    matches = [match for match in matches if match[0] >= 0]
    return max(matches, default=(-1, "familiar"), key=lambda match: match[0])[1]


def parse_jd_rule_based(jd_text: str) -> dict:
    """无大模型时的兜底规则解析（关键词匹配），用于降级与离线测试。"""
    from .taxonomy import SYNONYMS, SKILL_CATEGORY
    text = (jd_text or "").casefold()
    found, seen = [], set()
    # Canonical Chinese names are not duplicated in SYNONYMS. Scan both
    # namespaces so explicit canonical terms and aliases share the same fallback.
    terms = list(SYNONYMS.items()) + [(name, name) for name in SKILL_CATEGORY]
    for kw, norm in terms:
        if kw.casefold() in text and norm not in seen:
            seen.add(norm)
            found.append({"name": norm, "raw": kw, "importance": "required",
                          "level": _rule_level(text, kw), "category": skill_category(norm),
                          "skill_type": skill_type(norm)})
    return {
        "job_title": "", "category": "人工智能", "level": "middle",
        "core_responsibilities": [], "required_skills": found,
        "bonus_skills": [], "typical_scenarios": [],
    }
