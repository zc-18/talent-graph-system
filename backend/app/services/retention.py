"""Retention cleanup for private resume profiles and derived business records."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from .. import models


def cleanup_expired_resume_profiles(db: Session, *, cutoff: datetime | None = None,
                                    limit: int = 500,
                                    organization_id: int | None = None,
                                    dry_run: bool = False) -> dict:
    cutoff = cutoff or datetime.utcnow()
    limit = min(5000, max(1, limit))
    query = db.query(models.ResumeProfile).filter(
        models.ResumeProfile.retention_expires_at.isnot(None),
        models.ResumeProfile.retention_expires_at <= cutoff)
    if organization_id is not None:
        query = query.filter(models.ResumeProfile.organization_id == organization_id)
    profiles = query.order_by(models.ResumeProfile.retention_expires_at,
                              models.ResumeProfile.id).limit(limit).all()
    profile_ids = {row.id for row in profiles}
    candidates = (db.query(models.BatchCandidate).filter(
        models.BatchCandidate.resume_profile_id.in_(profile_ids)).all()
        if profile_ids else [])
    candidate_ids = {row.id for row in candidates}
    batch_ids = {row.batch_id for row in candidates}
    counts = {
        "profiles": len(profile_ids),
        "match_runs": (db.query(models.MatchRun).filter(
            models.MatchRun.resume_profile_id.in_(profile_ids)).count() if profile_ids else 0),
        "batch_candidates": len(candidate_ids),
        "candidate_selections": (db.query(models.CandidateSelection).filter(
            models.CandidateSelection.batch_candidate_id.in_(candidate_ids)).count()
            if candidate_ids else 0),
        "team_members": (db.query(models.TeamMember).filter(
            models.TeamMember.resume_profile_id.in_(profile_ids)).count() if profile_ids else 0),
    }
    report = {
        "cutoff": cutoff.isoformat(), "dry_run": dry_run,
        "organization_id": organization_id, "deleted": counts,
        "profile_ids": sorted(profile_ids),
    }
    if dry_run or not profile_ids:
        return report

    if candidate_ids:
        db.query(models.CandidateSelection).filter(
            models.CandidateSelection.batch_candidate_id.in_(candidate_ids)).delete(
            synchronize_session=False)
    db.query(models.TeamMember).filter(
        models.TeamMember.resume_profile_id.in_(profile_ids)).delete(synchronize_session=False)
    db.query(models.MatchRun).filter(
        models.MatchRun.resume_profile_id.in_(profile_ids)).delete(synchronize_session=False)
    db.query(models.BatchCandidate).filter(
        models.BatchCandidate.id.in_(candidate_ids)).delete(synchronize_session=False)
    db.query(models.ResumeProfile).filter(
        models.ResumeProfile.id.in_(profile_ids)).delete(synchronize_session=False)
    db.flush()

    for batch_id in batch_ids:
        batch = db.get(models.RecruitmentBatch, batch_id)
        if batch is None:
            continue
        remaining = db.query(models.BatchCandidate).filter(
            models.BatchCandidate.batch_id == batch_id).all()
        batch.total_count = len(remaining)
        batch.processed_count = sum(row.parse_status in {"succeeded", "failed"}
                                    for row in remaining)
        batch.succeeded_count = sum(row.parse_status == "succeeded" for row in remaining)
        batch.failed_count = sum(row.parse_status == "failed" for row in remaining)
        if batch.status not in {"queued", "processing"}:
            batch.status = "completed_with_errors" if batch.failed_count else "completed"
    return report
