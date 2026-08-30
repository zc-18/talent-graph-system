# -*- coding: utf-8 -*-
"""把当前生产数据库完整克隆为一次性发布候选库（R6 发布门禁）。

本脚本只做同一 MySQL 实例内的**数据库级完整克隆**，不在源库执行任何 DDL/DML：

1. 默认 dry-run，列出源库、目标库、45 张基表及精确行数；
2. `--apply` 时要求目标库不存在，先创建目标库，再逐表 `CREATE TABLE LIKE` +
   `INSERT INTO target SELECT * FROM source`；
3. 复制后逐表精确 `COUNT(*)` 对账；任何一张不一致即删除整个目标库并失败；
4. 不覆盖已有数据库，不提供 `--force`，避免误删其他影子/回滚库。

为什么不用 ORM `Base.metadata.create_all`：它只覆盖当前模型声明，无法证明历史表、索引、
默认值与源库完全一致。`CREATE TABLE LIKE` 能复制列、索引和表选项，跨库 INSERT 则保持
每一个主键/时间戳/JSON 值不变。MySQL 的 `CREATE TABLE LIKE` **不会复制外键**，所以本脚本
在所有数据复制完成后从 `information_schema` 逐个重建（支持复合键及 ON UPDATE/DELETE），
最后同时比对行数、列、索引、外键与稳定表选项。`ALTER TABLE ADD CONSTRAINT` 让 InnoDB 把它
接管的支撑索引改名成约束名（源库的同一外键写在 `CREATE TABLE` 里，索引沿用列名），因此建完
外键后按「列形状唯一匹配」把索引名改回源库口径，索引比对才能保持严格相等。视图/触发器/
检查约束若源库存在则应扩展脚本；R6 发布前已断言三者均为 0。这比在源生产库原地执行 repair 安全。

用法（backend/ 目录）：

    # 只看计划，零写入
    uv run python -X utf8 data/clone_database_r6.py \
        --source talent_graph_v3 --target talent_graph_v4

    # 创建一次性候选库；目标已存在则拒绝
    uv run python -X utf8 data/clone_database_r6.py \
        --source talent_graph_v3 --target talent_graph_v4 --apply

发布流程：候选库中执行迁移/repair → 全量测试与 check_state → 服务器 .env 切换 DB_NAME
→ 重启 → 冒烟；失败只需把 DB_NAME 切回源库，源库从未被本脚本修改。
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, text  # noqa: E402
from app.config import settings  # noqa: E402

_NAME = re.compile(r"^[A-Za-z0-9_]+$")


def _name(value: str, label: str) -> str:
    if not _NAME.fullmatch(value or ""):
        raise SystemExit(f"{label} 仅允许字母、数字、下划线：{value!r}")
    return value


def _server_engine():
    url = (f"mysql+pymysql://{settings.db_user}:{settings.db_password}"
           f"@{settings.db_host}:{settings.db_port}/information_schema?charset=utf8mb4")
    return create_engine(url, pool_pre_ping=True)


def _exists(conn, db_name: str) -> bool:
    return conn.execute(text(
        "SELECT COUNT(*) FROM SCHEMATA WHERE SCHEMA_NAME=:name"),
        {"name": db_name}).scalar_one() > 0


def _tables(conn, db_name: str) -> list[str]:
    return [row[0] for row in conn.execute(text(
        "SELECT TABLE_NAME FROM TABLES "
        "WHERE TABLE_SCHEMA=:name AND TABLE_TYPE='BASE TABLE' ORDER BY TABLE_NAME"),
        {"name": db_name}).all()]


def _counts(conn, db_name: str, tables: list[str]) -> dict[str, int]:
    return {table: int(conn.execute(text(
        f"SELECT COUNT(*) FROM `{db_name}`.`{table}`")).scalar_one())
            for table in tables}


def _checksums(conn, db_name: str, tables: list[str]) -> dict[str, int]:
    """Use MySQL's row-content checksum to detect a mixed-time clone."""
    out: dict[str, int | None] = {}
    for table in tables:
        row = conn.execute(text(
            f"CHECKSUM TABLE `{db_name}`.`{table}` EXTENDED")).one()
        if row[1] is None:
            # NULL means the engine refused to checksum the table. Treating that as a value
            # would let two unreadable tables "match" each other and pass the gate.
            raise RuntimeError(f"CHECKSUM TABLE 返回 NULL，无法校验：`{db_name}`.`{table}`")
        out[table] = int(row[1])
    return out


