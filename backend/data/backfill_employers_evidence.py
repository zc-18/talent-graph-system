"""Idempotent employer and evidence URL repair for a reviewed shadow database.

Dry-run is the default.  ``--apply`` updates only Employer/EmployerAlias,
RawJD.employer_id, and Evidence.source_url.  ``--recalculate`` additionally
invokes the system's existing five-factor confidence batch after the repair.
No job, version, capability-change, or evolution-run row is rebuilt or removed.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime
import json
from pathlib import Path
import sys
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import models  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.services.confidence_batch import run_confidence_recalculation  # noqa: E402
from app.services.employer_resolution import (  # noqa: E402
    get_or_create_employer, normalize_employer_name, register_employer_alias,
)


# Only explicitly reviewed subsidiaries are rolled up.  Unlisted brands and
# similarly named companies remain independent rather than being guessed.
REVIEWED_GROUP_RULES = {
    "网易集团": (
        "网易（杭州）网络有限公司", "广州网易计算机系统有限公司",
        "网易有道信息技术（北京）有限公司", "杭州网易云音乐科技有限公司",
        "网易传媒科技（北京）有限公司",
    ),
    "蔚来集团": (
        "蔚来控股有限公司", "上海蔚来汽车有限公司",
        "武汉蔚来能源有限公司", "安徽蔚来汽车销售服务有限公司",
    ),
    "腾讯集团": (
        "腾讯科技（深圳）有限公司", "深圳市腾讯计算机系统有限公司",
        "腾讯云计算（北京）有限责任公司",
    ),
}
GROUP_INDEX = {
    normalize_employer_name(alias): group
    for group, aliases in REVIEWED_GROUP_RULES.items()
    for alias in aliases
}


def _valid_http_url(value: str | None) -> bool:
    parsed = urlparse((value or "").strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _canonical_unit_key(raw, employers: dict[int, models.Employer],
                        *, infer_unlinked: bool = False) -> str | None:
    if raw.employer_id:
        employer = employers.get(raw.employer_id)
        if employer and employer.status == "active":
            return f"id:{employer.parent_id or employer.id}"
    if not infer_unlinked:
        return None
    normalized = normalize_employer_name(raw.company)
    if not normalized:
        return None
    group = GROUP_INDEX.get(normalized)
    return f"group:{normalize_employer_name(group)}" if group else f"name:{normalized}"


def _protected_counts(db) -> dict[str, int]:
    return {
        "job_versions": db.query(models.JobVersion).count(),
        "job_version_skills": db.query(models.JobVersionSkill).count(),
        "capability_changes": db.query(models.CapabilityChange).count(),
        "evolution_runs": db.query(models.EvolutionRun).count(),
        "evolution_reviews": db.query(models.EvolutionReview).count(),
    }


def _job_snapshot(db) -> dict[int, dict]:
    jobs = {row.id: row for row in db.query(models.Job).filter(
        models.Job.status == "published").all()}
    relation_job = dict(db.query(models.JobSkill.id, models.JobSkill.job_id).filter(
        models.JobSkill.job_id.in_(jobs)).all()) if jobs else {}
    evidence_rows = (db.query(models.Evidence, models.RawJD)
                     .join(models.RawJD, models.RawJD.id == models.Evidence.raw_jd_id)
                     .filter(models.Evidence.job_skill_id.in_(relation_job),
                             models.Evidence.source_type == "jd",
                             models.RawJD.is_duplicate == False,  # noqa: E712
                             models.RawJD.duplicate_of.is_(None),
                             models.RawJD.raw_text.isnot(None)).all()) if relation_job else []
    employer_ids = {raw.employer_id for _, raw in evidence_rows if raw.employer_id}
    employers = {row.id: row for row in db.query(models.Employer).filter(
        models.Employer.id.in_(employer_ids)).all()} if employer_ids else {}
    parent_ids = {row.parent_id for row in employers.values() if row.parent_id}
    if parent_ids:
        employers.update({row.id: row for row in db.query(models.Employer).filter(
            models.Employer.id.in_(parent_ids)).all()})
    by_job = defaultdict(lambda: {"jd_ids": set(), "employers": set(), "urls": set()})
    for evidence, raw in evidence_rows:
        job_id = relation_job[evidence.job_skill_id]
        by_job[job_id]["jd_ids"].add(raw.id)
        unit = _canonical_unit_key(raw, employers)
        if unit:
            by_job[job_id]["employers"].add(unit)
        if _valid_http_url(evidence.source_url):
            by_job[job_id]["urls"].add(evidence.id)

    factors_by_job = defaultdict(list)
    for job_id, factors in db.query(models.JobSkill.job_id, models.JobSkill.factors).filter(
            models.JobSkill.job_id.in_(jobs), models.JobSkill.status == "active").all():
        if isinstance(factors, dict):
            factors_by_job[job_id].append(factors)
    factor_keys = ("support", "diversity", "freshness", "authority", "external")
    result = {}
    for job_id, job in jobs.items():
        rows = factors_by_job.get(job_id, [])
        result[job_id] = {
            "job_id": job.id, "job_name": job.name, "job_version": job.version or 1,
            "confidence": round(float(job.confidence or 0.0), 4),
            "factors": {key: round(sum(float(row.get(key, 0.0) or 0.0) for row in rows)
                                          / max(1, len(rows)), 4) for key in factor_keys},
            "valid_jd_count": len(by_job[job_id]["jd_ids"]),
            "employer_count": len(by_job[job_id]["employers"]),
            "valid_evidence_url_count": len(by_job[job_id]["urls"]),
        }
    return result


def _summary(db) -> dict:
    raw_rows = db.query(models.RawJD.id, models.RawJD.company,
                        models.RawJD.employer_id).all()
    known_company = sum(bool(normalize_employer_name(company)) for _, company, _ in raw_rows)
    identified = sum(employer_id is not None for _, _, employer_id in raw_rows)
    evidence_urls = [row[0] for row in db.query(models.Evidence.source_url).all()]
    return {
        "raw_jds": len(raw_rows), "known_company_jds": known_company,
        "identified_employer_jds": identified,
        "unknown_employer_jds": len(raw_rows) - identified,
        "evidence_rows": len(evidence_urls),
        "valid_evidence_urls": sum(_valid_http_url(value) for value in evidence_urls),
    }


def _resolve_employer(db, company: str | None):
    normalized = normalize_employer_name(company)
    if not normalized:
        return None
    group_name = GROUP_INDEX.get(normalized)
    if not group_name:
        return get_or_create_employer(db, company)
    parent = get_or_create_employer(db, group_name)
    child = get_or_create_employer(db, company)
    if child.id != parent.id:
        child.parent_id = parent.id
    register_employer_alias(db, child, company or "")
    return child


def apply_backfill(db, *, recalculate: bool = False,
                   as_of: datetime | None = None) -> dict:
    protected_before = _protected_counts(db)
    summary_before = _summary(db)
    jobs_before = _job_snapshot(db)
    employer_updates = 0
    url_updates = 0
    for raw in db.query(models.RawJD).order_by(models.RawJD.id):
        employer = _resolve_employer(db, raw.company)
        employer_id = employer.id if employer else None
        if raw.employer_id != employer_id:
            raw.employer_id = employer_id
            employer_updates += 1
    db.flush()
    for evidence, raw_url in (db.query(models.Evidence, models.RawJD.source_url)
                              .join(models.RawJD,
                                    models.RawJD.id == models.Evidence.raw_jd_id).all()):
        if not _valid_http_url(evidence.source_url) and _valid_http_url(raw_url):
            evidence.source_url = raw_url.strip()
            url_updates += 1
    db.commit()
    confidence_run = None
    if recalculate:
        confidence_run = run_confidence_recalculation(
            db, as_of=as_of, trigger="employer_backfill")
    protected_after = _protected_counts(db)
    if protected_before != protected_after:
        raise RuntimeError(
            f"演化保护失败：受保护表数量变化 {protected_before} -> {protected_after}")
    jobs_after = _job_snapshot(db)
    return {
        "mode": "applied", "writes": True,
        "employer_updates": employer_updates, "url_updates": url_updates,
        "summary_before": summary_before, "summary_after": _summary(db),
        "protected_counts": protected_after,
        "confidence_run": confidence_run,
        "jobs": [{
            "job_id": job_id, "job_name": after["job_name"],
            "before": jobs_before.get(job_id), "after": after,
            "confidence_delta": round(after["confidence"]
                                      - float(jobs_before.get(job_id, {}).get(
                                          "confidence", 0.0)), 4),
        } for job_id, after in sorted(jobs_after.items())],
    }


def dry_run(db) -> dict:
    summary = _summary(db)
    raw_rows = db.query(models.RawJD).all()
    predicted_keys = {_canonical_unit_key(raw, {}, infer_unlinked=True) for raw in raw_rows}
    predicted_keys.discard(None)
    url_candidates = (db.query(models.Evidence.id)
                      .join(models.RawJD, models.RawJD.id == models.Evidence.raw_jd_id)
                      .filter((models.Evidence.source_url.is_(None))
                              | (models.Evidence.source_url == ""),
                              models.RawJD.source_url.isnot(None),
                              models.RawJD.source_url != "").count())
    return {
        "mode": "dry-run", "writes": False,
        "summary": summary,
        "predicted_employer_units": len(predicted_keys),
        "url_backfill_candidates": url_candidates,
        "protected_counts": _protected_counts(db),
        "jobs": list(_job_snapshot(db).values()),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true",
                        help="write employer and URL repairs; default is dry-run")
    parser.add_argument("--recalculate", action="store_true",
                        help="run the existing five-factor confidence batch after repair")
    parser.add_argument("--as-of", help="ISO timestamp used for idempotent confidence replay")
    parser.add_argument("--report", type=Path,
                        help="optional JSON report path (recommended for shadow release)")
    args = parser.parse_args()
    if args.recalculate and not args.apply:
        parser.error("--recalculate requires --apply")
    db = SessionLocal()
    try:
        result = (apply_backfill(
            db, recalculate=args.recalculate,
            as_of=datetime.fromisoformat(args.as_of) if args.as_of else None)
                  if args.apply else dry_run(db))
        payload = json.dumps(result, ensure_ascii=False, indent=2)
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(payload, encoding="utf-8")
        print(payload)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
