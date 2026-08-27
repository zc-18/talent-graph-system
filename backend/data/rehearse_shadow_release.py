"""Rehearse migration, backup, corruption and rollback on a disposable SQLite shadow."""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen


PUBLIC_TABLES = (
    "job", "skill", "job_skill", "evidence", "job_level_skill",
    "job_version", "job_version_skill", "authority_evidence",
)
PRIVATE_TABLES = (
    "resume", "match_result", "resume_batch", "talent_profile", "team", "team_member",
    "app_user", "organization", "organization_member", "user_session", "audit_log",
    "usage_event", "resume_profile", "match_run", "recruitment_batch", "batch_candidate",
    "candidate_selection", "feedback_ticket", "feedback_revision",
)


def _backup(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(source)) as src, closing(sqlite3.connect(target)) as dst:
        src.backup(dst)


def _state(path: Path) -> dict:
    tables: dict[str, dict] = {}
    digest = hashlib.sha256()
    with closing(sqlite3.connect(path)) as connection:
        for table in PUBLIC_TABLES + PRIVATE_TABLES:
            exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()
            if not exists:
                tables[table] = {"count": 0, "present": False, "sha256": None}
                continue
            columns = [row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')]
            rows = connection.execute(f'SELECT * FROM "{table}" ORDER BY rowid').fetchall()
            payload = json.dumps({"columns": columns, "rows": rows}, ensure_ascii=False,
                                 default=str, separators=(",", ":")).encode("utf-8")
            digest.update(table.encode("utf-8")); digest.update(payload)
            tables[table] = {"count": len(rows), "present": True,
                             "sha256": hashlib.sha256(payload).hexdigest()}
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    return {"sha256": digest.hexdigest(), "tables": tables, "integrity": integrity}


def _read_only(health_url: str) -> tuple[bool, dict]:
    with urlopen(health_url, timeout=10) as response:
        body = json.loads(response.read().decode("utf-8"))
    return body.get("read_only") is True, body


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--health-url", default="http://127.0.0.1:8200/api/health")
    parser.add_argument("--source-kind", choices=("unspecified", "test", "demo",
                                                   "production_snapshot"),
                        default="unspecified")
    args = parser.parse_args()
    source, artifacts = args.source.resolve(), args.artifacts.resolve()
    if not source.is_file():
        raise SystemExit(f"shadow database not found: {source}")
    source_hash_before = hashlib.sha256(source.read_bytes()).hexdigest()
    backup = artifacts / "shadow-final-backup.db"
    rehearsal = artifacts / "shadow-final-rehearsal.db"
    _backup(source, backup)
    _backup(backup, rehearsal)
    before = _state(rehearsal)

    os.environ["DATABASE_URL_OVERRIDE"] = f"sqlite:///{rehearsal.as_posix()}"
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from app import models  # noqa: F401,E402
    from app.db import Base, engine  # noqa: E402
    Base.metadata.create_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    migrated = _state(rehearsal)

    with closing(sqlite3.connect(rehearsal)) as connection:
        job_id = connection.execute("SELECT id FROM job ORDER BY id LIMIT 1").fetchone()[0]
        connection.execute(
            "UPDATE job SET summary='ROLLBACK_REHEARSAL_SENTINEL', version=version+99 WHERE id=?",
            (job_id,),
        )
        evidence = connection.execute("SELECT id FROM evidence ORDER BY id LIMIT 1").fetchone()
        if evidence:
            connection.execute("DELETE FROM evidence WHERE id=?", (evidence[0],))
        connection.commit()
    corrupted = _state(rehearsal)

    engine.dispose()
    rehearsal.unlink()
    _backup(backup, rehearsal)
    restored = _state(rehearsal)
    read_only, health = _read_only(args.health_url)
    source_hash_after = hashlib.sha256(source.read_bytes()).hexdigest()
    health_host = (urlparse(args.health_url).hostname or "").lower()
    health_is_remote = health_host not in {"localhost", "127.0.0.1", "::1", ""}
    private_tables_preserved = all(
        before["tables"][table]["present"] and
        before["tables"][table]["sha256"] == restored["tables"][table]["sha256"]
        for table in PRIVATE_TABLES
    )
    summary = {
        "shadow_migration": migrated["integrity"] == "ok" and migrated["sha256"] == before["sha256"],
        "backup_created": backup.is_file() and backup.stat().st_size > 0,
        "state_reconciled": before["sha256"] == restored["sha256"],
        "rollback_rehearsed": corrupted["sha256"] != before["sha256"] == restored["sha256"],
        "production_read_only": read_only,
        "production_snapshot_imported": args.source_kind == "production_snapshot",
        "employer_recompute_completed": False,
        "shadow_cutover_rehearsed": False,
        "private_tables_preserved": private_tables_preserved,
        "production_health_verified": read_only and health_is_remote,
    }
    result = {
        "run_id": args.run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": summary,
        "source_kind": args.source_kind,
        "source_database": str(source),
        "source_was_not_modified": source_hash_before == source_hash_after,
        "source_sha256_before": source_hash_before,
        "source_sha256_after": source_hash_after,
        "schema_operation": "sqlalchemy_create_all_idempotence_only",
        "health": health,
        "states": {"before": before, "after_migration": migrated,
                   "corrupted": corrupted, "restored": restored},
        "backup": {"path": str(backup), "size": backup.stat().st_size,
                   "sha256": hashlib.sha256(backup.read_bytes()).hexdigest()},
    }
    output = artifacts / "eval_migration_result.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    print(f"report: {output}")
    return 0 if all(summary.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
