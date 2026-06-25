"""简历解析（PDF / Word / 纯文本）+ 技能要素抽取。

赛题指标：简历技能提取准确率 ≥ 90%。
策略：大模型结构化抽取为主 + 词典规则兜底，技能统一归一化。
"""
from __future__ import annotations
import io
import re
from .. import clients
from .taxonomy import normalize_skill, skill_category, skill_type, SYNONYMS


def extract_text(filename: str, content: bytes) -> str:
    """从上传文件提取纯文本。支持 pdf / docx / txt。"""
    name = (filename or "").lower()
    if name.endswith(".pdf"):
        return _from_pdf(content)
    if name.endswith(".docx"):
        return _from_docx(content)
    if name.endswith(".doc"):
        # 旧版 .doc 无法直接解析，尝试按文本读取
        return content.decode("utf-8", errors="ignore")
    return content.decode("utf-8", errors="ignore")


def _from_pdf(content: bytes) -> str:
    try:
        import pdfplumber
        text = []
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            for page in pdf.pages:
                text.append(page.extract_text() or "")
        return "\n".join(text)
    except Exception:  # noqa: BLE001
        return ""


def _from_docx(content: bytes) -> str:
    try:
        import docx
        doc = docx.Document(io.BytesIO(content))
        parts = [p.text for p in doc.paragraphs]
        for table in doc.tables:
            for row in table.rows:
                parts.append(" ".join(c.text for c in row.cells))
        return "\n".join(parts)
    except Exception:  # noqa: BLE001
        return ""


_RESUME_SYS = """你是简历解析专家。从简历文本中精确抽取候选人信息，只抽取简历中真实出现的内容，不臆造。
技能要细粒度到具体技术点（如 PyTorch、Spark、向量数据库、模型微调）。只输出JSON。"""

_RESUME_TPL = """解析以下简历，输出JSON：
{{
  "candidate_name": "姓名(无则空)",
  "years_experience": 工作年限数字,
  "education": "最高学历",
  "skills": ["技能点1","技能点2"],
  "skill_levels": {{"技能点":"familiar/proficient/expert"}},
  "projects": ["项目简述"],
  "titles": ["曾任岗位"]
}}

简历文本：
---
{resume}
---"""


def parse_resume(text: str) -> dict:
    """结构化解析简历。"""
    if not text or len(text.strip()) < 10:
        return {"candidate_name": "", "years_experience": 0, "skills": [], "skill_levels": {},
                "education": "", "projects": [], "titles": [], "raw_skill_count": 0}
    messages = [
        {"role": "system", "content": _RESUME_SYS},
        {"role": "user", "content": _RESUME_TPL.format(resume=text[:5000])},
    ]
    data = clients.chat_json(messages, temperature=0.1, max_tokens=1200)
    return _postprocess_resume(data, text)


def _postprocess_resume(data: dict, text: str) -> dict:
    raw_skills = data.get("skills", []) or []
    levels_in = data.get("skill_levels", {}) or {}
    norm_levels, norm_skills, seen = {}, [], set()
    for s in raw_skills:
        nm = normalize_skill(s if isinstance(s, str) else s.get("name", ""))
        if not nm or nm in seen:
            continue
        seen.add(nm)
        norm_skills.append(nm)
        lvl = levels_in.get(s) if isinstance(s, str) else None
        norm_levels[nm] = lvl if lvl in ("familiar", "proficient", "expert") else "proficient"
    # 规则兜底：补充词典命中但模型漏抽的技能
    for kw, nm in SYNONYMS.items():
        if nm in seen:
            continue
        if re.search(re.escape(kw), text, re.IGNORECASE):
            seen.add(nm)
            norm_skills.append(nm)
            norm_levels[nm] = "familiar"
    try:
        yrs = float(data.get("years_experience", 0) or 0)
    except (ValueError, TypeError):
        yrs = 0.0
    return {
        "candidate_name": (data.get("candidate_name") or "").strip(),
        "years_experience": yrs,
        "education": data.get("education", ""),
        "skills": norm_skills,
        "skill_levels": norm_levels,
        "skill_categories": {s: skill_category(s) for s in norm_skills},
        "projects": data.get("projects", [])[:10],
        "titles": data.get("titles", [])[:5],
        "raw_skill_count": len(raw_skills),
    }


# 个人信息（PII）字段：依据数据合规与隐私最小化原则，不在服务端持久化
_PII_FIELDS = {"candidate_name", "projects", "titles"}


def redact_for_storage(parsed: dict) -> dict:
    """数据最小化：剔除姓名/项目经历/任职单位等个人身份信息(PII)，
    仅保留用于岗位匹配分析的非身份技能要素，供（可选的）服务端留存。

    原始简历全文与姓名仅在内存中用于本次解析、即时返回给本人，绝不落库。
    """
    return {k: v for k, v in (parsed or {}).items() if k not in _PII_FIELDS}
