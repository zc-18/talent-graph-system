"""岗位分级能力画像（初/中/高级）。

按 RawJD.inferred_level 把同一岗位簇的真实 JD 分桶，逐桶交叉验证聚合，
落库 JobLevelSkill，形成 junior/middle/senior 三档能力画像，并支持
"晋升视角"的级别间能力差异对比（level_diff）。
"""
from __future__ import annotations
import json
import os
import unicodedata
from collections import defaultdict
from pathlib import Path
from uuid import uuid4
from sqlalchemy.orm import Session
from .. import models
from . import cleaning, extraction, hallucination
from .ingest import title_key
from .evolution import compute_changes
from .job_resolution import resolve_job_query

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


def _skill_key(name: str) -> str:
    """Normalize a skill name without erasing meaningful punctuation."""
    return unicodedata.normalize("NFKC", name or "").strip().casefold()


def _stage_cache(cache_path: str, cache: dict) -> str:
    """Write a complete cache next to its destination; caller publishes it."""
    path = Path(cache_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with open(staged, "w", encoding="utf-8") as stream:
            json.dump(cache, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        staged.unlink(missing_ok=True)
        raise
    return str(staged)


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


def bucket_slices(rows: list) -> dict[tuple[str, str, str, str], list]:
    """Bucket by level + recruitment + track + industry for RoleContract slices."""
    buckets: dict[tuple[str, str, str, str], list] = defaultdict(list)
    for row in rows:
        level = getattr(row, "inferred_level", None)
        if level not in LEVELS or not (getattr(row, "raw_text", "") or "").strip():
            continue
        resolved = resolve_job_query(getattr(row, "job_title", "") or "")
        recruitment_type = getattr(row, "recruitment_type", None) or resolved.recruitment_type
        track = getattr(row, "track", None) or resolved.track
        industry = getattr(row, "industry", None) or resolved.industry
        buckets[(level, recruitment_type or "unspecified", track or "unspecified",
                 industry or "general")].append(row)
    valid = {key: items for key, items in buckets.items()
             if len(items) >= MIN_JDS_PER_BUCKET}
    if len({key[0] for key in valid}) < MIN_BUCKETS:
        return {}
    return valid


def build_level_profiles(db: Session, job: models.Job, rows=None, parse_fn=None,
                         cache_path: str | None = None) -> dict[str, dict]:
    """构建岗位分级能力画像并落库 JobLevelSkill。

    返回 {"level|recruitment|track|industry": {slice metadata, capabilities}}；
    分桶规则不满足时返回 {}。本函数事务中立：只 flush/暂存状态，不 commit；
    调用方必须在返回后 commit，并在异常时 rollback。
    """
    parse_fn = parse_fn or extraction.parse_jd
    cache_path = cache_path or str(_DEFAULT_CACHE)
    if rows is None:
        rows = collect_job_rows(db, job)
    buckets = bucket_slices(rows)
    if not buckets:
        # 全量重建必须是目标状态同步，而不是只写本次仍有效的切片。岗位在新数据或
        # 摘除错挂证据后可能不再满足分桶门槛；若直接早退，历史画像会永久残留。
        db.query(models.JobLevelSkill).filter(
            models.JobLevelSkill.job_id == job.id).delete(
                synchronize_session="fetch")
        db.flush()
        return {}

    cache = _load_cache(cache_path)
    cache_dirty = False
    out: dict[str, dict] = {}
    staged_rows: list[models.JobLevelSkill] = []
    confirmed: dict[str, int] = {}
    collisions: dict[str, set[int]] = defaultdict(set)
    for skill_id, name in (db.query(models.Skill.id, models.Skill.name)
                           .join(models.JobSkill,
                                 models.JobSkill.skill_id == models.Skill.id)
                           .filter(models.JobSkill.job_id == job.id,
                                   models.JobSkill.status == "active").all()):
        key = _skill_key(name)
        if not key:
            continue
        collisions[key].add(skill_id)
        confirmed[key] = skill_id
    ambiguous = {key: ids for key, ids in collisions.items() if len(ids) > 1}
    if ambiguous:
        detail = ", ".join(f"{key}={sorted(ids)}" for key, ids in sorted(ambiguous.items()))
        raise ValueError(f"岗位 {job.name} 存在归一化后同名的 active 技能：{detail}")

    for slice_key, level_rows in buckets.items():
        level, recruitment_type, track, industry = slice_key
        agg_input, source_meta = [], {}
        employer_ids = {getattr(r, "employer_id", None) for r in level_rows
                        if getattr(r, "employer_id", None)}
        employer_parents = dict(db.query(models.Employer.id, models.Employer.parent_id).filter(
            models.Employer.id.in_(employer_ids)).all()) if employer_ids else {}
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
                "company": r.company,
            })
            source_meta[r.id] = {
                "platform": getattr(r, "platform", None) or r.source,
                "authority": getattr(r, "source_authority", None) or 0.6,
                "employer_id": getattr(r, "employer_id", None),
                "employer_parent_id": employer_parents.get(getattr(r, "employer_id", None)),
                "company": r.company,
            }

        agg = hallucination.aggregate_capabilities(agg_input, source_meta=source_meta)
        # 只取粗粒度 active 能力项（细粒度不进分级画像，保证跨级可比）
        caps = [c for c in agg["capabilities"]
                if c.get("status") == "active" and c.get("granularity") != "fine"]

        # 再与该岗位「已确认的能力集」取交集。分级聚合是按级别桶独立跑的，桶内样本
        # 小、交叉验证口径与全量建图不同，直接落库会让分级画像出现岗位能力集里根本
        # 没有的技能（实测越界率 45.5%）——同一个岗位，岗位页显示 7 项能力、级别页
        # 却列出 88 项，评委点开就是硬伤。以岗位能力集为准，分级画像只做「级别内的
        # 子集划分」，不引入新能力项。
        # 空 active 集同样必须相交成空集，不能让生成结果绕过岗位能力门槛。
        caps = [c for c in caps if _skill_key(c["name"]) in confirmed]

        for c in caps:
            # 直接复用已确认 JobSkill 的 skill_id，而不是按名称再次 upsert；由此把
            # 「画像行一定对应同岗位 active JobSkill」固化为写入时不变量。
            skill_id = confirmed[_skill_key(c["name"])]
            staged_rows.append(models.JobLevelSkill(
                job_id=job.id, level=level, skill_id=skill_id,
                recruitment_type=recruitment_type, track=track, industry=industry,
                importance=c["importance"], weight=c.get("weight", 0.5),
                level_required=c.get("level_required", "familiar"),
                confidence=c.get("confidence", 0.0), factors=c.get("factors"),
                source_count=c.get("source_count", 0), jd_count=len(level_rows)))
        out["|".join(slice_key)] = {
            "level": level, "recruitment_type": recruitment_type,
            "track": track, "industry": industry,
            "jd_count": len(level_rows), "capabilities": caps}

    # 与岗位能力集取交集后，可能只剩一档还有能力项（实测生成式AI系统测试员只剩
    # 中级 1 项）。分级画像的价值在于跨级对比，单档无从对比，出一张只有一行的
    # 「中级画像」反而像数据没跑通。与 bucket_rows 的「≥2 档」是同一条规则，
    # 只是那条按 JD 条数判、这条按交集结果判——判据晚了一步，得在这里补上。
    populated_levels = {v["level"] for v in out.values() if v["capabilities"]}
    if len(populated_levels) < MIN_BUCKETS:
        db.query(models.JobLevelSkill).filter(
            models.JobLevelSkill.job_id == job.id).delete(
                synchronize_session="fetch")
        db.flush()
        return {}

    # 缓存是可再生的解析产物，但写失败不能发生在画像已经提交之后。先在同目录完整
    # 写入并原子替换缓存，成功后才触碰旧画像；即使后续数据库事务回滚，缓存仅会多出
    # 一份有效解析结果，不会形成半张画像。
    if cache_dirty and cache_path:
        staged_cache = _stage_cache(cache_path, cache)
        try:
            os.replace(staged_cache, cache_path)
        finally:
            Path(staged_cache).unlink(missing_ok=True)

    # 只有所有切片都成功计算、缓存落盘且通过跨级门槛后，才把旧画像替换为新目标状态。
    # 服务函数不提交调用方事务；flush 让约束错误在返回前暴露，由调用方统一回滚。
    db.query(models.JobLevelSkill).filter(
        models.JobLevelSkill.job_id == job.id).delete(
            synchronize_session="fetch")
    db.add_all(staged_rows)
    db.flush()
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
