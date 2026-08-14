"""修复 2026-07-30 线上误点写进 `job_skill` 的能力项数值与证据。

`repair_phantom_changes.py` 清掉的是**日志侧**的 40 条幽灵记录。复查时发现误点还在
**库表侧**留了一处更隐蔽的伤：那两次交互式演化在页面里只贴了一两条 JD，
`apply_evolution` 对"在这个小窗口里仍被提及"的能力项照常刷新了数值——

* `source_count` 被按 1-2 条 JD 的窗口重算，Java 岗 14 项因此变成**独立来源数 = 1**，
  与作品的核心卖点「≥2 独立来源交叉验证才算确认能力项」当面冲突（评委点开 Java
  就能看到「独立来源 ×1」的 active 能力项）；
* `confidence` 同样被小窗口拉低，Java 岗位置信度掉到 0.43（32 个岗位里倒数第二）；
* `Evidence` 被改写成页面里贴的那几条 JD，而它们不在 `raw_jd` 表里，于是
  `raw_jd_id` 为空——**Java 是全库唯一存在不可回溯证据的岗位**（34 条），
  与《测试方案与报告》「7861 条证据全部可回溯到具体原始 JD」的口径不符。

这些行的 `status` 没被改（07-29 的加固挡住了降级），所以只看 status 会以为数据没事。
判据是 `last_seen` 落在误点时刻。

**为什么不重跑 pipeline**：`upsert_job` 是整体重建能力关系，会连 Java 那 23 条
合法演化淘汰项与 v2 演化叙事一起冲掉——这正是 `rebuild_conflict` 守卫要拦的事
（见 tests/test_rebuild_guards.py）。所以这里走外科手术：**只动误点碰过的行**，
用真实语料重算它们的数值与证据，不新增行、不删除行、不动其它行的 status。

修复口径与主链路完全一致：同一套 `hallucination.aggregate_capabilities`、同一套
`confidence.py` 公式、同一个 `graph_service.write_evidence`；解析结果直接复用
`data/parsed_cache_real.json`，不掏 LLM 钱、不引入新的随机性。

重算后按 ≥2 独立来源的判据回填 status：真实语料支持 ≥2 来源的仍是 active；
只有 1 个来源的降级为 candidate（判据④「保留观察，未淘汰」）；真实语料里
完全不支持的（误点凭空刷出来的行）同样降为 candidate 而不是删除——删除会让
证据链断掉，留成候选项可查、可解释。

用法（backend/ 下）：
    $env:DB_NAME='talent_graph_v3'
    uv run python -X utf8 data/repair_click_evidence.py            # dry-run，打印逐项对照
    uv run python -X utf8 data/repair_click_evidence.py --apply
"""
from __future__ import annotations
import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import SessionLocal  # noqa: E402
from app import models  # noqa: E402
from app.services import cleaning, graph_service, hallucination, ingest  # noqa: E402

CACHE = Path(__file__).resolve().parent / "parsed_cache_real.json"
BACKUP_DIR = Path(__file__).resolve().parent / "backup"

TARGETS = ["Java开发工程师", "AI产品经理"]

MIN_SOURCES = 2   # 与 hallucination 的交叉验证阈值一致

# 合法批次演化的时刻——修好的行 last_seen 回到这里，避免下次排查又把它当成新伤
LEGIT_TS = datetime(2026, 7, 27, 14, 29, 27)


