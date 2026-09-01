"""Safety primitives shared by one-shot data repairs.

Repairs are rehearsed on SQLite or a deliberately named database shadow.  The
currently served production database is never a valid apply target.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


# 生产库与整条回滚链都不是 repair 的 apply 目标。切库时必须把新库名加进来：
# 这里漏一个名字，第一道闸就对那个库空转（只剩"库名须含 shadow"这一层兜底）。
PROTECTED_DATABASES = frozenset({
    "talent_graph",        # config.py 默认值，最初的演示库
    "talent_graph_v2",     # 老图谱（806 条语料）
    "talent_graph_v3",     # R6 补闸前快照
    "talent_graph_v4",     # R6 发布库
    "talent_graph_v5",     # R7 发布库 —— 当前生产
})
PRODUCTION_DATABASE = "talent_graph_v5"  # 当前对外服务的库


def assert_shadow_apply_target(db, *, allow_shadow: bool,
                               confirm_database: str | None) -> str:
    """Allow SQLite; require two exact confirmations for a non-production shadow."""
    # Session.get_bind() may hand back a Connection (a session bound to an open
    # connection); only Engine carries ``.url``. Normalise before reading it so the
    # guard states a verdict instead of dying on AttributeError.
    bind = db.get_bind()
    engine = getattr(bind, "engine", bind)
    if engine.dialect.name == "sqlite":
        return "sqlite"
    database = engine.url.database or ""
    if database in PROTECTED_DATABASES:
        raise RuntimeError(
            f"拒绝 apply：{database} 属于生产/回滚链（当前生产库 {PRODUCTION_DATABASE}），"
            "永远不是 repair 发布目标；请先克隆到新 shadow 库验收后切库")
    if (not allow_shadow or confirm_database != database
            or "shadow" not in database.casefold()):
        raise RuntimeError(
            "拒绝非 SQLite apply：目标必须是名称含 shadow 的影子库，并同时传 "
            "--allow-shadow 与精确匹配实际连接的 --confirm-database")
    return database


def backup_path(directory: Path, prefix: str) -> Path:
    """Return a timezone-explicit, microsecond-resolution backup path."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    return directory / f"{prefix}_{stamp}.json"


def write_json_exclusive(path: Path, payload) -> None:
    """Create a backup atomically enough to refuse accidental overwrite."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.flush()
