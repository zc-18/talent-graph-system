"""数据库迁移（2026-08 第六轮：个人资料自助维护）—— 幂等，可反复执行。

只给 `app_user` **补两列**：`nickname`（展示昵称）和 `avatar_url`（站内头像相对路径）。

* 不新建表、不删列、不改任何既有列的类型或默认值；
* 两列都 NULL-able，老数据不需要回填（前端展示时用 username 兜底）；
* 与知识图谱完全无关：job / skill / job_skill / raw_jd / evidence 一行不动，
  脚本结尾会把这五张表的行数打出来自证。

MySQL 8 没有 `ADD COLUMN IF NOT EXISTS`，所以先查 information_schema 再决定要不要加。

用法（backend/ 目录下）：
    uv run python -X utf8 data/migrate_profile_202608.py                      # 迁移 .env 指向的库
    uv run python -X utf8 data/migrate_profile_202608.py --db talent_graph_v3 # 生产库
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, text  # noqa: E402
from app.config import settings  # noqa: E402

TARGET_TABLE = "app_user"
# 已有表补列（create_all 不会给已存在的表加列）
ADD_COLUMNS: dict[str, list[tuple[str, str]]] = {
    "app_user": [
        ("nickname", "VARCHAR(64) NULL"),
        ("avatar_url", "VARCHAR(512) NULL"),
    ],
}
# 既有核心表：只读行数，用来自证这次迁移没碰图谱数据
WITNESS_TABLES = ("job", "skill", "job_skill", "raw_jd", "evidence")


def db_engine(db_name: str):
    url = (f"mysql+pymysql://{settings.db_user}:{settings.db_password}"
           f"@{settings.db_host}:{settings.db_port}/{db_name}?charset=utf8mb4")
    return create_engine(url, pool_pre_ping=True)


def table_exists(conn, db_name: str, table: str) -> bool:
    return conn.execute(text(
        "SELECT 1 FROM information_schema.TABLES WHERE TABLE_SCHEMA=:d AND TABLE_NAME=:t"),
        {"d": db_name, "t": table}).fetchone() is not None


def column_exists(conn, db_name: str, table: str, column: str) -> bool:
    return conn.execute(text(
        "SELECT 1 FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA=:d AND TABLE_NAME=:t AND COLUMN_NAME=:c"),
        {"d": db_name, "t": table, "c": column}).fetchone() is not None


def migrate(db_name: str) -> None:
    engine = db_engine(db_name)

    with engine.connect() as conn:
        if not table_exists(conn, db_name, TARGET_TABLE):
            sys.exit(f"[migrate] FAILED：{db_name} 缺少 {TARGET_TABLE} 表，"
                     f"请先执行 data/migrate_talent_202608.py / migrate_202609.py")
        before = {f"{table}.{col}": column_exists(conn, db_name, table, col)
                  for table, cols in ADD_COLUMNS.items() for col, _ in cols}
        witness_before = {t: conn.execute(text(f"SELECT COUNT(*) FROM `{t}`")).scalar()
                          for t in WITNESS_TABLES if table_exists(conn, db_name, t)}
    for key, ok in before.items():
        print(f"[migrate] {key:<22} {'exists' if ok else 'MISSING → will add'}")

    with engine.begin() as conn:
        for table, cols in ADD_COLUMNS.items():
            for col, ddl in cols:
                if column_exists(conn, db_name, table, col):
                    print(f"[migrate] {table}.{col} exists, skip")
                else:
                    conn.execute(text(f"ALTER TABLE `{table}` ADD COLUMN `{col}` {ddl}"))
                    print(f"[migrate] {table}.{col} added")

    with engine.connect() as conn:
        missing = [f"{table}.{col}" for table, cols in ADD_COLUMNS.items() for col, _ in cols
                   if not column_exists(conn, db_name, table, col)]
        if missing:
            sys.exit(f"[migrate] FAILED，仍缺列: {missing}")
        witness_after = {t: conn.execute(text(f"SELECT COUNT(*) FROM `{t}`")).scalar()
                         for t in WITNESS_TABLES if table_exists(conn, db_name, t)}
        user_count = conn.execute(text(f"SELECT COUNT(*) FROM `{TARGET_TABLE}`")).scalar()

    drifted = {t: (witness_before.get(t), witness_after.get(t))
               for t in witness_after if witness_before.get(t) != witness_after.get(t)}
    if drifted:
        sys.exit(f"[migrate] FAILED：既有表行数发生变化，请立即核查 {drifted}")

    added = [key for key, ok in before.items() if not ok]
    print(f"[migrate] added: {added or '（无，已是最新）'}")
    print(f"[migrate] app_user 行数（不变）: {user_count}")
    print(f"[migrate] 既有表行数（应与迁移前一致）: {witness_after}")
    print(f"[migrate] done → {db_name}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=settings.db_name, help="目标库名（生产库用 talent_graph_v3）")
    args = ap.parse_args()
    migrate(args.db)
