"""Employer/URL repair is conservative, idempotent and evolution-safe."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models
from app.db import Base
from data import backfill_employers_evidence as backfill


def _database():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    job = models.Job(name="回填测试岗位", slug="backfill-test", category="人工智能",
                     status="published", version=2, confidence=.23,
                     core_responsibilities=[], typical_scenarios=[])
    skill = models.Skill(name="Python", normalized_name="Python", category="编程语言")
    db.add_all([job, skill])
    db.flush()
    relation = models.JobSkill(
        job_id=job.id, skill_id=skill.id, status="active", importance="required",
        weight=.9, confidence=.23, source_count=0,
        factors={"support": .5, "diversity": 0, "freshness": .9,
                 "authority": .8, "external": 0})
    db.add(relation)
    db.flush()
    db.add(models.JobVersion(job_id=job.id, version=2, status="published"))
    db.add(models.CapabilityChange(
        job_id=job.id, version=2, change_type="modify", skill_name="Python",
        old_value={"weight": .7}, new_value={"weight": .9}))
    companies = (
        "网易有道信息技术（北京）有限公司",
        "杭州网易云音乐科技有限公司",
        "未知",
    )
    for index, company in enumerate(companies):
        raw = models.RawJD(
            job_title=job.name, company=company, raw_text="要求掌握 Python",
            source_url=(f"https://jobs.example/{index}" if index < 2 else ""),
            publish_date=datetime(2026, 8, 1), is_duplicate=False,
            dedup_hash=f"backfill-{index}")
        db.add(raw)
        db.flush()
        db.add(models.Evidence(
            job_skill_id=relation.id, raw_jd_id=raw.id, source_type="jd",
            source_url="", snippet=raw.raw_text))
    db.commit()
    return db


def test_backfill_groups_reviewed_subsidiaries_and_keeps_unknown_unknown():
    db = _database()
    try:
        before = backfill.dry_run(db)
        assert before["writes"] is False
        assert db.query(models.Employer).count() == 0
        result = backfill.apply_backfill(db)
        raws = db.query(models.RawJD).order_by(models.RawJD.id).all()
        assert raws[0].employer_id and raws[1].employer_id
        first = db.get(models.Employer, raws[0].employer_id)
        second = db.get(models.Employer, raws[1].employer_id)
        assert first.parent_id and first.parent_id == second.parent_id
        assert db.get(models.Employer, first.parent_id).name == "网易集团"
        assert raws[2].employer_id is None
        assert [row.source_url for row in db.query(models.Evidence).order_by(
            models.Evidence.id).all()] == [
                "https://jobs.example/0", "https://jobs.example/1", ""]
        assert result["jobs"][0]["before"]["employer_count"] == 0
        assert result["jobs"][0]["after"]["employer_count"] == 1
        assert result["protected_counts"]["capability_changes"] == 1

        counts = {model: db.query(model).count() for model in (
            models.Employer, models.EmployerAlias, models.JobVersion,
            models.CapabilityChange, models.Evidence)}
        replay = backfill.apply_backfill(db)
        assert replay["employer_updates"] == 0 and replay["url_updates"] == 0
        assert counts == {model: db.query(model).count() for model in counts}
    finally:
        db.close()