def _index_signature(conn, db_name: str) -> list[tuple]:
    return [tuple(row) for row in conn.execute(text(
        "SELECT TABLE_NAME, INDEX_NAME, NON_UNIQUE, SEQ_IN_INDEX, COLUMN_NAME, "
        "COALESCE(SUB_PART, 0), INDEX_TYPE "
        "FROM STATISTICS WHERE TABLE_SCHEMA=:name "
        "ORDER BY TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX"), {"name": db_name}).all()]


def _foreign_keys(conn, db_name: str) -> list[dict]:
    """Return ordered, composite-key-aware foreign-key definitions."""
    rows = conn.execute(text(
        "SELECT rc.TABLE_NAME, rc.CONSTRAINT_NAME, rc.REFERENCED_TABLE_NAME, "
        "rc.UPDATE_RULE, rc.DELETE_RULE, k.COLUMN_NAME, k.REFERENCED_COLUMN_NAME, "
        "k.ORDINAL_POSITION "
        "FROM REFERENTIAL_CONSTRAINTS rc "
        "JOIN KEY_COLUMN_USAGE k ON k.CONSTRAINT_SCHEMA=rc.CONSTRAINT_SCHEMA "
        " AND k.TABLE_NAME=rc.TABLE_NAME AND k.CONSTRAINT_NAME=rc.CONSTRAINT_NAME "
        "WHERE rc.CONSTRAINT_SCHEMA=:name "
        "ORDER BY rc.TABLE_NAME, rc.CONSTRAINT_NAME, k.ORDINAL_POSITION"),
        {"name": db_name}).all()
    grouped: dict[tuple[str, str], dict] = {}
    for table, constraint, ref_table, update_rule, delete_rule, column, ref_column, _ in rows:
        key = (table, constraint)
        item = grouped.setdefault(key, {
            "table": table, "constraint": constraint, "ref_table": ref_table,
            "update": update_rule, "delete": delete_rule,
            "columns": [], "ref_columns": [],
        })
        item["columns"].append(column)
        item["ref_columns"].append(ref_column)
    return [grouped[key] for key in sorted(grouped)]


def _foreign_key_signature(conn, db_name: str) -> list[tuple]:
    return [(row["table"], tuple(row["columns"]), row["ref_table"],
             tuple(row["ref_columns"]), row["update"], row["delete"])
            for row in _foreign_keys(conn, db_name)]


def _table_options(conn, db_name: str) -> dict[str, tuple]:
    return {row[0]: tuple(row[1:]) for row in conn.execute(text(
        "SELECT TABLE_NAME, ENGINE, ROW_FORMAT, TABLE_COLLATION, CREATE_OPTIONS, "
        "TABLE_COMMENT, AUTO_INCREMENT FROM TABLES "
        "WHERE TABLE_SCHEMA=:name AND TABLE_TYPE='BASE TABLE' ORDER BY TABLE_NAME"),
        {"name": db_name}).all()}


def _column_signature(conn, db_name: str) -> list[tuple]:
    return [tuple(row) for row in conn.execute(text(
        "SELECT TABLE_NAME, ORDINAL_POSITION, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, "
        "COALESCE(COLUMN_DEFAULT, '<NULL>'), EXTRA, COLLATION_NAME "
        "FROM COLUMNS WHERE TABLE_SCHEMA=:name "
        "ORDER BY TABLE_NAME, ORDINAL_POSITION"), {"name": db_name}).all()]


def _index_shapes(conn, db_name: str) -> dict[tuple[str, str], tuple]:
    """Group the index signature by (table, index name), keyed shape-first for renaming."""
    grouped: dict[tuple[str, str], list] = {}
    for table, index, non_unique, seq, column, sub_part, index_type in _index_signature(
            conn, db_name):
        grouped.setdefault((table, index), []).append(
            (seq, column, sub_part, non_unique, index_type))
    return {key: tuple(sorted(value)) for key, value in grouped.items()}


def _restore_index_names(conn, source: str, target: str) -> list[tuple[str, str, str]]:
    """Rename target indexes InnoDB renamed while adopting them for a foreign key.

    Only a rename is ever issued, and only when exactly one unmatched target index on the same
    table has a byte-identical column shape. An ambiguous or shape-changed index is left alone
    so the caller's signature comparison still fails loudly instead of being papered over.
    """
    source_shapes = _index_shapes(conn, source)
    target_shapes = _index_shapes(conn, target)
    renames: list[tuple[str, str, str]] = []
    for (table, source_index), shape in source_shapes.items():
        if (table, source_index) in target_shapes:
            continue
        candidates = [name for (other, name), other_shape in target_shapes.items()
                      if other == table and other_shape == shape
                      and (other, name) not in source_shapes]
        if len(candidates) != 1:
            continue
        renames.append((table, candidates[0], source_index))
    for table, old_name, new_name in renames:
        conn.execute(text(
            f"ALTER TABLE `{target}`.`{table}` RENAME INDEX `{old_name}` TO `{new_name}`"))
    return renames


