"""Dry-run and idempotency checks for demo account/feedback initialization."""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models
from app.db import Base
from app.services.confidence_batch import run_confidence_recalculation
from data import seed_demo_accounts_feedback as seed


def _database():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _seed_evidence_chains(db, *, with_version: bool = True):
    job = models.Job(name="演示反馈岗位", slug="demo-feedback-job", category="人工智能",
                     status="published", version=1, confidence=.8,
                     core_responsibilities=[], typical_scenarios=[])
    db.add(job)
    db.flush()
    if with_version:
        db.add(models.JobVersion(job_id=job.id, version=1, status="published"))
    employer = models.Employer(name="真实证据企业", normalized_name="real-demo-employer",
                               status="active")
    db.add(employer)
    db.flush()
    for index in range(5):
        skill = models.Skill(name=f"演示技能{index}", normalized_name=f"演示技能{index}",
                             category="人工智能")
        db.add(skill)
        db.flush()
        relation = models.JobSkill(
            job_id=job.id, skill_id=skill.id, status="active",
            importance="required", weight=.8, confidence=.8)
        db.add(relation)
        db.flush()
        raw = models.RawJD(
            job_title=job.name, company=employer.name, employer_id=employer.id,
            source="official", source_authority=1.0,
            raw_text=f"岗位要求演示技能{index}", publish_date=datetime(2026, 8, 1),
            dedup_hash=f"demo-feedback-{index}", is_duplicate=False)
        db.add(raw)
        db.flush()
        db.add(models.Evidence(
            job_skill_id=relation.id, raw_jd_id=raw.id, source_type="jd",
            source_url=f"https://evidence.test/{index}", snippet=raw.raw_text))
    db.commit()


def test_demo_seed_is_dry_run_then_idempotent_apply():
    db = _database()
    try:
        _seed_evidence_chains(db)
        references = seed._evidence_references(db)
        before = (db.query(models.AppUser).count(), db.query(models.FeedbackTicket).count())
        plan = seed._plan(db, references)
        assert plan["mode"] == "dry-run" and plan["writes"] is False
        assert before == (db.query(models.AppUser).count(), db.query(models.FeedbackTicket).count())

        users = seed._ensure_users(db)
        organization = seed._ensure_organization(db, users)
        seed._ensure_feedback(db, users, organization, references)
        db.commit()
        counts = {
            "users": db.query(models.AppUser).count(),
            "organizations": db.query(models.Organization).count(),
            "tickets": db.query(models.FeedbackTicket).count(),
            "revisions": db.query(models.FeedbackRevision).count(),
            "events": db.query(models.FeedbackEvent).count(),
        }
        users = seed._ensure_users(db)
        organization = seed._ensure_organization(db, users)
        seed._ensure_feedback(db, users, organization, references)
        db.commit()
        assert counts == {
            "users": db.query(models.AppUser).count(),
            "organizations": db.query(models.Organization).count(),
            "tickets": db.query(models.FeedbackTicket).count(),
            "revisions": db.query(models.FeedbackRevision).count(),
            "events": db.query(models.FeedbackEvent).count(),
        }
        assert {row.status for row in db.query(models.FeedbackTicket).all()} == {
            "submitted", "triaged", "approved", "rejected", "applied"}
        assert db.query(models.FeedbackRevision).count() == 6
        applied = seed._existing_ticket(db, "demo-feedback-05")
        timeline = [row.event_type for row in db.query(models.FeedbackEvent).filter_by(
            ticket_id=applied.id).order_by(models.FeedbackEvent.id)]
        assert timeline == ["submitted", "revised", "triage", "approve", "apply"]

        as_of = datetime(2026, 8, 25, 18, 30)
        run_confidence_recalculation(db, as_of=as_of, trigger="seed")
        run_confidence_recalculation(db, as_of=as_of + timedelta(days=1), trigger="seed")
        verification = seed._verify(db)
        assert verification["confidence_snapshot_times"] == 2
    finally:
        db.close()


def test_demo_seed_plans_and_creates_real_baseline_version_when_missing():
    db = _database()
    try:
        _seed_evidence_chains(db, with_version=False)
        references = seed._evidence_references(db)
        plan = seed._plan(db, references)
        assert {item["version_action"] for item in plan["feedback"]} == {
            "create_current_baseline"}
        assert db.query(models.JobVersion).count() == 0
        users = seed._ensure_users(db)
        seed._ensure_baseline_versions(db, references, users["admin"])
        db.commit()
        references = seed._evidence_references(db)
        assert all(item["job_version_id"] for item in references)
        version = db.query(models.JobVersion).one()
        assert db.query(models.JobVersionSkill).filter_by(
            job_version_id=version.id).count() == 5
        assert version.contract_snapshot
    finally:
        db.close()
