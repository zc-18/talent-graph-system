"""构建岗位分级（初/中/高级）能力画像。

用法（backend/ 目录）：
    $env:DB_NAME='talent_graph_v2'
    uv run python -X utf8 data/build_levels.py [--jobs "Java开发工程师,大模型算法工程师"]

默认遍历所有岗位；不满足「每档≥3条有效JD 且 ≥2档」规则的岗位自动跳过。
解析结果复用 data/parsed_cache_real.json（缓存优先，新增合并写回）。
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import SessionLocal, init_db  # noqa: E402
from app import models  # noqa: E402
from app.services import leveling  # noqa: E402

CACHE = str(Path(__file__).parent / "parsed_cache_real.json")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", default="", help="逗号分隔岗位名，缺省=全部岗位")
    args = ap.parse_args()
    wanted = {j.strip() for j in args.jobs.split(",") if j.strip()}

    init_db()
    db = SessionLocal()
    try:
        q = db.query(models.Job)
        jobs = [j for j in q.all() if not wanted or j.name in wanted]
        print(f"[build_levels] 岗位数：{len(jobs)}")
        built, skipped = 0, 0
        for job in jobs:
            rows = leveling.collect_job_rows(db, job)
            profiles = leveling.build_level_profiles(db, job, rows=rows, cache_path=CACHE)
            if not profiles:
                skipped += 1
                by_level = {}
                for r in rows:
                    lvl = getattr(r, "inferred_level", None)
                    by_level[lvl] = by_level.get(lvl, 0) + 1
                print(f"  - {job.name}: 跳过（JD分布 {by_level or '无'}，不满足 每档≥"
                      f"{leveling.MIN_JDS_PER_BUCKET}条 且 ≥{leveling.MIN_BUCKETS}档）")
                continue
            built += 1
            desc = ", ".join(f"{leveling.LEVEL_LABELS[lv]}({p['jd_count']}条JD/"
                             f"{len(p['capabilities'])}项能力)"
                             for lv, p in sorted(profiles.items()))
            print(f"  + {job.name}: {desc}")
        print(f"[build_levels] 完成：构建 {built} 个岗位分级画像，跳过 {skipped} 个")
    finally:
        db.close()


if __name__ == "__main__":
    main()