def _signature_diff(label: str, source_rows, target_rows) -> str:
    """Name the exact rows that differ so a failed clone is diagnosable, not just refused."""
    only_source = [row for row in source_rows if row not in set(target_rows)]
    only_target = [row for row in target_rows if row not in set(source_rows)]
    return (f"{label}不一致：仅源库 {len(only_source)} 行 / 仅目标库 {len(only_target)} 行"
            f"\n  仅源库前 12: {only_source[:12]}"
            f"\n  仅目标前 12: {only_target[:12]}")


def _assert_no_unsupported_objects(conn, db_name: str) -> None:
    """Refuse to clone schema objects this script does not copy.

    Views, triggers and CHECK constraints are not reproduced by CREATE TABLE LIKE, and every
    verification below compares only base tables -- so a source carrying any of them would
    clone into a silently incomplete database that still passes every gate. The docstring
    claims these are zero; assert it instead of trusting it.
    """
    probes = (
        ("视图", "SELECT COUNT(*) FROM VIEWS WHERE TABLE_SCHEMA=:name"),
        ("触发器", "SELECT COUNT(*) FROM TRIGGERS WHERE TRIGGER_SCHEMA=:name"),
        ("CHECK 约束", "SELECT COUNT(*) FROM TABLE_CONSTRAINTS "
                       "WHERE CONSTRAINT_SCHEMA=:name AND CONSTRAINT_TYPE='CHECK'"),
        ("存储过程/函数", "SELECT COUNT(*) FROM ROUTINES WHERE ROUTINE_SCHEMA=:name"),
        ("事件", "SELECT COUNT(*) FROM EVENTS WHERE EVENT_SCHEMA=:name"),
    )
    found = []
    for label, sql in probes:
        try:
            count = conn.execute(text(sql), {"name": db_name}).scalar_one()
        except Exception as exc:                       # information_schema view unavailable
            raise SystemExit(f"无法确认源库是否存在{label}，拒绝克隆：{exc}") from exc
        if count:
            found.append(f"{label}×{count}")
    if found:
        raise SystemExit(
            f"源库 {db_name} 含本脚本不复制的对象（{'、'.join(found)}），"
            "拒绝产出不完整的候选库；请先扩展脚本再发布")


