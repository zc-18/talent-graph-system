# -*- coding: utf-8 -*-
"""Reconcile all JobSkill metrics/statuses on a disposable database shadow.

Default is strictly read-only. Apply is allowed on SQLite, or on a deliberately
named non-production database containing ``shadow`` after two exact CLI
confirmations. ``talent_graph_v3`` is refused unconditionally: clone it, validate
the shadow, then use the governed cutover process.

This corrects current facts; it does not create CapabilityChange evolution rows.
Candidate rows are never auto-promoted.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import models  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.services import repair_safety, state_reconcile  # noqa: E402


BACKUP_DIR = Path(__file__).resolve().parent / "backup"


def _backup(db, *, as_of: datetime, run_id: str) -> dict:
    return {
        "schema": "r6-job-skill-state-backup-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "as_of": as_of.isoformat(), "run_id": run_id,
        "database": db.get_bind().url.database,
        **state_reconcile.backup_projection(db),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true",
                        help="stage+verify+commit on an allowed shadow (default dry-run)")
    parser.add_argument("--allow-shadow", action="store_true",
                        help="approve a non-SQLite shadow; production remains forbidden")
    parser.add_argument("--confirm-database", default=None,
                        help="must exactly equal the connected non-SQLite shadow database")
    parser.add_argument("--as-of", default=None,
                        help="UTC ISO timestamp; defaults to current UTC")
    args = parser.parse_args(argv)
    as_of = (datetime.fromisoformat(args.as_of) if args.as_of
             else datetime.now(timezone.utc))
    as_of = as_of.astimezone(timezone.utc).replace(tzinfo=None, microsecond=0)
    run_id = f"r6-state-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid4().hex[:8]}"

    db = SessionLocal()
    try:
        plan = state_reconcile.plan_all(db, as_of=as_of)
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        if not args.apply:
            db.rollback()
            print("[dry-run] zero writes; candidate rows reaching the gate are not promoted")
            return 0

        repair_safety.assert_shadow_apply_target(
            db, allow_shadow=args.allow_shadow,
            confirm_database=args.confirm_database)
        path = repair_safety.backup_path(BACKUP_DIR, "job_skill_state_r6")
        repair_safety.write_json_exclusive(
            path, _backup(db, as_of=as_of, run_id=run_id))
        try:
            before_changes = db.query(models.CapabilityChange).count()
            before_authority = db.query(models.AuthorityEvidence).count()
            manifest = state_reconcile.reconcile_all(
                db, as_of=as_of, run_id=run_id)
            errors = state_reconcile.verify_all(db, as_of=as_of)
            if db.query(models.CapabilityChange).count() != before_changes:
                errors.append("CapabilityChange count changed")
            if db.query(models.AuthorityEvidence).count() != before_authority:
                errors.append("AuthorityEvidence count changed")
            audit = db.query(models.AuditLog).filter(
                models.AuditLog.action == state_reconcile.AUDIT_ACTION,
                models.AuditLog.target_id == run_id).one_or_none()
            if manifest["audit_created"] and audit is None:
                errors.append("repair AuditLog missing")
            if errors:
                raise RuntimeError("commit 前验证失败：" + "; ".join(errors[:20]))
            db.commit()
        except Exception:
            db.rollback()
            raise
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        print(f"[committed shadow] backup={path}")
        return 0
    except Exception as exc:
        db.rollback()
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
