# -*- coding: utf-8 -*-
"""别名学习效果评测：留出集上的"学之前 vs 学之后"对照（意见⑧）。

**为什么不报 F1**：F1 需要逐条人工标注的 ground truth，30 份真实公开简历没有标注，
硬凑一个"标准答案"等于自己给自己出题。这里报两个不需要标注、可复算的指标：

  ① 表述对齐率：简历里的原始技能表述，有多大比例能对齐到图谱已有技能/规范技能。
     对不齐 = 这项能力在人岗匹配时白抽了。别名学习直接作用于这个指标。
  ② 目标岗位匹配度：留出集每份简历对其目标岗位跑一遍 matching.match 的综合得分。
     表述对齐得更好 → 命中的必备技能更多 → 匹配度更高。

留出集（8 份）**没有参与学习**，所以这是留出评测不是自我确认。
另外跑既有指标的回归（合成简历 F1 / JD 解析 F1 / 匹配率），确认没被带坏。

用法（backend/ 下）：
    $env:DB_NAME='talent_graph_v3'; uv run python -X utf8 data/eval_alias_learning.py
"""
from __future__ import annotations
import json
import os
import re
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

# 关键：先关掉学习结果再导入 taxonomy，拿到"学之前"的干净词典，
# 学之后的效果由本脚本手工叠加，避免两次进程/模块重载带来的不确定性。
os.environ["TALENT_DISABLE_LEARNED_ALIASES"] = "1"

from app.config import settings  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app import models  # noqa: E402
from app.services.taxonomy import SKILL_CATEGORY, SOFT_SKILLS, normalize_skill  # noqa: E402
from app.services import matching  # noqa: E402

LEARNED_FP = BACKEND / "data" / "learned_aliases.json"
OUT_FP = BACKEND / "data" / "eval_alias_result.json"
_RE_PREFIX = re.compile(r"^(熟练掌握|熟练使用|熟练运用|熟练|精通|掌握|了解|熟悉|会用|使用|具备|擅长)\s*")
_RE_SUFFIX = re.compile(r"(相关经验|实战经验|使用经验|开发经验|经验|能力|技术栈)$")


def clean_term(t: str) -> str:
    t = (t or "").strip().strip("·•-—_/\\|")
    return _RE_SUFFIX.sub("", _RE_PREFIX.sub("", t)).strip()


def load_learned() -> tuple[dict, dict]:
    raw = json.loads(LEARNED_FP.read_text("utf-8"))
    tier_a = {k.lower(): v for k, v in (raw.get("aliases") or {}).items()}
    tier_b = {k.lower(): v for k, v in (raw.get("node_aliases") or {}).items()}
    return tier_a, tier_b


