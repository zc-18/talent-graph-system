"""每日动态挖掘观测层建表（2026-09）—— 幂等，可反复执行。

只做一件事：在目标库里创建 daily_mining_run / daily_mining_item / daily_skill_delta
三张新表。**不碰任何已有表**——既不补列也不改索引，因为观测层的设计前提就是
「公开图谱一行不动」，迁移脚本本身也必须遵守这一点。

用法（backend/ 目录下）：
    uv run python data/migrate_mining_202609.py                      # 迁移 .env 指向的库
    uv run python data/migrate_mining_202609.py --db talent_graph_v5_shadow
    uv run python data/migrate_mining_202609.py --check              # 只检查，不建表
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, inspect, text  # noqa: E402
from app.config import settings  # noqa: E402
from app.models import DailyMiningRun, DailyMiningItem, DailySkillDelta  # noqa: E402

NEW_TABLES = [DailyMiningRun, DailyMiningItem, DailySkillDelta]
# 迁移前后都要保持字节不变的表：观测层不允许碰公开图谱
UNTOUCHED = ("job", "skill", "job_skill", "evidence", "raw_jd",
             "crawl_batch", "capability_change")


def db_engine(db_name: str):
    if settings.database_url_override:
        return create_engine(settings.database_url_override, pool_pre_ping=True)
    url = (f"mysql+pymysql://{settings.db_user}:{settings.db_password}"
           f"@{settings.db_host}:{settings.db_port}/{db_name}?charset=utf8mb4")
    return create_engine(url, pool_pre_ping=True)


def snapshot_counts(conn, tables) -> dict[str, int]:
    out = {}
    for t in tables:
        try:
            out[t] = conn.execute(text(f"SELECT COUNT(*) FROM `{t}`")).scalar()
        except Exception:
            out[t] = -1        # 表不存在（全新库），不参与对账
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=settings.db_name)
    ap.add_argument("--check", action="store_true", help="只报告表是否存在，不建表")
    args = ap.parse_args()

    engine = db_engine(args.db)
    with engine.connect() as conn:
        before = snapshot_counts(conn, UNTOUCHED)
    existing = set(inspect(engine).get_table_names())

    for model in NEW_TABLES:
        name = model.__tablename__
        print(f"[migrate] {name}: {'已存在' if name in existing else '待创建'}")
    if args.check:
        return 0

    for model in NEW_TABLES:
        model.__table__.create(bind=engine, checkfirst=True)
    after_tables = set(inspect(engine).get_table_names())
    missing = [m.__tablename__ for m in NEW_TABLES if m.__tablename__ not in after_tables]
    if missing:
        print(f"[migrate] 失败：{missing} 未创建", file=sys.stderr)
        return 1

    with engine.connect() as conn:
        after = snapshot_counts(conn, UNTOUCHED)
    drift = {t: (before[t], after[t]) for t in UNTOUCHED if before[t] != after[t]}
    if drift:
        print(f"[migrate] 失败：迁移动了公开图谱 {drift}", file=sys.stderr)
        return 1

    print(f"[migrate] OK — {args.db} 已具备观测层三张表；公开图谱计数未变：")
    for t in UNTOUCHED:
        print(f"           {t}={after[t]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
