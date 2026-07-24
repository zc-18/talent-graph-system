"""岗位分级能力画像（初/中/高级）。

按 RawJD.inferred_level 把同一岗位簇的真实 JD 分桶，逐桶交叉验证聚合，
落库 JobLevelSkill，形成 junior/middle/senior 三档能力画像，并支持
"晋升视角"的级别间能力差异对比（level_diff）。
"""
from __future__ import annotations
import json
import os
from collections import defaultdict
from pathlib import Path
from sqlalchemy.orm import Session
from .. import models
from . import cleaning, extraction, hallucination, graph_service
from .ingest import title_key
from .evolution import compute_changes

MIN_JDS_PER_BUCKET = 3     # 每档至少 3 条有效 JD 才建画像
MIN_BUCKETS = 2            # 至少 2 档才有对比意义

LEVELS = ("junior", "middle", "senior")
LEVEL_LABELS = {"junior": "初级", "middle": "中级", "senior": "高级"}

_DEFAULT_CACHE = Path(__file__).resolve().parents[2] / "data" / "parsed_cache_real.json"


def _load_cache(cache_path: str) -> dict:
    if cache_path and os.path.exists(cache_path):
        try:
            with open(cache_path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def collect_job_rows(db: Session, job: models.Job) -> list[models.RawJD]:
    """取该岗位簇的非重复真实 JD 行（title_key 归一后等于岗位名）。"""
    rows = db.query(models.RawJD).filter(
        models.RawJD.is_duplicate == False).all()  # noqa: E712
    return [r for r in rows
            if title_key(r.job_title or "", getattr(r, "cluster_hint", None)) == job.name]


def bucket_rows(rows: list) -> dict[str, list]:
    """按 inferred_level 分桶，并应用「≥3条/桶 且 ≥2桶」规则；不满足返回 {}。"""
    buckets: dict[str, list] = defaultdict(list)
    for r in rows:
        lvl = getattr(r, "inferred_level", None)
        if lvl in LEVELS and (r.raw_text or "").strip():
            buckets[lvl].append(r)
    valid = {lvl: rs for lvl, rs in buckets.items() if len(rs) >= MIN_JDS_PER_BUCKET}
    if len(valid) < MIN_BUCKETS:
        return {}
    return valid


def build_level_profiles(db: Session, job: models.Job, rows=None, parse_fn=None,
                         cache_path: str | None = None) -> dict[str, dict]:
    """构建岗位分级能力画像并落库 JobLevelSkill。

    返回 {level: {"jd_count": n, "capabilities": [...]}}；分桶规则不满足时返回 {}。
    """
    parse_fn = parse_fn or extraction.parse_jd
    cache_path = cache_path or str(_DEFAULT_CACHE)
    if rows is None:
        rows = collect_job_rows(db, job)
    buckets = bucket_rows(rows)
    if not buckets:
        return {}

    cache = _load_cache(cache_path)
    cache_dirty = False
    out: dict[str, dict] = {}

    for level, level_rows in buckets.items():
        agg_input, source_meta = [], {}
        for r in level_rows:
            h = cleaning.exact_hash(r.raw_text or "")
            p = cache.get(h)
            if not p:
                p = parse_fn(r.raw_text)
                cache[h] = p
                cache_dirty = True
            agg_input.append({
                "required_skills": p.get("required_skills", []),
                "bonus_skills": p.get("bonus_skills", []),
                "fine_skills": p.get("fine_skills", []),
                "lag_days": r.lag_days or 0, "is_duplicate": False,
                "raw_jd_id": r.id, "source": r.source,
            })
            source_meta[r.id] = {
                "platform": getattr(r, "platform", None) or r.source,
                "authority": getattr(r, "source_authority", None) or 0.6,
            }

        agg = hallucination.aggregate_capabilities(agg_input, source_meta=source_meta)
        # 只取粗粒度 active 能力项（细粒度不进分级画像，保证跨级可比）
        caps = [c for c in agg["capabilities"]
                if c.get("status") == "active" and c.get("granularity") != "fine"]

        # 落库：先清该 job+level 旧行
        db.query(models.JobLevelSkill).filter(
            models.JobLevelSkill.job_id == job.id,
            models.JobLevelSkill.level == level).delete()
        for c in caps:
            sk = graph_service.upsert_skill(db, c["name"], c.get("category"),
                                            c.get("skill_type"))
            db.add(models.JobLevelSkill(
                job_id=job.id, level=level, skill_id=sk.id,
                importance=c["importance"], weight=c.get("weight", 0.5),
                level_required=c.get("level_required", "familiar"),
                confidence=c.get("confidence", 0.0), factors=c.get("factors"),
                source_count=c.get("source_count", 0), jd_count=len(level_rows)))
        db.commit()
        out[level] = {"jd_count": len(level_rows), "capabilities": caps}

    if cache_dirty and cache_path:
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False)
    return out


