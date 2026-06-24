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


def make_parse_fn(use_cache: bool):
    """use_cache=True 时复用 parsed_cache.json，避免重复调用大模型（免费快速重建）。"""
    cache = {}
    path = os.path.join(HERE, "parsed_cache.json")
    if use_cache and os.path.exists(path):
        cache = json.load(open(path, encoding="utf-8"))
        print(f"复用解析缓存 {len(cache)} 条")

    def fn(t):
        h = exact_hash(t)
        return cache[h] if h in cache else extraction.parse_jd(t)
    return fn


def reset():
    with engine.begin() as conn:
        conn.execute(text("SET FOREIGN_KEY_CHECKS=0"))
        for t in RESET_TABLES:
            conn.execute(text(f"TRUNCATE TABLE {t}"))
        conn.execute(text("SET FOREIGN_KEY_CHECKS=1"))
    print(f"已清空 {len(RESET_TABLES)} 张业务表")


def progress(stage, cur, total):
    if cur % 10 == 0 or cur == total:
        print(f"  [{stage}] {cur}/{total}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true")
    ap.add_argument("--workers", type=int, default=5)
    ap.add_argument("--use-cache", action="store_true", help="复用解析缓存，免费快速重建")
    args = ap.parse_args()

    init_db()
    if args.reset:
        reset()

    with open(os.path.join(HERE, "seed_jds.json"), encoding="utf-8") as f:
        dataset = json.load(f)
    print(f"加载 JD: {len(dataset)} 条，开始构建图谱...")

    parse_fn = make_parse_fn(args.use_cache)
    cache_path = os.path.join(HERE, "parsed_cache.json")  # 始终写出合并后的完整缓存
    db = SessionLocal()
    t0 = time.time()
    try:
        result = ingest.build_graph_from_dataset(
            db, dataset, parse_fn=parse_fn, progress=progress, max_workers=args.workers,
            cache_path=cache_path)
    finally:
        db.close()
    dt = time.time() - t0
    print(f"\n构建完成，用时 {dt:.1f}s")
    print(json.dumps({"jobs_built": result["jobs_built"], "total_jds": result["total_jds"],
                      "duplicates": result["duplicates"]}, ensure_ascii=False, indent=2))
    print("\n各岗位置信度：")
    for d in sorted(result["details"], key=lambda x: -x["confidence"]):
        s = d["stats"]
        print(f"  {d['job']:18s} conf={d['confidence']:.3f} "
              f"有效JD={s['valid_jds']} 重复={s['duplicate_jds']} "
              f"确认能力={s['confirmed_capabilities']} 过滤低置信={s['filtered_low_confidence']}")


if __name__ == "__main__":
    main()