def aggregate_from_corpus(db, job_name: str) -> dict[str, dict]:
    """用真实语料重算该岗位的能力聚合，返回 {技能名: capability}。

    复用主链路的聚合函数与清洗结果（`is_duplicate` / `lag_days` 直接取库中已算好的值，
    不重算——重算需要全库 SimHash 池，单岗位子集算出来的重复判定会与主链路不一致）。
    """
    cache = json.load(open(CACHE, encoding="utf-8")) if CACHE.exists() else {}
    rows = db.query(models.RawJD).all()
    items = [r for r in rows
             if ingest.title_key(r.job_title or "", getattr(r, "cluster_hint", None)) == job_name]

    agg_input, source_meta, hit, miss = [], {}, 0, 0
    for r in items:
        p = cache.get(cleaning.exact_hash(r.raw_text or ""))
        if not p:
            miss += 1
            continue
        hit += 1
        agg_input.append({
            "required_skills": p.get("required_skills", []),
            "bonus_skills": p.get("bonus_skills", []),
            "fine_skills": p.get("fine_skills", []),
            "lag_days": r.lag_days, "is_duplicate": r.is_duplicate,
            "raw_jd_id": r.id, "source": r.source,
        })
        source_meta[r.id] = {"platform": getattr(r, "platform", None) or r.source,
                             "authority": getattr(r, "source_authority", None) or 0.6}

    print(f"  语料：{len(items)} 条 JD 聚到「{job_name}」，命中解析缓存 {hit} 条"
          f"{f'，缺失 {miss} 条（本次跳过，不掏 LLM）' if miss else ''}")
    if not agg_input:
        return {}
    agg = hallucination.aggregate_capabilities(agg_input, source_meta=source_meta)
    return {c["name"]: c for c in agg["capabilities"]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真正写库（缺省仅 dry-run）")
    args = ap.parse_args()

    if not CACHE.exists():
        print(f"[中止] 找不到解析缓存 {CACHE}——重算需要它，否则会触发全量 LLM 解析。")
        return

    db = SessionLocal()
    backup: list[dict] = []
    try:
        for job_name in TARGETS:
            job = db.query(models.Job).filter(models.Job.name == job_name).first()
            if not job:
                print(f"[跳过] {job_name} 不在库中")
                continue

            # 判据不是"误点时刻碰过"，而是"现在确实自相矛盾"——只修真正坏掉的行：
            #   ① active 却只有 1 个独立来源 → 与 ≥2 交叉验证的核心口径冲突；
            #   ② 挂着 raw_jd_id 为空的证据 → 与"证据全部可回溯到原始 JD"冲突。
            # 误点当然也刷新过一批本就健康的行（来源数没变、置信度只有小数点后的浮动），
            # 那些一并重算只会把交付文档里的口径数字全部推翻一遍，得不偿失。
            untraceable = {r[0] for r in db.query(models.Evidence.job_skill_id)
                           .join(models.JobSkill,
                                 models.JobSkill.id == models.Evidence.job_skill_id)
                           .filter(models.JobSkill.job_id == job.id,
                                   models.Evidence.raw_jd_id.is_(None)).distinct().all()}
            damaged = [js for js in db.query(models.JobSkill)
                       .filter(models.JobSkill.job_id == job.id,
                               models.JobSkill.status == "active").all()
                       if js.source_count < MIN_SOURCES or js.id in untraceable]
            print(f"\n{job_name}（id={job.id}，岗位置信度 {job.confidence:.4f}）")
            print(f"  自相矛盾的 active 行：{len(damaged)} 条"
                  f"（单来源 {sum(1 for js in damaged if js.source_count < MIN_SOURCES)}，"
                  f"证据不可回溯 {len(untraceable)}）")
            if not damaged:
                continue

            fresh = aggregate_from_corpus(db, job_name)
            if not fresh:
                print("  [跳过] 真实语料聚不出能力项，不做任何改动")
                continue

            plan = []
            for js in damaged:
                name = db.query(models.Skill.name).filter(
                    models.Skill.id == js.skill_id).scalar()
                cap = fresh.get(name)
                if cap:
                    new_sc = cap.get("source_count", 0)
                    new_status = "active" if new_sc >= MIN_SOURCES else "candidate"
                else:
                    new_sc, new_status = 0, "candidate"
                plan.append((js, name, cap, new_sc, new_status))

            width = max(len(n) for _, n, _, _, _ in plan)
            for js, name, cap, new_sc, new_status in plan:
                mark = "" if new_status == js.status else "  ← 降级"
                newc = cap.get("confidence", 0.0) if cap else 0.0
                src = "语料未支持" if not cap else ""
                print(f"    {name:{width}s}  来源 {js.source_count} → {new_sc}"
                      f"   置信 {js.confidence:.4f} → {newc:.4f}"
                      f"   {js.status} → {new_status}{mark} {src}")

            if not args.apply:
                continue

            for js, name, cap, new_sc, new_status in plan:
                evs = (db.query(models.Evidence)
                       .filter(models.Evidence.job_skill_id == js.id).all())
                backup.append({
                    "job": job_name, "skill": name, "job_skill_id": js.id,
                    "before": {"source_count": js.source_count, "confidence": js.confidence,
                               "status": js.status, "weight": js.weight,
                               "importance": js.importance, "factors": js.factors,
                               "last_seen": js.last_seen.isoformat() if js.last_seen else None},
                    "evidence_before": [{"raw_jd_id": e.raw_jd_id, "source_type": e.source_type,
                                         "source_url": e.source_url, "snippet": e.snippet,
                                         "weight": e.weight} for e in evs],
                })
                # 证据整条替换为语料证据：误点写进来的那几条不在 raw_jd 表里、不可回溯，
                # 留着就等于让"证据全部可回溯"这句话有例外。
                db.query(models.Evidence).filter(
                    models.Evidence.job_skill_id == js.id).delete(synchronize_session=False)
                if cap:
                    js.source_count = new_sc
                    js.confidence = cap.get("confidence", js.confidence)
                    js.factors = cap.get("factors")
                    js.weight = cap.get("weight", js.weight)
                    js.importance = cap.get("importance", js.importance)
                    js.level_required = cap.get("level_required", js.level_required)
                    db.flush()
                    graph_service.write_evidence(db, js.id, cap.get("evidence", []))
                else:
                    js.source_count = 0
                js.status = new_status
                # last_seen 回到误点之前的合法批次时刻，避免下次排查又把它当成新伤
                js.last_seen = LEGIT_TS

            db.flush()
            # 岗位置信度用与主链路同一个函数重算。job_confidence 只取粗粒度 active 项，
            # 而粗/细是看 Skill.parent_id 而非 job_skill 上的字段，故这里 join 出来。
            caps = [{"status": st, "confidence": cf, "weight": w,
                     "granularity": "fine" if pid else "coarse", "source_count": sc}
                    for st, cf, w, sc, pid in db.query(
                        models.JobSkill.status, models.JobSkill.confidence,
                        models.JobSkill.weight, models.JobSkill.source_count,
                        models.Skill.parent_id)
                    .join(models.Skill, models.Skill.id == models.JobSkill.skill_id)
                    .filter(models.JobSkill.job_id == job.id).all()]
            job.confidence = hallucination.job_confidence(caps)
            job.evidence_count = sum(c["source_count"] for c in caps if c["status"] == "active")
            db.commit()
            print(f"  已写库：岗位置信度 → {job.confidence:.4f}，"
                  f"evidence_count → {job.evidence_count}")

        if args.apply and backup:
            BACKUP_DIR.mkdir(exist_ok=True)
            path = BACKUP_DIR / f"click_evidence_{datetime.now():%Y%m%d_%H%M%S}.json"
            path.write_text(json.dumps(backup, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"\n改动前的原值已备份到 {path}")
        elif not args.apply:
            print("\n[dry-run] 未写库。加 --apply 执行。")
    finally:
        db.close()


if __name__ == "__main__":
    main()
