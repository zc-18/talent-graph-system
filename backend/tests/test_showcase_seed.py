"""Showcase workflow records are complete, anonymized and idempotent."""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models
from app.db import Base
from data import seed_showcase_records as seed


def _database():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    job = models.Job(
        name="展示目标岗位", slug="showcase-target", category="人工智能",
        status="published", version=1, confidence=.86, level="middle",
        track="software", industry="internet", recruitment_type="social",
        core_responsibilities=[], typical_scenarios=[])
    db.add(job)
    db.flush()
    for index in range(8):
        skill = models.Skill(name=f"能力{index}", normalized_name=f"能力{index}",
                             category="人工智能")
        db.add(skill)
        db.flush()
        db.add(models.JobSkill(
            job_id=job.id, skill_id=skill.id, status="active",
            importance="required", weight=.9 - index * .03,
            confidence=.82, source_count=3,
            factors={"support": .7, "diversity": 1.0, "freshness": .9,
                     "authority": .8, "external": .5}))
    db.commit()
    return db


def test_showcase_seed_is_idempotent_and_pii_free():
    db = _database()
    try:
        before = db.query(models.AppUser).count()
        planned = seed.plan(db)
        assert planned["writes"] is False and planned["candidates"] == 60
        assert db.query(models.AppUser).count() == before

        first = seed.apply_showcase(db, include_feedback=False)
        counts = {
            model.__tablename__: db.query(model).count()
            for model in (models.AppUser, models.Organization, models.RecruitmentBatch,
                          models.BatchCandidate, models.CandidateSelection,
                          models.MatchRun, models.UsageEvent, models.AuditLog,
                          models.JobCandidate, models.EvolutionRun)
        }
        second = seed.apply_showcase(db, include_feedback=False)
        assert counts == {
            model.__tablename__: db.query(model).count()
            for model in (models.AppUser, models.Organization, models.RecruitmentBatch,
                          models.BatchCandidate, models.CandidateSelection,
                          models.MatchRun, models.UsageEvent, models.AuditLog,
                          models.JobCandidate, models.EvolutionRun)
        }
        assert first["candidates"] == second["candidates"] == 60
        assert first["top_k_selections"] == 20
        assert first["personal_match_runs"] == 6
        assert first["failed_candidates"] == 3
        assert first["evaluation_included"] is False
        assert first["pii_stored"] is False
    finally:
        db.close()
