"""运行完整 pipeline：从 seed_jds.json 构建岗位能力图谱并写入云 MySQL。

用法：
  uv run python data/run_pipeline.py            # 增量构建
  uv run python data/run_pipeline.py --reset    # 先清空业务表再全量构建
"""
from __future__ import annotations
import json
import os
import sys
import time
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.db import SessionLocal, init_db, engine  # noqa: E402
from app.services import ingest, extraction  # noqa: E402
from app.services.cleaning import exact_hash  # noqa: E402
from sqlalchemy import text  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
RESET_TABLES = ["evidence", "capability_change", "match_result", "job_skill",
                "skill_relation", "resume", "raw_jd", "job", "skill", "tech_trend"]
# --from-db 时 raw_jd 是**数据源**而不是产物，清空它等于把采集结果删掉，必须保留
KEEP_ON_FROM_DB = {"raw_jd"}


def make_parse_fn(use_cache: bool, cache_path: str):
    """use_cache=True 时复用解析缓存，避免重复调用大模型（免费快速重建）。"""
    cache = {}
    if use_cache and os.path.exists(cache_path):
        cache = json.load(open(cache_path, encoding="utf-8"))
        print(f"复用解析缓存 {len(cache)} 条 ← {os.path.basename(cache_path)}")

    def fn(t):
        h = exact_hash(t)
        return cache[h] if h in cache else extraction.parse_jd(t)
    return fn


def reset(keep_raw: bool = False):
    tables = [t for t in RESET_TABLES if not (keep_raw and t in KEEP_ON_FROM_DB)]
    with engine.begin() as conn:
        conn.execute(text("SET FOREIGN_KEY_CHECKS=0"))
        for t in tables:
            conn.execute(text(f"TRUNCATE TABLE {t}"))
        conn.execute(text("SET FOREIGN_KEY_CHECKS=1"))
    kept = "（保留 raw_jd 采集数据）" if keep_raw else ""
    print(f"已清空 {len(tables)} 张业务表{kept}")


