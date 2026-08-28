"""Evidence-only full-database confidence recalculation and daily scheduler."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
import logging
from threading import Event, Lock, Thread

from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import models
from ..config import settings
from ..db import SessionLocal
from . import confidence as confidence_formula


logger = logging.getLogger("talent-graph.confidence")
BEIJING = timezone(timedelta(hours=8), name="Asia/Shanghai")
UTC = timezone.utc
HALF_LIFE_DAYS = 180.0
REAL_EXTERNAL_TYPES = {"web", "external", "authority", "policy", "report"}


def _naive_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(microsecond=0)
    return value.astimezone(UTC).replace(tzinfo=None, microsecond=0)


def next_scheduled_utc(now: datetime | None = None) -> datetime:
    current = (now or datetime.now(UTC)).astimezone(BEIJING)
    target = current.replace(hour=settings.confidence_scheduler_hour,
                             minute=settings.confidence_scheduler_minute,
                             second=0, microsecond=0)
    if target <= current:
        target += timedelta(days=1)
    return target.astimezone(UTC).replace(tzinfo=None)


def _source_authority(raw: models.RawJD) -> float:
    source = f"{raw.source or ''} {raw.platform or ''}".casefold()
    if any(token in source for token in ("gov", "政府", "人社", "国聘", "official")):
        return 1.0
    if any(token in source for token in ("官网", "career", "company_site", "企业招聘")):
        return 1.0
    if any(token in source for token in ("dataset", "数据集", "tianchi", "kaggle", "huggingface")):
        return confidence_formula.SOURCE_AUTHORITY["dataset"]
    if raw.source_authority is not None:
        return max(0.0, min(1.0, float(raw.source_authority)))
    return confidence_formula.SOURCE_AUTHORITY["web"]


def _freshness(raw: models.RawJD, as_of: datetime) -> float:
    observed = raw.publish_date or raw.collected_at
    if not observed:
        return 0.0
    age_days = max(0.0, (as_of - observed).total_seconds() / 86400.0)
    return round(0.5 ** (age_days / HALF_LIFE_DAYS), 6)


def _is_valid_raw_jd(raw: models.RawJD | None, as_of: datetime) -> bool:
    if raw is None or raw.is_duplicate or raw.duplicate_of is not None:
        return False
    if not (raw.raw_text or "").strip():
        return False
    observed = raw.publish_date or raw.collected_at
    return observed is None or observed <= as_of


def _dedup_key(raw: models.RawJD) -> tuple[str, str | int]:
    return ("hash", raw.dedup_hash) if raw.dedup_hash else ("id", raw.id)


def _employer_key(raw: models.RawJD, employers: dict[int, models.Employer]) -> int | None:
    if not raw.employer_id:
        return None
    employer = employers.get(raw.employer_id)
    if not employer or employer.status != "active":
        return None
    if employer.parent_id:
        parent = employers.get(employer.parent_id)
        if not parent or parent.status != "active":
            return None
        return parent.id
    return employer.id


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _weighted_factors(rows: list[tuple[models.JobSkill, dict[str, float]]]) -> dict[str, float]:
    if not rows:
        return {key: 0.0 for key in confidence_formula.WEIGHTS}
    weights = [max(0.05, float(row.weight or 0.5)) for row, _ in rows]
    total = sum(weights)
    return {key: round(sum(factors[key] * weight for (_, factors), weight in zip(rows, weights))
                       / total, 4)
            for key in confidence_formula.WEIGHTS}


def _job_calculation(db: Session, job: models.Job, as_of: datetime) -> dict:
    relations = db.query(models.JobSkill).filter(
        models.JobSkill.job_id == job.id,
        models.JobSkill.status == "active").all()
    relation_ids = [row.id for row in relations]
    evidence_rows = (db.query(models.Evidence).filter(
        models.Evidence.job_skill_id.in_(relation_ids)).all()) if relation_ids else []
    raw_ids = {row.raw_jd_id for row in evidence_rows if row.raw_jd_id}
    raw_jds = {row.id: row for row in db.query(models.RawJD).filter(
        models.RawJD.id.in_(raw_ids)).all()} if raw_ids else {}
    employer_ids = {row.employer_id for row in raw_jds.values() if row.employer_id}
    employers = {row.id: row for row in db.query(models.Employer).filter(
        models.Employer.id.in_(employer_ids)).all()} if employer_ids else {}
    parent_ids = {row.parent_id for row in employers.values() if row.parent_id}
    if parent_ids:
        employers.update({row.id: row for row in db.query(models.Employer).filter(
            models.Employer.id.in_(parent_ids)).all()})

    by_relation: dict[int, list[models.Evidence]] = defaultdict(list)
    for evidence in evidence_rows:
        by_relation[evidence.job_skill_id].append(evidence)

    valid_job_jds: dict[tuple[str, str | int], models.RawJD] = {}
    for evidence in evidence_rows:
        raw = raw_jds.get(evidence.raw_jd_id)
        if evidence.source_type == "jd" and _is_valid_raw_jd(raw, as_of):
            valid_job_jds.setdefault(_dedup_key(raw), raw)
    total_valid_jds = len(valid_job_jds)

    authority_rows = db.query(models.AuthorityEvidence).filter(
        models.AuthorityEvidence.job_id == job.id).all()
    has_authority = any(
        (row.publish_date is None or row.publish_date <= as_of)
        and bool((row.url or "").strip() or (row.local_file or "").strip())
        for row in authority_rows)

    relation_results: list[tuple[models.JobSkill, dict[str, float]]] = []
    real_evidence_keys: set[tuple[str, str | int]] = set(valid_job_jds)
    for relation in relations:
        supporting: dict[tuple[str, str | int], models.RawJD] = {}
        external_keys: set[str] = set()
        for evidence in by_relation.get(relation.id, []):
            raw = raw_jds.get(evidence.raw_jd_id)
            if evidence.source_type == "jd" and _is_valid_raw_jd(raw, as_of):
                supporting.setdefault(_dedup_key(raw), raw)
            elif (evidence.source_type or "").casefold() in REAL_EXTERNAL_TYPES:
                key = (evidence.source_url or evidence.snippet or "").strip()
                if key:
                    external_keys.add(key)
                    real_evidence_keys.add(("external", key))
        employer_keys = {_employer_key(raw, employers) for raw in supporting.values()}
        employer_keys.discard(None)
        freshness = [_freshness(raw, as_of) for raw in supporting.values()]
        authorities = [_source_authority(raw) for raw in supporting.values()]
        factors = confidence_formula.factors_from_jd(
            support_ratio=len(supporting) / total_valid_jds if total_valid_jds else 0.0,
            platforms={str(key) for key in employer_keys},
            avg_freshness=_mean(freshness),
            avg_authority=_mean(authorities),
            has_web=bool(external_keys) or has_authority,
        )
        relation.confidence = confidence_formula.compute(factors)
        relation.factors = factors
        relation.source_count = len(employer_keys)
        relation_results.append((relation, factors))

    factors = _weighted_factors(relation_results)
    if relation_results:
        weights = [max(0.05, float(row.weight or 0.5)) for row, _ in relation_results]
        score = round(sum(float(row.confidence or 0.0) * weight
                          for (row, _), weight in zip(relation_results, weights)) / sum(weights), 4)
    else:
        score = 0.0

    version = db.query(models.JobVersion).filter(
        models.JobVersion.job_id == job.id,
        models.JobVersion.version == (job.version or 1)).order_by(
        models.JobVersion.id.desc()).first()
    if version:
        current_by_skill = {row.skill_id: row for row, _ in relation_results}
        version_skills = db.query(models.JobVersionSkill).filter(
            models.JobVersionSkill.job_version_id == version.id).all()
        for snapshot_skill in version_skills:
            current = current_by_skill.get(snapshot_skill.skill_id)
            if current:
                snapshot_skill.confidence = current.confidence
                snapshot_skill.factors = current.factors

    return {
        "confidence": score,
        "factors": factors,
        "valid_jd_count": total_valid_jds,
        "evidence_count": len(real_evidence_keys),
        "job_version_id": version.id if version else None,
    }


def run_confidence_recalculation(db: Session, *, as_of: datetime | None = None,
                                 trigger: str = "manual") -> dict:
    """Recalculate all published jobs atomically; the same as-of time is idempotent."""
    calculation_time = _naive_utc(as_of or datetime.now(UTC))
    existing = db.query(models.ConfidenceRun).filter(
        models.ConfidenceRun.as_of == calculation_time).first()
    if existing and existing.status == "completed":
        return _run_result(existing, idempotent_replay=True)
    if existing:
        db.query(models.JobConfidenceSnapshot).filter(
            models.JobConfidenceSnapshot.run_id == existing.id).delete(synchronize_session=False)
        run = existing
        run.status = "running"
        run.error = None
        run.started_at = datetime.utcnow()
        run.completed_at = None
    else:
        run = models.ConfidenceRun(
            as_of=calculation_time, trigger=trigger, status="running",
            formula=confidence_formula.FORMULA_TEXT)
        db.add(run)
    try:
        db.flush()
        jobs = db.query(models.Job).filter(models.Job.status == "published").order_by(
            models.Job.id).all()
        evidence_total = 0
        valid_jd_total = 0
        for job in jobs:
            result = _job_calculation(db, job, calculation_time)
            previous = db.query(models.JobConfidenceSnapshot).filter(
                models.JobConfidenceSnapshot.job_id == job.id,
                models.JobConfidenceSnapshot.as_of < calculation_time).order_by(
                models.JobConfidenceSnapshot.as_of.desc()).first()
            before = float(job.confidence or 0.0)
            baseline = float(previous.confidence) if previous else before
            score = float(result["confidence"])
            delta = round(score - baseline, 4)
            job.confidence = score
            job.evidence_count = result["evidence_count"]
            db.add(models.JobConfidenceSnapshot(
                run_id=run.id, job_id=job.id,
                job_version_id=result["job_version_id"], job_version=job.version or 1,
                as_of=calculation_time, evidence_count=result["evidence_count"],
                valid_jd_count=result["valid_jd_count"], factors=result["factors"],
                previous_confidence=baseline, confidence=score, delta=delta))
            evidence_total += result["evidence_count"]
            valid_jd_total += result["valid_jd_count"]
        run.job_count = len(jobs)
        run.evidence_count = evidence_total
        run.valid_jd_count = valid_jd_total
        run.status = "completed"
        run.completed_at = datetime.utcnow()
        db.commit()
        return _run_result(run, idempotent_replay=False)
    except Exception as exc:
        db.rollback()
        failed = db.query(models.ConfidenceRun).filter(
            models.ConfidenceRun.as_of == calculation_time).first()
        if failed is None:
            failed = models.ConfidenceRun(
                as_of=calculation_time, trigger=trigger, status="failed",
                formula=confidence_formula.FORMULA_TEXT)
            db.add(failed)
        failed.status = "failed"
        failed.error = str(exc)[:2000]
        failed.completed_at = datetime.utcnow()
        db.commit()
        raise


def _run_result(run: models.ConfidenceRun, *, idempotent_replay: bool) -> dict:
    return {
        "run_id": run.id, "status": run.status, "trigger": run.trigger,
        "as_of": run.as_of.isoformat(), "job_count": run.job_count,
        "evidence_count": run.evidence_count, "valid_jd_count": run.valid_jd_count,
        "idempotent_replay": idempotent_replay,
    }


def latest_snapshot_map(db: Session, job_ids: set[int]) -> dict[int, models.JobConfidenceSnapshot]:
    if not job_ids:
        return {}
    latest = db.query(
        models.JobConfidenceSnapshot.job_id.label("job_id"),
        func.max(models.JobConfidenceSnapshot.as_of).label("as_of"),
    ).filter(models.JobConfidenceSnapshot.job_id.in_(job_ids)).group_by(
        models.JobConfidenceSnapshot.job_id).subquery()
    rows = db.query(models.JobConfidenceSnapshot).join(
        latest,
        (models.JobConfidenceSnapshot.job_id == latest.c.job_id)
        & (models.JobConfidenceSnapshot.as_of == latest.c.as_of)).all()
    return {row.job_id: row for row in rows}


class ConfidenceScheduler:
    def __init__(self) -> None:
        self._stop = Event()
        self._lock = Lock()
        self._thread: Thread | None = None

    def start(self) -> None:
        if not settings.confidence_scheduler_enabled:
            logger.info("confidence scheduler disabled")
            return
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = Thread(target=self._run, name="confidence-scheduler", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=2.0)

    def _run(self) -> None:
        while not self._stop.is_set():
            scheduled = next_scheduled_utc()
            wait_seconds = max(0.0, (scheduled - datetime.utcnow()).total_seconds())
            logger.info("next confidence run scheduled for %s UTC", scheduled.isoformat())
            if self._stop.wait(wait_seconds):
                return
            db = SessionLocal()
            try:
                result = run_confidence_recalculation(
                    db, as_of=scheduled, trigger="scheduled")
                logger.info("confidence run completed: %s", result)
            except Exception:
                logger.exception("scheduled confidence run failed")
            finally:
                db.close()


scheduler = ConfidenceScheduler()
