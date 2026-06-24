"""准确率评测harness：JD解析、简历提取、人岗匹配三项核心指标。

赛题要求：JD解析≥90%、简历提取≥90%、匹配≥90%。
用法：
  uv run python data/evaluate.py jd [--sample N]
  uv run python data/evaluate.py resume
  uv run python data/evaluate.py match
"""
from __future__ import annotations
import json
import os
import sys
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.services import extraction
from app.services.cleaning import exact_hash
from app.services.taxonomy import normalize_skill, SOFT_SKILLS

HERE = os.path.dirname(os.path.abspath(__file__))

_PARSE_CACHE = None


def _cached_parse(text: str):
    """优先使用 pipeline 落盘的解析缓存，避免重复调用大模型。"""
    global _PARSE_CACHE
    if _PARSE_CACHE is None:
        path = os.path.join(HERE, "parsed_cache.json")
        _PARSE_CACHE = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else {}
    h = exact_hash(text)
    if h in _PARSE_CACHE:
        return _PARSE_CACHE[h]
    return extraction.parse_jd(text)


def _load(name):
    with open(os.path.join(HERE, name), encoding="utf-8") as f:
        return json.load(f)


def _norm_set(items):
    return {normalize_skill(x) for x in items if normalize_skill(x)}


def eval_jd(sample: int | None = None):
    jds = _load("seed_jds.json")
    gts = {g["id"]: g for g in _load("ground_truth.json")}
    # 仅评测非重复 JD（重复样本无独立真值技能）
    pairs = []
    for i, jd in enumerate(jds, 1):
        g = gts.get(i)
        if not g or g["ground_truth"].get("duplicate"):
            continue
        pairs.append((i, jd, g["ground_truth"]))
    if sample:
        pairs = pairs[:sample]

    tot_p, tot_r, tot_f, tot_imp = 0.0, 0.0, 0.0, 0.0
    n = len(pairs)
    rows = []
    for idx, jd, gt in pairs:
        parsed = _cached_parse(jd["raw_text"])
        ext_req = _norm_set(s["name"] for s in parsed.get("required_skills", []))
        ext_bonus = _norm_set(s["name"] for s in parsed.get("bonus_skills", []))
        ext_all = ext_req | ext_bonus
        # 通胀噪声技能在JD原文中确实作为"必备"出现，解析层应抽到（通胀由清洗层另行标记）
        gt_req = _norm_set(list(gt.get("required", [])) + list(gt.get("inflated_extra", [])))
        gt_bonus = _norm_set(gt.get("bonus", []))
        gt_all = gt_req | gt_bonus

        # 召回：真值技能是否被抽到（required 计入 required 或 all）
        found = gt_all & ext_all
        recall = len(found) / max(1, len(gt_all))
        # 精确：抽取的硬技能中命中真值的比例（软技能不罚分）
        ext_hard = {s for s in ext_all if s not in SOFT_SKILLS}
        precision = len(ext_hard & gt_all) / max(1, len(ext_hard))
        f1 = 2 * precision * recall / max(1e-9, precision + recall)
        # 重要度分类正确率（required vs bonus）
        imp_correct = 0
        for s in gt_req:
            if s in ext_req:
                imp_correct += 1
        for s in gt_bonus:
            if s in ext_bonus or s in ext_req:
                imp_correct += 0.5
        imp_acc = imp_correct / max(1, len(gt_req) + 0.5 * len(gt_bonus))

        tot_p += precision; tot_r += recall; tot_f += f1; tot_imp += imp_acc
        rows.append({"id": idx, "role": jd["job_title"], "precision": round(precision, 3),
                     "recall": round(recall, 3), "f1": round(f1, 3),
                     "missed": sorted(gt_all - ext_all)})

    res = {"metric": "JD解析", "n": n,
           "precision": round(tot_p / n, 4), "recall": round(tot_r / n, 4),
           "f1": round(tot_f / n, 4), "importance_acc": round(tot_imp / n, 4)}
    print(json.dumps(res, ensure_ascii=False, indent=2))
    misses = [r for r in rows if r["recall"] < 0.9]
    if misses:
        print("\n低召回样本:")
        for r in misses[:12]:
            print(f"  #{r['id']} {r['role']} R={r['recall']} 漏:{r['missed']}")
    out = os.path.join(HERE, "eval_jd_result.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"summary": res, "rows": rows}, f, ensure_ascii=False, indent=2)
    print(f"\n明细已保存: {out}")
    return res


def eval_resume():
    """简历技能提取准确率（≥90%）。"""
    from app.services import resume as resume_svc
    resumes = _load("test_resumes.json")
    tot_p, tot_r, tot_f = 0.0, 0.0, 0.0
    rows = []
    for r in resumes:
        parsed = resume_svc.parse_resume(r["raw_text"])
        ext = _norm_set(parsed.get("skills", []))
        gt = _norm_set(r["ground_truth_skills"])
        found = ext & gt
        recall = len(found) / max(1, len(gt))
        # 精确率：抽取的硬技能命中真值比例（软技能不罚分）
        ext_hard = {s for s in ext if s not in SOFT_SKILLS}
        precision = len(ext_hard & gt) / max(1, len(ext_hard))
        f1 = 2 * precision * recall / max(1e-9, precision + recall)
        tot_p += precision; tot_r += recall; tot_f += f1
        rows.append({"id": r["id"], "target": r["target_job"], "precision": round(precision, 3),
                     "recall": round(recall, 3), "missed": sorted(gt - ext)})
    n = len(resumes)
    res = {"metric": "简历提取", "n": n, "precision": round(tot_p / n, 4),
           "recall": round(tot_r / n, 4), "f1": round(tot_f / n, 4)}
    print(json.dumps(res, ensure_ascii=False, indent=2))
    for r in rows:
        if r["recall"] < 0.9:
            print(f"  #{r['id']} {r['target']} R={r['recall']} 漏:{r['missed']}")
    with open(os.path.join(HERE, "eval_resume_result.json"), "w", encoding="utf-8") as f:
        json.dump({"summary": res, "rows": rows}, f, ensure_ascii=False, indent=2)
    return res


def eval_match():
    """人岗匹配准确率（≥90%）：系统对每个必备技能的"具备/缺失"判定 vs 真值。"""
    from app.db import SessionLocal
    from app import models
    from app.services import matching
    resumes = _load("test_resumes.json")
    db = SessionLocal()
    try:
        tot_acc, tot_f1 = 0.0, 0.0
        rows = []
        for r in resumes:
            job = db.query(models.Job).filter(models.Job.name == r["target_job"]).first()
            if not job:
                continue
            js = db.query(models.JobSkill).filter(models.JobSkill.job_id == job.id,
                                                  models.JobSkill.importance == "required",
                                                  models.JobSkill.status == "active").all()
            caps = []
            req_names = set()
            for j in js:
                sk = db.query(models.Skill).get(j.skill_id)
                if sk:
                    caps.append({"name": sk.name, "importance": "required", "weight": j.weight,
                                 "level_required": j.level_required, "category": sk.category,
                                 "confidence": j.confidence, "status": "active"})
                    req_names.add(sk.name)
            gt_skills = _norm_set(r["ground_truth_skills"])
            # 真值：必备技能中候选人确实具备的（= 命中），其余为缺失
            gt_matched = {n for n in req_names if n in gt_skills}
            gt_missing = req_names - gt_matched

            result = matching.match(caps, r["ground_truth_skills"], {}, use_semantic=True)
            sys_matched = {m["name"] for m in result["matched_skills"] if m["importance"] == "required"}
            sys_missing = {m["name"] for m in result["missing_required"]}

            # 逐必备技能分类正确率
            correct = 0
            for n in req_names:
                in_sys = n in sys_matched
                in_gt = n in gt_matched
                if in_sys == in_gt:
                    correct += 1
            acc = correct / max(1, len(req_names))
            # F1 over matched判定
            tp = len(sys_matched & gt_matched)
            fp = len(sys_matched - gt_matched)
            fn = len(gt_matched - sys_matched)
            prec = tp / max(1, tp + fp); rec = tp / max(1, tp + fn)
            f1 = 2 * prec * rec / max(1e-9, prec + rec)
            tot_acc += acc; tot_f1 += f1
            rows.append({"id": r["id"], "target": r["target_job"], "acc": round(acc, 3),
                         "score": result["overall_score"],
                         "wrong": sorted((sys_matched ^ gt_matched))})
        n = len(rows)
        res = {"metric": "人岗匹配", "n": n, "classification_acc": round(tot_acc / n, 4),
               "match_f1": round(tot_f1 / n, 4)}
        print(json.dumps(res, ensure_ascii=False, indent=2))
        for r in rows:
            if r["acc"] < 0.9:
                print(f"  #{r['id']} {r['target']} acc={r['acc']} 误判:{r['wrong']}")
        with open(os.path.join(HERE, "eval_match_result.json"), "w", encoding="utf-8") as f:
            json.dump({"summary": res, "rows": rows}, f, ensure_ascii=False, indent=2)
        return res
    finally:
        db.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("task", choices=["jd", "resume", "match", "all"])
    ap.add_argument("--sample", type=int, default=None)
    args = ap.parse_args()
    if args.task in ("jd", "all"):
        eval_jd(args.sample)
    if args.task in ("resume", "all"):
        eval_resume()
    if args.task in ("match", "all"):
        eval_match()
