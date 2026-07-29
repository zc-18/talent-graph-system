"""真实数据驱动的既有岗位能力演化批处理（v1 历史基线 → v2 现网数据）。

对每个已建图岗位（version=1，非新兴岗位），收集 2025-26 真实采集批次
（tencent/netease）中同簇 RawJD，缓存优先解析后，走与 routers/evolution.update_job
完全相同的服务链：旧能力作为先验合并 → hallucination.aggregate_capabilities →
evolution.compute_changes → evolution.apply_evolution，写入 CapabilityChange
（data_source 标注真实平台、JD 数与批次）。

用法（backend/ 下，务必 $env:DB_NAME='talent_graph_v3'）：
    uv run python -X utf8 data/run_evolution_batch.py [--min-jds 3] [--platforms tencent,netease]
"""
from __future__ import annotations
import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.db import SessionLocal  # noqa: E402
from app import models  # noqa: E402
from app.services import extraction, hallucination, evolution  # noqa: E402
from app.services.cleaning import exact_hash  # noqa: E402

HERE = Path(__file__).parent
CACHE_PATH = HERE / "parsed_cache_real.json"
BATCH_LABEL = "2026W31-r1"      # 默认批次标签，可用 --batch-label 覆盖（多时间切片演化时必须指定）


def load_cluster_map() -> dict[str, str]:
    """规范岗位名 -> 采集簇（title_map 反查）。"""
    m = json.loads((HERE / "collect" / "title_map.json").read_text("utf-8"))["cluster_job_name"]
    return {v: k for k, v in m.items()}


def old_capabilities(db, job) -> list[dict]:
    out = []
    js_rows = db.query(models.JobSkill).filter(models.JobSkill.job_id == job.id,
                                               models.JobSkill.status == "active").all()
    for j in js_rows:
        sk = db.query(models.Skill).get(j.skill_id)
        if sk:
            out.append({"name": sk.name, "importance": j.importance, "weight": j.weight,
                        "level_required": j.level_required, "confidence": j.confidence,
                        "source_count": j.source_count})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--platforms", default="tencent,netease")
    ap.add_argument("--min-jds", type=int, default=3)
    ap.add_argument("--workers", type=int, default=5)
    ap.add_argument("--batch-label", default=BATCH_LABEL,
                    help="写入 CapabilityChange.data_source.batch 的批次标签；"
                         "做多时间切片演化（2018→2024→2026）时每次都要指定对应批次")
    ap.add_argument("--max-version", type=int, default=0,
                    help="跳过版本号已达该值的岗位（0=不跳过）。多时间切片按时间顺序跑时留 0，"
                         "让同一岗位可以 v1→v2→v3 连续演化")
    args = ap.parse_args()
    batch_label = args.batch_label
    platforms = args.platforms.split(",")

    cache = json.loads(CACHE_PATH.read_text("utf-8")) if CACHE_PATH.exists() else {}
    job2cluster = load_cluster_map()
    db = SessionLocal()
    summary = []
    try:
        jobs = db.query(models.Job).filter(models.Job.is_new == False).all()  # noqa: E712
        for job in jobs:
            if args.max_version and (job.version or 1) >= args.max_version:
                print(f"[skip] {job.name} 已是 v{job.version}（≥ --max-version）")
                continue
            cluster = job2cluster.get(job.name)
            if not cluster:
                print(f"[skip] {job.name} 无对应采集簇")
                continue
            rows = db.query(models.RawJD).filter(
                models.RawJD.platform.in_(platforms),
                models.RawJD.cluster_hint == cluster,
                models.RawJD.is_duplicate.isnot(True)).all()
            if len(rows) < args.min_jds:
                print(f"[skip] {job.name} 新数据不足（{len(rows)} 条 < {args.min_jds}）")
                continue

            # 缓存优先解析（与 pipeline 相同的文本哈希键）
            def _parse(row):
                h = exact_hash(row.raw_text or "")
                p = cache.get(h) or extraction.parse_jd(row.raw_text)
                return h, p
            parsed = []
            with ThreadPoolExecutor(max_workers=args.workers) as ex:
                for h, p in ex.map(_parse, rows):
                    cache[h] = p
                    parsed.append(p)
            CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False), "utf-8")

            # === 与 routers/evolution.update_job 相同的服务链 ===
            old_caps = old_capabilities(db, job)
            agg_input = []
            for row, p in zip(rows, parsed):
                agg_input.append({"required_skills": p.get("required_skills", []),
                                  "bonus_skills": p.get("bonus_skills", []),
                                  # 细粒度技能点同样参与（与路由一致）：漏了它，
                                  # 跑过演化的岗位反而比新建的岗位更粗。
                                  "fine_skills": p.get("fine_skills", []),
                                  "lag_days": 0, "is_duplicate": False,
                                  "raw_jd_id": row.id, "source": row.platform})
            for c in old_caps:  # 旧能力作为先验来源之一（与路由一致）
                agg_input.append({
                    "required_skills": [{"name": c["name"], "importance": "required",
                                         "level": c["level_required"], "category": "",
                                         "skill_type": "hard", "raw": c["name"]}]
                    if c["importance"] == "required" else [],
                    "bonus_skills": [{"name": c["name"], "importance": "bonus",
                                      "level": "familiar", "category": "", "skill_type": "hard",
                                      "raw": c["name"]}] if c["importance"] == "bonus" else [],
                    "lag_days": 120, "is_duplicate": False, "raw_jd_id": None,
                    "source": "history"})

            agg = hallucination.aggregate_capabilities(
                agg_input,
                source_meta={r.id: {"platform": r.platform,
                                    "authority": r.source_authority or 1.0}
                             for r in rows})
            # 同名去重（粗粒度优先），避免 fine/coarse 同名导致 job_skill 唯一键冲突
            seen_names, deduped = set(), []
            for c in sorted(agg["capabilities"],
                            key=lambda x: x.get("granularity") == "fine"):
                if c["name"] in seen_names:
                    continue
                seen_names.add(c["name"])
                deduped.append(c)
            agg["capabilities"] = deduped
            changes = evolution.compute_changes(
                old_caps, agg["capabilities"],
                # 先验注入之前、最新窗口 JD 里真实出现过的技能名——淘汰判据的举证基础
                window_skill_names={s.get("name") for p in parsed
                                    for s in (p.get("required_skills", [])
                                              + p.get("bonus_skills", [])) if s.get("name")},
                window_jd_count=len(rows))
            src_platforms = sorted({r.platform for r in rows})
            for ch in changes:  # 标注真实数据来源
                ds = ch.get("data_source") or {}
                ds.update({"platforms": src_platforms, "jd_count": len(rows),
                           "batch": batch_label})
                ch["data_source"] = ds
            result = evolution.apply_evolution(db, job, agg["capabilities"], changes)
            print(f"[evolve] {job.name}: v{result['version']} "
                  f"jd={len(rows)} +{result['added']} -{result['deleted']} ~{result['modified']}")
            summary.append({"job": job.name, "jd_count": len(rows), **result})
    finally:
        db.close()
    print("\n== 演化汇总 ==")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