def progress(stage, cur, total):
    if cur % 10 == 0 or cur == total:
        print(f"  [{stage}] {cur}/{total}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true")
    ap.add_argument("--workers", type=int, default=5)
    ap.add_argument("--use-cache", action="store_true", help="复用解析缓存，免费快速重建")
    ap.add_argument("--from-db", action="store_true",
                    help="从数据库 RawJD（真实采集数据）构建，替代 seed_jds.json")
    ap.add_argument("--platforms", default="", help="--from-db 时限定平台（逗号分隔）")
    ap.add_argument("--cache-file", default="",
                    help="解析缓存文件名（默认：--from-db 用 parsed_cache_real.json，否则 parsed_cache.json）")
    ap.add_argument("--skip-existing-jobs", action="store_true",
                    help="只补建库里还不存在的岗位（分时间切片建图时，避免全量聚合冲掉已演化岗位的能力项）")
    ap.add_argument("--only-jobs", default="",
                    help="只重建这些岗位（规范岗位名，逗号分隔）。修单个岗位的正道——"
                         "此前只能全量重跑，而全量重跑会冲掉所有已演化岗位的能力项")
    ap.add_argument("--force-rebuild-evolved", action="store_true",
                    help="允许重建已跑过演化的岗位（默认拒绝）。会丢失该岗位的演化结果，除非你确知在做什么")
    ap.add_argument("--refresh-evolved-evidence", action="store_true",
                    help="对因演化历史而跳过重建的岗位，仅向既有能力关系追加精确匹配的JD证据；"
                         "不改能力关系、岗位字段、置信度、版本或审计记录")
    ap.add_argument("--dry-run", action="store_true",
                    help="只打印将重建/将跳过的岗位，不改图谱；配合 --refresh-evolved-evidence "
                         "时会解析并估算证据新增数（清洗字段本身幂等，仍会写回 raw_jd）")
    args = ap.parse_args()

    only_jobs = {s.strip() for s in args.only_jobs.split(",") if s.strip()} or None
    if only_jobs and not args.from_db:
        ap.error("--only-jobs 目前只支持 --from-db（真实语料）路径")
    if args.refresh_evolved_evidence and not args.from_db:
        ap.error("--refresh-evolved-evidence 只支持 --from-db（RawJD 历史语料）路径")
    if args.refresh_evolved_evidence and args.force_rebuild_evolved:
        ap.error("--refresh-evolved-evidence 与 --force-rebuild-evolved 互斥：前者保留关系，后者重建关系")
    if args.refresh_evolved_evidence and args.reset:
        ap.error("--refresh-evolved-evidence 不能与 --reset 同用：清空演化历史后已无安全刷新对象")

    init_db()
    if args.reset:
        reset(keep_raw=args.from_db)

    default_cache = "parsed_cache_real.json" if args.from_db else "parsed_cache.json"
    cache_path = os.path.join(HERE, args.cache_file or default_cache)  # 始终写出合并后的完整缓存
    db = SessionLocal()
    t0 = time.time()
    try:
        if args.from_db:
            from app import models
            q = db.query(models.RawJD)
            if args.platforms:
                q = q.filter(models.RawJD.platform.in_(args.platforms.split(",")))
            rows = q.order_by(models.RawJD.id).all()
            print(f"从数据库加载真实 JD: {len(rows)} 条，开始构建图谱...")
            if only_jobs:
                print(f"仅重建白名单岗位（{len(only_jobs)} 个）：{'、'.join(sorted(only_jobs))}")
            result = ingest.build_graph_from_rows(
                db, rows, parse_fn=make_parse_fn(args.use_cache, cache_path), progress=progress,
                max_workers=args.workers, cache_path=cache_path,
                skip_existing=args.skip_existing_jobs,
                only_jobs=only_jobs, force=args.force_rebuild_evolved,
                dry_run=args.dry_run,
                refresh_evolved_evidence=args.refresh_evolved_evidence)
        else:
            with open(os.path.join(HERE, "seed_jds.json"), encoding="utf-8") as f:
                dataset = json.load(f)
            print(f"加载 JD: {len(dataset)} 条，开始构建图谱...")
            result = ingest.build_graph_from_dataset(
                db, dataset, parse_fn=make_parse_fn(args.use_cache, cache_path), progress=progress,
                max_workers=args.workers, cache_path=cache_path)
    finally:
        db.close()
    dt = time.time() - t0
    if result.get("dry_run"):
        refresh_note = ""
        if args.refresh_evolved_evidence:
            refresh_note = (f"，其中将证据刷新 {result['evolved_jobs_to_refresh']} 个演化岗位，"
                            f"预计新增 {result['estimated_new_evidence']} 条 Evidence")
        print(f"\n[dry-run] 未改动图谱，用时 {dt:.1f}s；"
              f"将重建 {len(result['plan'])} 个岗位，跳过 {len(result['skipped_evolved'])} 个"
              f"{refresh_note}")
        return
    print(f"\n构建完成，用时 {dt:.1f}s")
    print(json.dumps({"jobs_built": result["jobs_built"], "total_jds": result["total_jds"],
                      "duplicates": result["duplicates"],
                      "evolved_jobs_refreshed": result.get("evolved_jobs_refreshed", 0),
                      "new_evidence": result.get("new_evidence", 0)},
                     ensure_ascii=False, indent=2))
    skipped = result.get("skipped_evolved") or []
    if skipped:
        print(f"\n⚠ 已跳过 {len(skipped)} 个跑过演化的岗位（未重建，演化结果保留）：")
        for s in skipped:
            refresh = s.get("evidence_refresh")
            suffix = (f"  仅新增证据={refresh['added_evidence']} "
                      f"年份={refresh['selected_years']}" if refresh else "")
            print(f"  {s['job_name']}  v{s['version']}  {s['changes']} 条变更  "
                  f"{s['active_capabilities']} 项能力{suffix}")
    if args.refresh_evolved_evidence:
        print("\n说明：本模式不会调用 run_confidence_batch.py；该批处理会改 JobSkill/Job "
              "置信度并可能降级关系，不属于证据-only 安全边界。")
    print("\n各岗位置信度：")
    for d in sorted(result["details"], key=lambda x: -x["confidence"]):
        s = d["stats"]
        print(f"  {d['job']:18s} conf={d['confidence']:.3f} "
              f"有效JD={s['valid_jds']} 重复={s['duplicate_jds']} "
              f"确认能力={s['confirmed_capabilities']} 过滤低置信={s['filtered_low_confidence']}")


if __name__ == "__main__":
    main()
