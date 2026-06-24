"""人岗匹配度诊断、差距分析与学习路径规划。

赛题指标：人岗匹配准确率 ≥ 90%、支持多维度匹配分析。
匹配 = 精确匹配 + 语义匹配(BGE向量) + 级别匹配；输出差距、改进建议、学习路径。
"""
from __future__ import annotations
import re
from .. import clients
from .taxonomy import skill_category

_LEVEL_RANK = {"familiar": 1, "proficient": 2, "expert": 3}
SEMANTIC_THRESHOLD = 0.88   # 语义等价阈值（仅用于中文技能名，英文用词典归一化）
_CJK = re.compile(r"[一-鿿]")


def _has_cjk(s: str) -> bool:
    return bool(_CJK.search(s or ""))


def _level_rank(lvl: str) -> int:
    return _LEVEL_RANK.get(lvl, 1)


def match(job_caps: list[dict], resume_skills: list[str], resume_levels: dict | None = None,
          use_semantic: bool = True) -> dict:
    """核心匹配算法。

    job_caps: [{name, importance, weight, level_required, category, confidence}]
    resume_skills: 候选人技能名列表（已归一化）
    """
    resume_levels = resume_levels or {}
    resume_set = set(resume_skills)
    active_caps = [c for c in job_caps if c.get("status", "active") == "active"]
    required = [c for c in active_caps if c["importance"] == "required"]
    bonus = [c for c in active_caps if c["importance"] == "bonus"]

    # 语义匹配准备：对未精确命中的岗位技能，用向量找最近的简历技能
    sem_map: dict[str, tuple[str, float]] = {}
    if use_semantic and resume_skills:
        unmatched = [c["name"] for c in active_caps if c["name"] not in resume_set]
        if unmatched:
            sem_map = _semantic_align(unmatched, resume_skills)

    matched_skills, missing_required, missing_bonus = [], [], []
    req_score_sum, req_weight_sum = 0.0, 0.0
    level_hits, level_total = 0, 0

    for c in required:
        name, w = c["name"], c.get("weight", 0.5)
        req_weight_sum += w
        hit, via, sim = _resolve_hit(c, resume_set, sem_map)
        if hit:
            # 级别匹配
            req_lvl = _level_rank(c.get("level_required", "familiar"))
            have_lvl = _level_rank(resume_levels.get(via, "proficient"))
            level_total += 1
            level_factor = 1.0 if have_lvl >= req_lvl else 0.65
            if have_lvl >= req_lvl:
                level_hits += 1
            req_score_sum += w * level_factor
            matched_skills.append({"name": name, "importance": "required", "via": via,
                                   "match_type": "exact" if sim >= 0.999 else "semantic",
                                   "similarity": round(sim, 3), "weight": w,
                                   "level_ok": have_lvl >= req_lvl})
        else:
            missing_required.append({"name": name, "weight": w, "category": c.get("category", ""),
                                     "level_required": c.get("level_required", "familiar"),
                                     "confidence": c.get("confidence", 0)})

    bonus_hit = 0
    for c in bonus:
        hit, via, sim = _resolve_hit(c, resume_set, sem_map)
        if hit:
            bonus_hit += 1
            matched_skills.append({"name": c["name"], "importance": "bonus", "via": via,
                                   "match_type": "exact" if sim >= 0.999 else "semantic",
                                   "similarity": round(sim, 3), "weight": c.get("weight", 0.3)})
        else:
            missing_bonus.append({"name": c["name"], "category": c.get("category", "")})

    # 多维度评分
    required_coverage = (req_score_sum / req_weight_sum) if req_weight_sum else 1.0
    bonus_coverage = (bonus_hit / len(bonus)) if bonus else 0.0
    level_match = (level_hits / level_total) if level_total else (1.0 if not required else 0.0)
    extra_relevance = _extra_relevance(resume_set, active_caps)

    overall = round(100 * (0.62 * required_coverage + 0.16 * bonus_coverage
                           + 0.14 * level_match + 0.08 * extra_relevance), 2)
    dimension_scores = {
        "必备技能覆盖率": round(100 * required_coverage, 2),
        "加分技能覆盖率": round(100 * bonus_coverage, 2),
        "技能级别匹配度": round(100 * level_match, 2),
        "领域相关度": round(100 * extra_relevance, 2),
    }
    return {
        "overall_score": overall,
        "level": _grade(overall),
        "dimension_scores": dimension_scores,
        "matched_skills": matched_skills,
        "missing_required": sorted(missing_required, key=lambda x: x["weight"], reverse=True),
        "missing_bonus": missing_bonus,
        "summary": {
            "required_total": len(required), "required_matched": len(required) - len(missing_required),
            "bonus_total": len(bonus), "bonus_matched": bonus_hit,
            "resume_skill_count": len(resume_skills),
        },
    }


def _resolve_hit(cap: dict, resume_set: set, sem_map: dict):
    name = cap["name"]
    if name in resume_set:
        return True, name, 1.0
    if name in sem_map:
        via, sim = sem_map[name]
        if sim >= SEMANTIC_THRESHOLD:
            return True, via, sim
    return False, None, 0.0


