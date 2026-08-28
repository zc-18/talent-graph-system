"""Run one auditable confidence recalculation against the configured database."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import SessionLocal  # noqa: E402
from app.services.confidence_batch import run_confidence_recalculation  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--as-of", help="ISO-8601 factual time; defaults to current UTC")
    parser.add_argument("--trigger", default="manual", choices=("manual", "scheduled", "seed"))
    args = parser.parse_args()
    as_of = datetime.fromisoformat(args.as_of) if args.as_of else None
    db = SessionLocal()
    try:
        print(json.dumps(run_confidence_recalculation(
            db, as_of=as_of, trigger=args.trigger), ensure_ascii=False, indent=2))
    finally:
        db.close()


if __name__ == "__main__":
    main()
