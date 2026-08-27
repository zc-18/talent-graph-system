"""Dry-run-by-default cleanup for expired private resume profiles in a SQLite shadow."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True,
                        help="existing SQLite database; production MySQL is intentionally unsupported")
    parser.add_argument("--organization-id", type=int)
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--commit", action="store_true",
                        help="perform deletion; without this flag only a dry-run report is printed")
    args = parser.parse_args()
    database = args.database.resolve()
    if not database.is_file():
        raise SystemExit(f"SQLite database not found: {database}")
    os.environ["DATABASE_URL_OVERRIDE"] = f"sqlite:///{database.as_posix()}"
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

    from app.db import SessionLocal
    from app.services.retention import cleanup_expired_resume_profiles

    db = SessionLocal()
    try:
        report = cleanup_expired_resume_profiles(
            db, organization_id=args.organization_id, limit=args.limit,
            dry_run=not args.commit)
        if args.commit:
            db.commit()
        else:
            db.rollback()
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        return 0
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
