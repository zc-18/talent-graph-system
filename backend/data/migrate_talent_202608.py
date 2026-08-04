"""数据库迁移（2026-08 整改，老师意见⑧ 人才侧图层）—— 幂等，可反复执行。

新建 5 张表：resume_batch / talent_profile / team / team_member / skill_alias
（全部由 ORM create_all 建；**不改动任何既有表**，所以岗位/技能/JD/证据/置信度
 等既有对外口径一个数字都不会变）。

用法（backend/ 目录下）：
    uv run python -X utf8 data/migrate_talent_202608.py                    # 迁移 .env 指向的库
    uv run python -X utf8 data/migrate_talent_202608.py --db talent_graph_v3   # 生产库
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, text  # noqa: E402
from app.config import settings  # noqa: E402

NEW_TABLES = ["resume_batch", "talent_profile", "team", "team_member", "skill_alias"]
# 已有表补列（create_all 不给已存在的表加列）
ADD_COLUMNS: dict[str, list[tuple[str, str]]] = {
    "talent_profile": [("text_hash", "VARCHAR(40) NULL")],
}
ADD_INDEXES: list[tuple[str, str, str]] = [
    ("talent_profile", "ix_talent_profile_text_hash", "text_hash"),
]


def column_exists(conn, db_name: str, table: str, column: str) -> bool:
    return conn.execute(text(
        "SELECT 1 FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA=:d AND TABLE_NAME=:t AND COLUMN_NAME=:c"),
        {"d": db_name, "t": table, "c": column}).fetchone() is not None


def index_exists(conn, db_name: str, table: str, index: str) -> bool:
    return conn.execute(text(
        "SELECT 1 FROM information_schema.STATISTICS "
        "WHERE TABLE_SCHEMA=:d AND TABLE_NAME=:t AND INDEX_NAME=:i"),
        {"d": db_name, "t": table, "i": index}).fetchone() is not None


def db_engine(db_name: str):
    url = (f"mysql+pymysql://{settings.db_user}:{settings.db_password}"
           f"@{settings.db_host}:{settings.db_port}/{db_name}?charset=utf8mb4")
    return create_engine(url, pool_pre_ping=True)


def table_exists(conn, db_name: str, table: str) -> bool:
    return conn.execute(text(
        "SELECT 1 FROM information_schema.TABLES WHERE TABLE_SCHEMA=:d AND TABLE_NAME=:t"),
        {"d": db_name, "t": table}).fetchone() is not None


def migrate(db_name: str) -> None:
    engine = db_engine(db_name)

    with engine.connect() as conn:
        before = {t: table_exists(conn, db_name, t) for t in NEW_TABLES}
    for t, ok in before.items():
        print(f"[migrate] {t:<16} {'exists' if ok else 'MISSING → will create'}")

    from app import models  # noqa: F401
    from app.db import Base
    Base.metadata.create_all(bind=engine)

    with engine.begin() as conn:
        for table, cols in ADD_COLUMNS.items():
            if not table_exists(conn, db_name, table):
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

    with engine.connect() as conn:
        after = {t: table_exists(conn, db_name, t) for t in NEW_TABLES}
        missing = [t for t, ok in after.items() if not ok]
        if missing:
            sys.exit(f"[migrate] FAILED，仍缺表: {missing}")
        # 顺带确认既有核心表没被动过（create_all 不会改已存在的表，这里只是留个断言）
        counts = {}
        for t in ("job", "skill", "job_skill", "raw_jd", "evidence"):
            if table_exists(conn, db_name, t):
                counts[t] = conn.execute(text(f"SELECT COUNT(*) FROM `{t}`")).scalar()

    created = [t for t in NEW_TABLES if not before[t]]
    print(f"[migrate] created: {created or '（无，已是最新）'}")
    print(f"[migrate] 既有表行数（应与迁移前一致）: {counts}")
    print(f"[migrate] done → {db_name}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=settings.db_name, help="目标库名（生产库用 talent_graph_v3）")
    args = ap.parse_args()
    migrate(args.db)
