"""Authentication boundary and evidence replay confidence regressions."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models
from app.auth import hash_password, token_hash
from app.db import Base, get_db
from app.main import app
from app.services.confidence_batch import next_scheduled_utc, run_confidence_recalculation


@pytest.fixture()
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    value = sessionmaker(bind=engine)()
    try:
        yield value
    finally:
        value.close()


@pytest.fixture()
def client(session, monkeypatch):
    app.dependency_overrides[get_db] = lambda: session
    monkeypatch.setattr("app.routers.chat.clients.chat_stream", lambda messages: iter(["测试回答"]))
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _actor(session, role: str, suffix: str, *, organization: bool = False) -> dict:
    raw = f"auth-{role}-{suffix}"
    user = models.AppUser(username=f"{role}-{suffix}", password_hash=hash_password("Password123!"),
                          role=role, status="active")
    session.add(user)
    session.flush()
    if organization:
        org = models.Organization(name=f"Auth Org {suffix}", status="active", created_by=user.id)
        session.add(org)
        session.flush()
        session.add(models.OrganizationMember(
            organization_id=org.id, user_id=user.id, role="hr", status="active"))
    session.add(models.UserSession(
        user_id=user.id, token_hash=token_hash(raw),
        expires_at=datetime.utcnow() + timedelta(hours=1)))
    session.commit()
    return {"Authorization": f"Bearer {raw}"}


def test_only_health_login_register_and_chat_are_public(client):
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/chat/suggestions").status_code == 200
    chat = client.post("/api/chat", json={"message": "你好"})
    assert chat.status_code == 200 and "测试回答" in chat.text
    registered = client.post("/api/auth/register", json={
        "username": "public-user", "password": "Password123!", "role": "user"})
    assert registered.status_code == 201
    assert client.post("/api/auth/login", json={
        "username": "public-user", "password": "Password123!"}).status_code == 200

    protected = (
        ("get", "/api/jobs"), ("get", "/api/graph/stats"),
        ("get", "/api/discovery/seeds"), ("get", "/api/evolution/1/changes"),
        ("post", "/api/match/analyze"), ("get", "/api/talent/corpus"),
        ("get", "/api/me/matches"), ("get", "/api/hr/recruitment-batches"),
        ("get", "/api/feedback"), ("get", "/api/admin/users"),
        ("get", "/api/auth/me"),
    )
    for method, path in protected:
        assert client.request(method, path).status_code == 401, path


def test_roles_are_fixed_and_permissions_are_enforced(client, session):
    assert client.post("/api/auth/register", json={
        "username": "guest-role", "password": "Password123!", "role": "guest"
    }).status_code == 422
    user_headers = _actor(session, "user", "permissions")
    hr_headers = _actor(session, "hr", "permissions", organization=True)
    admin_headers = _actor(session, "admin", "permissions")
    assert client.get("/api/admin/users", headers=user_headers).status_code == 403
    assert client.get("/api/hr/recruitment-batches", headers=user_headers).status_code == 403
    assert client.get("/api/hr/recruitment-batches", headers=hr_headers).status_code == 200
    permissions = client.get("/api/admin/permissions", headers=admin_headers).json()["items"]
    assert {item["role"] for item in permissions} == {"user", "hr", "admin"}

    invalid = models.AppUser(username="legacy-role", password_hash="unused",
                             role="guest", status="active")
    session.add(invalid)
    session.flush()
    session.add(models.UserSession(
        user_id=invalid.id, token_hash=token_hash("legacy-role-token"),
        expires_at=datetime.utcnow() + timedelta(hours=1)))
    session.commit()
    assert client.get("/api/jobs", headers={
        "Authorization": "Bearer legacy-role-token"}).status_code == 403


def _seed_confidence_job(session, *, name: str, employer_count: int,
                         authority: float, age_days: int, external: bool,
                         duplicate: bool = False) -> models.Job:
    job = models.Job(name=name, slug=name, category="人工智能", status="published",
                     version=1, confidence=.91, core_responsibilities=[], typical_scenarios=[])
    skill = models.Skill(name=f"{name}-技能", normalized_name=f"{name}-技能", category="人工智能")
    session.add_all([job, skill])
    session.flush()
    relation = models.JobSkill(job_id=job.id, skill_id=skill.id, status="active",
                               importance="required", weight=.8, confidence=.91)
    session.add(relation)
    session.flush()
    for index in range(employer_count):
        employer = models.Employer(name=f"{name}-雇主-{index}",
                                   normalized_name=f"{name}-employer-{index}", status="active")
        session.add(employer)
        session.flush()
        raw = models.RawJD(
            job_title=name, company=employer.name, employer_id=employer.id,
            source="official" if authority == 1 else "dataset", platform="company_site",
            source_authority=authority, raw_text=f"{name}要求掌握{name}-技能",
            publish_date=datetime(2026, 8, 20) - timedelta(days=age_days),
            dedup_hash=f"{name}-jd-{index}", is_duplicate=False)
        session.add(raw)
        session.flush()
        session.add(models.Evidence(
            job_skill_id=relation.id, raw_jd_id=raw.id, source_type="jd",
            source_url=f"https://evidence.test/{name}/{index}", snippet=raw.raw_text))
        # A repeated Evidence row must not increase support or evidence counts.
        session.add(models.Evidence(
            job_skill_id=relation.id, raw_jd_id=raw.id, source_type="jd",
            source_url=f"https://evidence.test/{name}/{index}", snippet=raw.raw_text))
        if duplicate and index == 0:
            copied = models.RawJD(
                job_title=name, company=employer.name, employer_id=employer.id,
                source="official", raw_text=raw.raw_text,
                publish_date=raw.publish_date, dedup_hash=raw.dedup_hash,
                is_duplicate=True, duplicate_of=raw.id)
            session.add(copied)
            session.flush()
            session.add(models.Evidence(
                job_skill_id=relation.id, raw_jd_id=copied.id, source_type="jd",
                snippet=copied.raw_text))
    version = models.JobVersion(job_id=job.id, version=1, status="published")
    session.add(version)
    session.flush()
    session.add(models.JobVersionSkill(
        job_version_id=version.id, skill_id=skill.id, status="active",
        importance="required", weight=.8, confidence=.91))
    if external:
        session.add(models.AuthorityEvidence(
            job_id=job.id, kind="policy", title=f"{name}政策", issuer="人社部",
            publish_date=datetime(2026, 8, 1), url=f"https://authority.test/{name}"))
    session.commit()
    return job


def test_confidence_replay_distribution_dedup_idempotency_and_history(session):
    high = _seed_confidence_job(session, name="高可信岗位", employer_count=3,
                                authority=1.0, age_days=0, external=True, duplicate=True)
    medium = _seed_confidence_job(session, name="中可信岗位", employer_count=2,
                                  authority=.7, age_days=90, external=False)
    low = _seed_confidence_job(session, name="低可信岗位", employer_count=1,
                               authority=.6, age_days=360, external=False)
    as_of = datetime(2026, 8, 27, 18, 30)
    first = run_confidence_recalculation(session, as_of=as_of, trigger="scheduled")
    replay = run_confidence_recalculation(session, as_of=as_of, trigger="scheduled")
    assert first["idempotent_replay"] is False
    assert replay["idempotent_replay"] is True
    assert session.query(models.ConfidenceRun).count() == 1
    assert session.query(models.JobConfidenceSnapshot).count() == 3
    session.refresh(high)
    session.refresh(medium)
    session.refresh(low)
    assert high.confidence > medium.confidence > low.confidence
    high_snapshot = session.query(models.JobConfidenceSnapshot).filter_by(job_id=high.id).one()
    assert high_snapshot.valid_jd_count == 3
    assert high_snapshot.evidence_count == 3
    assert set(high_snapshot.factors) == {
        "support", "diversity", "freshness", "authority", "external"}

    second = run_confidence_recalculation(
        session, as_of=as_of + timedelta(days=1), trigger="scheduled")
    assert second["idempotent_replay"] is False
    assert session.query(models.JobConfidenceSnapshot).filter_by(job_id=high.id).count() == 2
    latest = session.query(models.JobConfidenceSnapshot).filter_by(job_id=high.id).order_by(
        models.JobConfidenceSnapshot.as_of.desc()).first()
    assert latest.previous_confidence == high_snapshot.confidence


def test_feedback_count_does_not_change_confidence(session):
    first = _seed_confidence_job(session, name="反馈岗位A", employer_count=2,
                                 authority=.7, age_days=30, external=False)
    second = _seed_confidence_job(session, name="反馈岗位B", employer_count=2,
                                  authority=.7, age_days=30, external=False)
    owner = models.AppUser(username="feedback-owner", password_hash="unused",
                           role="user", status="active")
    session.add(owner)
    session.flush()
    for index in range(5):
        ticket = models.FeedbackTicket(
            owner_user_id=owner.id, target_type="job", target_id=str(first.id),
            status="submitted", current_revision=1)
        session.add(ticket)
        session.flush()
        session.add(models.FeedbackRevision(
            ticket_id=ticket.id, revision=1, category="test",
            content=f"反馈 {index}", evidence=[], created_by=owner.id))
    session.commit()
    run_confidence_recalculation(
        session, as_of=datetime(2026, 8, 27, 18, 30), trigger="manual")
    session.refresh(first)
    session.refresh(second)
    assert first.confidence == second.confidence


def test_next_run_is_daily_at_beijing_0230():
    assert next_scheduled_utc(datetime(2026, 8, 27, 18, 29, tzinfo=timezone.utc)) == datetime(
        2026, 8, 27, 18, 30)
    assert next_scheduled_utc(datetime(2026, 8, 27, 18, 30, tzinfo=timezone.utc)) == datetime(
        2026, 8, 28, 18, 30)


def test_confidence_stats_and_protected_history_contract(client, session):
    headers = _actor(session, "user", "confidence-contract")
    job = _seed_confidence_job(session, name="历史接口岗位", employer_count=3,
                               authority=1.0, age_days=0, external=True)
    run_confidence_recalculation(
        session, as_of=datetime(2026, 8, 27, 18, 30), trigger="scheduled")
    assert client.get(
        f"/api/jobs/{job.id}/confidence-history").status_code == 401
    history = client.get(
        f"/api/jobs/{job.id}/confidence-history", headers=headers).json()
    assert history["job_id"] == job.id and history["total"] == 1
    assert set(history["items"][0]["factors"]) == {
        "support", "diversity", "freshness", "authority", "external"}
    stats = client.get("/api/graph/stats", headers=headers).json()
    assert stats["confidence_as_of"] == "2026-08-27T18:30:00"
    assert "avg_confidence_delta" in stats