def main() -> None:
    print(f"[eval-alias] 目标库 = {settings.db_name}")
    tier_a, tier_b = load_learned()
    db = SessionLocal()
    try:
        node_names = {(s.normalized_name or s.name) for s in db.query(models.Skill).all()}
        # 对齐必须**区分大小写**：下游 matching.match 是按字符串精确相等命中的，
        # 用小写集合判对齐会把 "Numpy" 也算成已对齐，那正是别名要修的问题，
        # 指标反而看不见效果（第一版就栽在这里，全程 +0.00%）。
        known = {n for n in node_names if n} | set(SKILL_CATEGORY) | set(SOFT_SKILLS)

        def before(term: str) -> str:
            return normalize_skill(term)

        def after(term: str) -> str:
            low = clean_term(term).lower()
            if low in tier_a:                 # 模拟 Tier A 并入 SYNONYMS 后的效果
                return tier_a[low]
            nm = normalize_skill(term)
            return tier_b.get(nm.lower(), nm)  # Tier B：对齐到图谱既有节点

        held = db.query(models.TalentProfile).filter(
            models.TalentProfile.holdout.is_(True)).order_by(models.TalentProfile.id).all()
        if not held:
            sys.exit("[eval-alias] 没有留出集，先跑 learn_aliases.py --mark-holdout 8")

        rows, tot_b, tot_a, tot_n = [], 0, 0, 0
        score_b_sum = score_a_sum = 0.0
        scored = 0
        for p in held:
            terms = [clean_term(t) for t in (p.raw_skill_terms or []) if clean_term(t)]
            if not terms:
                continue
            sb = [before(t) for t in terms]
            sa = [after(t) for t in terms]
            hit_b = sum(1 for s in sb if s in known)
            hit_a = sum(1 for s in sa if s in known)
            tot_b, tot_a, tot_n = tot_b + hit_b, tot_a + hit_a, tot_n + len(terms)

            row = {"code": p.code, "language": p.language, "terms": len(terms),
                   "aligned_before": hit_b, "aligned_after": hit_a,
                   "job": None, "score_before": None, "score_after": None}
            if p.matched_job_id:
                job = db.query(models.Job).get(p.matched_job_id)
                caps = []
                for js in db.query(models.JobSkill).filter(
                        models.JobSkill.job_id == p.matched_job_id,
                        models.JobSkill.status == "active").all():
                    sk = db.query(models.Skill).get(js.skill_id)
                    if sk:
                        caps.append({"name": sk.normalized_name or sk.name,
                                     "importance": js.importance, "weight": js.weight,
                                     "level_required": js.level_required,
                                     "category": sk.category, "confidence": js.confidence,
                                     "status": "active"})
                if caps:
                    # 语义匹配关掉：它会引入嵌入服务的抖动，掩盖别名本身的效果
                    mb = matching.match(caps, sorted(set(sb)), {}, use_semantic=False)
                    ma = matching.match(caps, sorted(set(sa)), {}, use_semantic=False)
                    row.update(job=job.name if job else None,
                               score_before=mb["overall_score"], score_after=ma["overall_score"])
                    score_b_sum += mb["overall_score"]
                    score_a_sum += ma["overall_score"]
                    scored += 1
            rows.append(row)

        rate_b = tot_b / tot_n if tot_n else 0
        rate_a = tot_a / tot_n if tot_n else 0
        print(f"\n留出集 {len(rows)} 份，原始技能表述合计 {tot_n} 条")
        print(f"① 表述对齐率  学之前 {rate_b:.2%}（{tot_b}/{tot_n}）"
              f" → 学之后 {rate_a:.2%}（{tot_a}/{tot_n}）"
              f"  提升 {rate_a - rate_b:+.2%}，多对齐 {tot_a - tot_b} 条")
        if scored:
            print(f"② 目标岗位匹配度（{scored} 份有目标岗位）"
                  f" 学之前 {score_b_sum / scored:.2f} → 学之后 {score_a_sum / scored:.2f}"
                  f"  提升 {(score_a_sum - score_b_sum) / scored:+.2f}")
        print("\n逐份明细：")
        for r in rows:
            d = r["aligned_after"] - r["aligned_before"]
            sc = ""
            if r["score_before"] is not None:
                sc = f"  匹配度 {r['score_before']:.2f}→{r['score_after']:.2f}"
            print(f"  {r['code']} [{r['language']}] {r['job'] or '(无目标岗位)':<20} "
                  f"表述{r['terms']:>3} 对齐 {r['aligned_before']:>3}→{r['aligned_after']:>3} ({d:+}){sc}")

        result = {
            "holdout_size": len(rows), "total_terms": tot_n,
            "alignment_rate_before": round(rate_b, 4), "alignment_rate_after": round(rate_a, 4),
            "aligned_before": tot_b, "aligned_after": tot_a,
            "match_score_before": round(score_b_sum / scored, 2) if scored else None,
            "match_score_after": round(score_a_sum / scored, 2) if scored else None,
            "scored_profiles": scored,
            "tier_a_aliases": len(tier_a), "tier_b_aliases": len(tier_b),
            "detail": rows,
        }
        OUT_FP.write_text(json.dumps(result, ensure_ascii=False, indent=2), "utf-8")
        print(f"\n[eval-alias] 结果写入 {OUT_FP}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
