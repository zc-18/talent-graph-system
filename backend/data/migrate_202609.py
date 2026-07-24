"""数据库迁移脚本（2026-09 版整改）—— 幂等，可反复执行。

用途：
1. 在目标库（默认读 .env 的 db_name，可用 --db 覆盖，如 talent_graph_v2）创建全部新表
   （crawl_batch / authority_evidence / job_level_skill 由 ORM create_all 自动建）。
2. 对已有表补加新列（MySQL 8 无 ADD COLUMN IF NOT EXISTS，先查 information_schema）。

用法（backend/ 目录下）：
    uv run python data/migrate_202609.py                 # 迁移 .env 指向的库
    uv run python data/migrate_202609.py --db talent_graph_v2   # 建/迁移 v2 新库（不存在则先 CREATE DATABASE）
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, text  # noqa: E402
from app.config import settings  # noqa: E402

# 已有表补列：table -> [(column, DDL 片段)]
ADD_COLUMNS: dict[str, list[tuple[str, str]]] = {
    "raw_jd": [
        ("platform", "VARCHAR(64) NULL"),
        ("salary_range", "VARCHAR(64) NULL"),
        ("experience_req", "VARCHAR(64) NULL"),
        ("education_req", "VARCHAR(64) NULL"),
        ("crawl_batch_id", "INT NULL"),
        ("raw_file_path", "VARCHAR(256) NULL"),
        ("inferred_level", "VARCHAR(16) NULL"),
        ("cluster_hint", "VARCHAR(64) NULL"),
        ("source_authority", "FLOAT NULL DEFAULT 0.6"),
    ],
    "job": [
        ("emergence_type", "VARCHAR(16) NULL"),
        ("first_seen_date", "DATETIME NULL"),
    ],
    "job_skill": [
        ("factors", "JSON NULL"),
    ],
    "evidence": [
        ("source_name", "VARCHAR(128) NULL"),
    ],
}

ADD_INDEXES: list[tuple[str, str, str]] = [  # (table, index_name, column)
    ("raw_jd", "ix_raw_jd_platform", "platform"),
    ("raw_jd", "ix_raw_jd_inferred_level", "inferred_level"),
    ("raw_jd", "ix_raw_jd_crawl_batch_id", "crawl_batch_id"),
]


def server_engine():
    """不带库名的连接（用于 CREATE DATABASE）。"""
    url = (f"mysql+pymysql://{settings.db_user}:{settings.db_password}"
           f"@{settings.db_host}:{settings.db_port}/?charset=utf8mb4")
    return create_engine(url, pool_pre_ping=True)


def db_engine(db_name: str):
    url = (f"mysql+pymysql://{settings.db_user}:{settings.db_password}"
           f"@{settings.db_host}:{settings.db_port}/{db_name}?charset=utf8mb4")
    return create_engine(url, pool_pre_ping=True)


def ensure_database(db_name: str) -> None:
    with server_engine().connect() as conn:
        exists = conn.execute(text(
            "SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME=:n"),
            {"n": db_name}).fetchone()
        if not exists:
            conn.execute(text(
                f"CREATE DATABASE `{db_name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"))
            print(f"[migrate] created database {db_name}")
        else:
            print(f"[migrate] database {db_name} exists")


def column_exists(conn, db_name: str, table: str, column: str) -> bool:
    row = conn.execute(text(
        "SELECT 1 FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA=:d AND TABLE_NAME=:t AND COLUMN_NAME=:c"),
        {"d": db_name, "t": table, "c": column}).fetchone()
    return row is not None


def table_exists(conn, db_name: str, table: str) -> bool:
    row = conn.execute(text(
        "SELECT 1 FROM information_schema.TABLES WHERE TABLE_SCHEMA=:d AND TABLE_NAME=:t"),
        {"d": db_name, "t": table}).fetchone()
    return row is not None


def index_exists(conn, db_name: str, table: str, index: str) -> bool:
    row = conn.execute(text(
        "SELECT 1 FROM information_schema.STATISTICS "
        "WHERE TABLE_SCHEMA=:d AND TABLE_NAME=:t AND INDEX_NAME=:i"),
        {"d": db_name, "t": table, "i": index}).fetchone()
    return row is not None


def migrate(db_name: str) -> None:
    ensure_database(db_name)
    engine = db_engine(db_name)

    # 1) ORM create_all：新表（含 crawl_batch/authority_evidence/job_level_skill）与全新库的全部表
    #    注意：必须在 import models 前绑定这个 engine，因此这里直接用 Base.metadata
    from app import models  # noqa: F401
    from app.db import Base
    Base.metadata.create_all(bind=engine)
    print("[migrate] create_all done (new tables ensured)")

    # 2) 已有表补列（create_all 不会给已存在的表加列）
    with engine.begin() as conn:
        for table, cols in ADD_COLUMNS.items():
            if not table_exists(conn, db_name, table):
                print(f"[migrate] skip {table} (not exists — fresh db, create_all covered it)")
                continue
            for col, ddl in cols:
                if column_exists(conn, db_name, table, col):
                    print(f"[migrate] {table}.{col} exists, skip")
                else:
                    conn.execute(text(f"ALTER TABLE `{table}` ADD COLUMN `{col}` {ddl}"))
                    print(f"[migrate] {table}.{col} added")
        for table, idx, col in ADD_INDEXES:
            if table_exists(conn, db_name, table) and not index_exists(conn, db_name, table, idx):
                conn.execute(text(f"CREATE INDEX `{idx}` ON `{table}` (`{col}`)"))
                print(f"[migrate] index {idx} added")

    print(f"[migrate] done → {db_name}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=settings.db_name,
                    help="目标库名（默认 .env 的 db_name；建 v2 用 --db talent_graph_v2）")
    args = ap.parse_args()
    migrate(args.db)
