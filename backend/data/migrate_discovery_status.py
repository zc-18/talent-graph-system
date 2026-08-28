"""DiscoveryRun 后台化迁移（第五轮 Lane C）—— 幂等，可反复执行。

给 `discovery_run` 补两列，让新岗位发现任务能从「同步阻塞 200 秒」改成
「立即返回 run_id + 后台执行 + 轮询」：

* `status`  VARCHAR(24) NOT NULL DEFAULT 'completed'  —— 状态机 queued/running/completed/failed
* `error`   TEXT NULL                                 —— 失败原因（对齐 evolution_run.error）

**默认值为什么是 completed 而不是 queued**：这张表里已经存在的行，都是老的同步实现
跑完之后落的库，它们的真实状态就是「已完成」。DDL 默认值同时也是这一列的存量回填值，
用 queued 会把全部历史行和展示种子（data/seed_showcase_records.py 写的 5 行）一夜之间
变成「排队中」，前端列表里永远转圈。后台路径在 INSERT 时显式写 'queued'，
ORM 侧 `models.DiscoveryRun.status` 的 default 也取 'completed'，两边口径一致。

幂等性：列/索引都先用 SQLAlchemy inspector 查存在性再决定是否 ALTER，重复执行只打印
skip。dialect 无关（inspector 而不是 information_schema），所以同一个脚本既能对云端
MySQL 跑，也能对 data/seed_local_demo.py 造的 SQLite 影子库跑来验证幂等。

用法（在 backend/ 目录下）：
    uv run python data/migrate_discovery_status.py --db talent_graph_v3   # 生产库
    uv run python data/migrate_discovery_status.py --db talent_graph_v3 --dry-run  # 只看计划
    uv run python data/migrate_discovery_status.py --url sqlite:///data/demo_local.db  # 影子库自测
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, inspect, text  # noqa: E402
from app.config import settings  # noqa: E402

TABLE = "discovery_run"

# (column, MySQL DDL, SQLite DDL) —— SQLite 没有 VARCHAR 长度校验但语法兼容，
# 唯一要小心的是 ADD COLUMN NOT NULL 必须带非空默认值，两个方言都满足。
ADD_COLUMNS: list[tuple[str, str]] = [
    ("status", "VARCHAR(24) NOT NULL DEFAULT 'completed'"),
    ("error", "TEXT NULL"),
]
ADD_INDEXES: list[tuple[str, str]] = [
    ("ix_discovery_run_status", "status"),
]


def build_engine(db_name: str | None, url: str | None):
    if url:
        return create_engine(url)
    target = db_name or settings.db_name
    return create_engine(
        f"mysql+pymysql://{settings.db_user}:{settings.db_password}"
        f"@{settings.db_host}:{settings.db_port}/{target}?charset=utf8mb4",
        pool_pre_ping=True)


def migrate(engine, *, dry_run: bool = False) -> None:
    inspector = inspect(engine)
    if TABLE not in inspector.get_table_names():
        raise SystemExit(
            f"[migrate] 表 {TABLE} 不存在 —— 先跑 data/migrate_202609.py 建表，本脚本只补列")

    existing_columns = {col["name"] for col in inspector.get_columns(TABLE)}
    existing_indexes = {idx["name"] for idx in inspector.get_indexes(TABLE)}
    # SQLite 的 NULL 索引名与 MySQL 的主键名都可能混进来，过滤掉 None 免得比对出错。
    existing_indexes.discard(None)

    planned: list[str] = []
    for column, ddl in ADD_COLUMNS:
        if column in existing_columns:
            print(f"[migrate] {TABLE}.{column} exists, skip")
            continue
        planned.append(f"ALTER TABLE {TABLE} ADD COLUMN {column} {ddl}")
    for index, column in ADD_INDEXES:
        if index in existing_indexes:
            print(f"[migrate] index {index} exists, skip")
            continue
        if column not in existing_columns and not any(
                column in stmt for stmt in planned):
            continue
        planned.append(f"CREATE INDEX {index} ON {TABLE} ({column})")

    if not planned:
        print("[migrate] nothing to do — schema already migrated")
        return
    if dry_run:
        for statement in planned:
            print(f"[migrate] (dry-run) {statement}")
        print(f"[migrate] dry-run: {len(planned)} statement(s) NOT executed")
        return

    with engine.begin() as conn:
        for statement in planned:
            conn.execute(text(statement))
            print(f"[migrate] {statement}")

    verify(engine)


def verify(engine) -> None:
    inspector = inspect(engine)
    columns = {col["name"] for col in inspector.get_columns(TABLE)}
    missing = {name for name, _ in ADD_COLUMNS} - columns
    if missing:
        raise RuntimeError(f"migration incomplete, still missing: {sorted(missing)}")
    with engine.connect() as conn:
        total = conn.execute(text(f"SELECT COUNT(*) FROM {TABLE}")).scalar() or 0
        blank = conn.execute(text(
            f"SELECT COUNT(*) FROM {TABLE} WHERE status IS NULL OR status = ''")).scalar() or 0
    if blank:
        raise RuntimeError(f"{blank}/{total} discovery_run rows have no status after migration")
    print(f"[migrate] verified: {TABLE}.status/.error present, {total} row(s) all carry a status")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None,
                    help="目标库名（默认 .env 的 db_name；生产用 --db talent_graph_v3）")
    ap.add_argument("--url", default=None,
                    help="完整 SQLAlchemy URL，覆盖 --db（影子库自测用 sqlite:///...）")
    ap.add_argument("--dry-run", action="store_true",
                    help="只打印将要执行的 DDL，不写库")
    args = ap.parse_args()
    engine = build_engine(args.db, args.url)
    print(f"[migrate] target = {engine.url.render_as_string(hide_password=True)}")
    migrate(engine, dry_run=args.dry_run)