def clone(source: str, target: str, apply: bool) -> dict:
    source = _name(source, "source")
    target = _name(target, "target")
    if source == target:
        raise SystemExit("source 与 target 不能相同")

    engine = _server_engine()
    with engine.connect() as conn:
        if not _exists(conn, source):
            raise SystemExit(f"源数据库不存在：{source}")
        tables = _tables(conn, source)
        if not tables:
            raise SystemExit(f"源数据库没有基表：{source}")
        source_counts = _counts(conn, source, tables)
        source_foreign_keys = _foreign_keys(conn, source)
        _assert_no_unsupported_objects(conn, source)
        target_exists = _exists(conn, target)
        print(f"source={source} / target={target} / tables={len(tables)} / "
              f"rows={sum(source_counts.values())}")
        for table in tables:
            print(f"  {table:<30} {source_counts[table]:>8}")
        if not apply:
            print(f"\n[dry-run] writes=false；目标当前{'已存在（apply 会拒绝）' if target_exists else '不存在'}")
            return {"writes": False, "source": source, "target": target,
                    "tables": len(tables), "rows": sum(source_counts.values()),
                    "target_exists": target_exists}
        if target_exists:
            raise SystemExit(f"目标数据库已存在，拒绝覆盖：{target}")

    # DDL 会隐式提交，无法靠一个事务回滚；失败时只删除**本脚本刚创建**的目标库。
    # CREATE DATABASE 本身就是存在性检查：上面的 _exists 与这里之间若有人抢先建库，
    # 这条会失败，created_by_this_run 保持 False，清理分支绝不会删别人的库。
    created_by_this_run = False
    try:
        with engine.begin() as conn:
            conn.execute(text(
                f"CREATE DATABASE `{target}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"))
            created_by_this_run = True
            # Target tables do not have foreign keys yet, so rows can be inserted in any order.
            # Keep FOREIGN_KEY_CHECKS enabled; adding each FK afterwards then validates all copied rows.
            for table in tables:
                conn.execute(text(
                    f"CREATE TABLE `{target}`.`{table}` LIKE `{source}`.`{table}`"))
                conn.execute(text(
                    f"INSERT INTO `{target}`.`{table}` SELECT * FROM `{source}`.`{table}`"))
            # CREATE TABLE LIKE deliberately omits foreign keys. Recreate them only after all
            # data has landed, so each ADD CONSTRAINT validates every copied row for real.
            for fk in source_foreign_keys:
                columns = ", ".join(f"`{value}`" for value in fk["columns"])
                ref_columns = ", ".join(f"`{value}`" for value in fk["ref_columns"])
                conn.execute(text(
                    f"ALTER TABLE `{target}`.`{fk['table']}` "
                    f"ADD CONSTRAINT `{fk['constraint']}` FOREIGN KEY ({columns}) "
                    f"REFERENCES `{target}`.`{fk['ref_table']}` ({ref_columns}) "
                    f"ON UPDATE {fk['update']} ON DELETE {fk['delete']}"))
            # Adopting an existing index for a new constraint, InnoDB renames that index to the
            # constraint name. The source created the same FK inline in CREATE TABLE, where the
            # index keeps its column-derived name instead. Only the name diverges, so restore it
            # and let the strict signature comparison below stay an equality gate.
            _restore_index_names(conn, source, target)

        with engine.connect() as conn:
            target_tables = _tables(conn, target)
            # Re-read the source after the copy. Public graph writes are gated, but private auth/audit
            # rows can still grow under READ_ONLY=1; never publish a mixed-time partial snapshot.
            source_counts_after = _counts(conn, source, tables)
            target_counts = _counts(conn, target, target_tables)
            if source_counts_after != source_counts:
                raise RuntimeError(
                    "克隆期间源库发生写入，拒绝混合时点快照；请在低流量窗口重试。"
                    f" before={source_counts} after={source_counts_after}")
            mismatches = {name: (source_counts.get(name), target_counts.get(name))
                          for name in sorted(set(source_counts) | set(target_counts))
                          if source_counts.get(name) != target_counts.get(name)}
            if mismatches:
                raise RuntimeError(f"逐表行数不一致：{mismatches}")
            source_checksums = _checksums(conn, source, tables)
            target_checksums = _checksums(conn, target, target_tables)
            if source_checksums != target_checksums:
                diff = {name: (source_checksums.get(name), target_checksums.get(name))
                        for name in sorted(set(source_checksums) | set(target_checksums))
                        if source_checksums.get(name) != target_checksums.get(name)}
                raise RuntimeError(f"逐表内容 checksum 不一致：{diff}")
            source_columns = _column_signature(conn, source)
            target_columns = _column_signature(conn, target)
            if source_columns != target_columns:
                raise RuntimeError(_signature_diff("列定义签名", source_columns, target_columns))
            source_indexes = _index_signature(conn, source)
            target_indexes = _index_signature(conn, target)
            if source_indexes != target_indexes:
                raise RuntimeError(_signature_diff("索引签名", source_indexes, target_indexes))
            source_fks = _foreign_key_signature(conn, source)
            target_fks = _foreign_key_signature(conn, target)
            if source_fks != target_fks:
                raise RuntimeError(_signature_diff("外键签名", source_fks, target_fks))
            # AUTO_INCREMENT may advance independently while cloning; compare every stable table
            # option but deliberately ignore its next value. Exact rows/PKs are already counted.
            source_options = {k: v[:-1] for k, v in _table_options(conn, source).items()}
            target_options = {k: v[:-1] for k, v in _table_options(conn, target).items()}
            if source_options != target_options:
                raise RuntimeError("表引擎/行格式/字符集/选项签名不一致")
        print(f"\n[OK] {source} → {target}：{len(tables)} 表 / "
              f"{sum(source_counts.values())} 行 / {len(source_foreign_keys)} 外键，"
              "行数/checksum/列/索引/外键/表选项完全一致")
        return {"writes": True, "source": source, "target": target,
                "tables": len(tables), "rows": sum(source_counts.values())}
    except Exception:
        if created_by_this_run:
            with engine.begin() as conn:
                if _exists(conn, target):
                    conn.execute(text(f"DROP DATABASE `{target}`"))
        raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    clone(args.source, args.target, args.apply)


if __name__ == "__main__":
    main()