def _semantic_align(job_names: list[str], resume_skills: list[str]) -> dict:
    """用 BGE 向量为每个岗位技能找语义最相近的简历技能。

    重要：当前嵌入模型为中文模型，对纯英文/缩写技能名会返回退化向量（不同英文词余弦≈1.0）。
    因此语义匹配仅在「双方均含中文字符」时启用，并加退化守卫；英文同义词交由词典归一化处理。
    """
    out: dict[str, tuple[str, float]] = {}
    cjk_jobs = [n for n in job_names if _has_cjk(n)]
    cjk_res = [s for s in resume_skills if _has_cjk(s)]
    if not cjk_jobs or not cjk_res:
        return out
    try:
        jvecs = clients.embed_batch([f"岗位技能：{n}" for n in cjk_jobs])
        rvecs = clients.embed_batch([f"岗位技能：{s}" for s in cjk_res])
    except Exception:  # noqa: BLE001
        return out
    for jn, jv in zip(cjk_jobs, jvecs):
        best, best_sim = None, 0.0
        for rs, rv in zip(cjk_res, rvecs):
            if jn == rs:
                continue
            sim = clients.cosine(jv, rv)
            # 退化守卫：不同字符串余弦≥0.999 视为退化向量，且非包含关系则忽略
            if sim >= 0.999 and not (jn in rs or rs in jn):
                continue
            if sim > best_sim:
                best, best_sim = rs, sim
        if best:
            out[jn] = (best, best_sim)
    return out


def _extra_relevance(resume_set: set, caps: list[dict]) -> float:
    """简历中与该岗位领域同类的技能占比（衡量领域相关度）。"""
    if not resume_set:
        return 0.0
    cap_cats = {c.get("category") for c in caps}
    rel = sum(1 for s in resume_set if skill_category(s) in cap_cats)
    return min(1.0, rel / max(1, len(resume_set)) * 1.5)


def _grade(score: float) -> str:
    if score >= 85:
        return "高度匹配"
    if score >= 70:
        return "较好匹配"
    if score >= 55:
        return "基本匹配"
    if score >= 40:
        return "存在差距"
    return "差距较大"


def build_learning_path(missing_required: list[dict], skill_relations: dict | None = None) -> list[dict]:
    """基于技能先修关系生成学习路径（拓扑 + 重要度）。

    skill_relations: {skill_name: [prerequisite_names]} 先修依赖。
    """
    skill_relations = skill_relations or {}
    missing_names = [m["name"] for m in missing_required]
    weight_map = {m["name"]: m.get("weight", 0.5) for m in missing_required}
    missing_set = set(missing_names)

    # 计算每个技能在缺失集合内的先修深度
    def depth(name, visiting=None):
        visiting = visiting or set()
        if name in visiting:
            return 0
        pres = [p for p in skill_relations.get(name, []) if p in missing_set]
        if not pres:
            return 0
        return 1 + max(depth(p, visiting | {name}) for p in pres)

    ordered = sorted(missing_names, key=lambda n: (depth(n), -weight_map.get(n, 0)))
    path = []
    for i, name in enumerate(ordered, 1):
        path.append({
            "step": i,
            "skill": name,
            "category": skill_category(name),
            "priority": "高" if weight_map.get(name, 0) >= 0.5 else "中",
            "prerequisites": [p for p in skill_relations.get(name, []) if p in missing_set],
        })
    return path


_SUGGEST_SYS = "你是资深的技术职业规划导师，针对人岗差距给出具体、可执行、循序渐进的提升建议。"


def generate_suggestions(job_name: str, missing_required: list[dict], missing_bonus: list[dict],
                         matched_count: int, overall: float) -> dict:
    """大模型生成针对性改进建议 + 各技能学习资源方向。"""
    miss_req = "、".join(m["name"] for m in missing_required[:10]) or "无"
    miss_bonus = "、".join(m["name"] for m in missing_bonus[:8]) or "无"
    prompt = f"""目标岗位：{job_name}
当前匹配度：{overall}分，已具备{matched_count}项核心能力。
缺失必备技能：{miss_req}
缺失加分技能：{miss_bonus}

请输出JSON：
{{
  "overall_advice": "总体提升建议(2-3句)",
  "skill_advice": [{{"skill":"技能名","action":"如何学习与达标(1句)","resource":"推荐资源方向"}}],
  "timeline": "预计达标周期建议"
}}"""
    try:
        data = clients.chat_json(
            [{"role": "system", "content": _SUGGEST_SYS}, {"role": "user", "content": prompt}],
            temperature=0.4, max_tokens=1200)
        if data.get("overall_advice"):
            return data
    except Exception:  # noqa: BLE001
        pass
    # 兜底
    return {
        "overall_advice": f"建议优先补齐{miss_req}等必备技能，针对性地通过项目实践提升匹配度。",
        "skill_advice": [{"skill": m["name"], "action": f"系统学习{m['name']}并完成实战项目",
                          "resource": "官方文档+实战课程"} for m in missing_required[:8]],
        "timeline": "建议 3-6 个月集中提升",
    }