# ------------------------- 级别差异（晋升视角） -------------------------

def _rows_to_caps(db: Session, job_id: int, level: str) -> list[dict]:
    q = db.query(models.JobLevelSkill, models.Skill).join(
        models.Skill, models.JobLevelSkill.skill_id == models.Skill.id).filter(
        models.JobLevelSkill.job_id == job_id,
        models.JobLevelSkill.level == level).all()
    return [{"name": sk.name, "importance": jls.importance, "weight": jls.weight,
             "level_required": jls.level_required, "confidence": jls.confidence,
             "source_count": jls.source_count} for jls, sk in q]


_LEVEL_REQ_ORDER = {"familiar": 0, "proficient": 1, "expert": 2}


def rewrite_promotion_reasons(changes: list[dict], to_label: str) -> list[dict]:
    """把 compute_changes 的演化语义改写为晋升语义。"""
    for ch in changes:
        skill = ch["skill_name"]
        t = ch["change_type"]
        old_v, new_v = ch.get("old_value") or {}, ch.get("new_value") or {}
        if t == "add":
            ch["reason"] = f"晋升到{to_label}需新增掌握 {skill}"
        elif t == "delete":
            ch["reason"] = f"{skill} 在{to_label}JD中不再单列，视为默认前提"
        elif t == "modify":
            if "weight" in old_v and "weight" in new_v:
                o, n = old_v.get("weight") or 0, new_v.get("weight") or 0
                if n > o:
                    ch["reason"] = f"晋升要求强化 {skill}（权重 {o:.0%}→{n:.0%}）"
                else:
                    ch["reason"] = f"{skill} 要求权重下降（{o:.0%}→{n:.0%}），重心转移"
            elif "importance" in old_v and "importance" in new_v:
                imp = {"required": "必备", "bonus": "加分"}
                o, n = imp.get(old_v["importance"], old_v["importance"]), \
                    imp.get(new_v["importance"], new_v["importance"])
                if new_v["importance"] == "required":
                    ch["reason"] = f"晋升到{to_label}后 {skill} 由{o}项升为{n}项"
                else:
                    ch["reason"] = f"{skill} 在{to_label}中由{o}项降为{n}项"
            elif "level_required" in old_v or "level_required" in new_v:
                o = old_v.get("level_required")
                n = new_v.get("level_required")
                if _LEVEL_REQ_ORDER.get(n, 0) > _LEVEL_REQ_ORDER.get(o, 0):
                    ch["reason"] = f"掌握深度要求提升（{o}→{n}）"
                else:
                    ch["reason"] = f"掌握深度要求调整（{o}→{n}）"
    return changes


def level_diff(db: Session, job_id: int, frm: str, to: str) -> dict:
    """相邻/任意两级能力画像对比，输出晋升所需的能力差异。"""
    old_caps = _rows_to_caps(db, job_id, frm)
    new_caps = _rows_to_caps(db, job_id, to)
    if not old_caps or not new_caps:
        return {"from": frm, "to": to, "from_label": LEVEL_LABELS.get(frm, frm),
                "to_label": LEVEL_LABELS.get(to, to), "changes": []}
    changes = compute_changes(old_caps, new_caps)
    rewrite_promotion_reasons(changes, LEVEL_LABELS.get(to, to))
    return {"from": frm, "to": to, "from_label": LEVEL_LABELS.get(frm, frm),
            "to_label": LEVEL_LABELS.get(to, to), "changes": changes}
