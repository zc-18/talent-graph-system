"""Private workflow regressions using an isolated in-memory SQLite database."""
from __future__ import annotations

from datetime import datetime, timedelta
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import guards, models
from app.auth import token_hash
from app.db import Base, get_db
from app.main import app
from app.routers import hr as hr_router
from app.routers.hr import _batch_contract
from app.services.retention import cleanup_expired_resume_profiles


@pytest.fixture()
def session():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    value = sessionmaker(bind=engine)()
    try:
        yield value
    finally:
        value.close()


@pytest.fixture()
def client(monkeypatch, session):
    monkeypatch.setattr(guards.settings, "read_only", True, raising=False)
    app.dependency_overrides[get_db] = lambda: session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def make_actor(session, role: str, suffix: str, *, with_org: bool = False):
    user = models.AppUser(
        username=f"{role}-private-{suffix}", password_hash="unused",
        role=role, status="active")
    session.add(user)
    session.flush()
    org = None
    if with_org:
        org = models.Organization(
            name=f"Private Org {suffix}", status="active", created_by=user.id)
        session.add(org)
        session.flush()
        session.add(models.OrganizationMember(
            organization_id=org.id, user_id=user.id, role="hr", status="active"))
    raw = f"private-token-{role}-{suffix}"
    session.add(models.UserSession(
        user_id=user.id, token_hash=token_hash(raw),
        expires_at=datetime.utcnow() + timedelta(hours=1)))
    session.commit()
    return user, org, {"Authorization": f"Bearer {raw}"}


def seed_job(session):
    job = models.Job(
        name="私域后端工程师", slug="private-backend", category="云计算与工程",
        track="software", industry="internet", recruitment_type="social",
        level="middle", status="published", version=1,
        core_responsibilities=[], typical_scenarios=[])
    skill = models.Skill(name="Java", normalized_name="Java", category="编程语言")
    session.add_all([job, skill])
    session.flush()
    session.add(models.JobSkill(
        job_id=job.id, skill_id=skill.id, importance="required", weight=0.9,
        confidence=0.9, source_count=2, status="active"))
    session.commit()
    return job


def seed_batch(session, user, org, job, *, status="completed"):
    batch = models.RecruitmentBatch(
        organization_id=org.id, created_by=user.id, name="候选批次",
        target_job_id=job.id, target_job_version=1, status=status)
    session.add(batch)
    session.flush()
    return batch


def seed_candidate(session, batch, *, code: str, status: str = "succeeded",
                   skills: list[str] | None = None, score: float = 80.0):
    profile = None
    if status == "succeeded":
        profile = models.ResumeProfile(
            organization_id=batch.organization_id, code=code, source_type="batch",
            skills=skills or ["Java"], skill_levels={}, authorized=True,
            retention_expires_at=datetime.utcnow() + timedelta(days=30))
        session.add(profile)
        session.flush()
    candidate = models.BatchCandidate(
        batch_id=batch.id, resume_profile_id=profile.id if profile else None,
        file_hash=f"hash-{code}", display_code=code, parse_status=status,
        error_code="PARSE_FAILED" if status == "failed" else None,
        error_detail="简历解析失败" if status == "failed" else None,
        overall_score=score if status == "succeeded" else None,
        dimension_scores={} if status == "succeeded" else None,
        result_snapshot={} if status == "succeeded" else None)
    session.add(candidate)
    session.flush()
    return candidate, profile


def test_recruitment_batch_freezes_contract_before_job_evolves(client, session):
    _, _, headers = make_actor(session, "hr", "frozen-contract", with_org=True)
    job = seed_job(session)
    created = client.post("/api/hr/recruitment-batches", headers=headers, json={
        "name": "冻结契约批次", "target_job_id": job.id,
    })
    assert created.status_code == 201
    batch = session.get(models.RecruitmentBatch, created.json()["id"])
    frozen = deepcopy(batch.contract_snapshot)
    assert frozen["version"] == 1

    python = models.Skill(name="Python", normalized_name="Python", category="编程语言")
    session.add(python)
    session.flush()
    session.add(models.JobSkill(
        job_id=job.id, skill_id=python.id, importance="required", weight=1.0,
        confidence=1.0, source_count=3, status="active"))
    job.version = 2
    session.commit()

    assert _batch_contract(session, batch) == frozen
    assert batch.target_job_version == 1


def test_recruitment_worker_uses_frozen_contract_after_job_evolves(
        client, session, monkeypatch):
    user, _, headers = make_actor(session, "hr", "worker-frozen-contract", with_org=True)
    job = seed_job(session)
    created = client.post("/api/hr/recruitment-batches", headers=headers, json={
        "name": "后台冻结契约批次", "target_job_id": job.id,
    })
    batch = session.get(models.RecruitmentBatch, created.json()["id"])
    frozen = deepcopy(batch.contract_snapshot)
    job.version = 2
    session.commit()
    captured = []

    monkeypatch.setattr(
        hr_router.recruitment, "process_file",
        lambda db, row, filename, content, contract, retention_days, preset_error:
        captured.append(contract))
    hr_router._process_batch(
        session.get_bind(), batch.id, [("candidate.txt", b"Java", None)], 90, user.id)

    assert captured == [frozen]


def test_hr_generic_match_does_not_persist_unretained_resume(client, session):
    _, _, headers = make_actor(session, "hr", "generic-match", with_org=True)
    parsed = client.post("/api/match/resume/text", headers=headers, json={"text": "熟悉 Java"})
    assert parsed.status_code == 200
    assert parsed.json()["resume_id"] is None
    assert session.query(models.ResumeProfile).count() == 0

    job = seed_job(session)
    analyzed = client.post("/api/match/analyze", headers=headers, json={
        "job_id": job.id, "skills": ["Java"], "save": True,
    })
    assert analyzed.status_code == 422
    assert analyzed.json()["detail"]["code"] == "HR_BATCH_REQUIRED"
    assert session.query(models.MatchRun).count() == 0


def test_feedback_revision_comments_and_complete_timeline(client, session):
    _, _, user_headers = make_actor(session, "user", "feedback-user")
    _, _, admin_headers = make_actor(session, "admin", "feedback-admin")
    payload = {
        "target_type": "job", "target_id": "7", "category": "correction",
        "content": "初始反馈", "evidence": []}
    ticket_id = client.post("/api/feedback", headers=user_headers, json=payload).json()["id"]
    revised = client.patch(
        f"/api/feedback/{ticket_id}", headers=user_headers,
        json={**payload, "content": "补充后的反馈"})
    assert revised.status_code == 200

    triaged = client.post(
        f"/api/admin/feedback/{ticket_id}/review", headers=admin_headers,
        json={"action": "triage", "comment": "证据齐全，进入审核"})
    approved = client.post(
        f"/api/admin/feedback/{ticket_id}/review", headers=admin_headers,
        json={"action": "approve", "comment": "同意更正"})

    assert triaged.status_code == approved.status_code == 200
    detail = approved.json()
    assert [item["type"] for item in detail["timeline"]] == [
        "submitted", "revised", "triage", "approve"]
    assert [item["comment"] for item in detail["review_comments"]] == [
        "证据齐全，进入审核", "同意更正"]
    assert [item["revision"] for item in detail["revisions"]] == [1, 2]
    assert all(item["actor_username"] for item in detail["timeline"])
    assert session.query(models.FeedbackEvent).filter_by(ticket_id=ticket_id).count() == 4
    assert client.post(
        f"/api/admin/feedback/{ticket_id}/review", headers=admin_headers,
        json={"action": "triage"}).status_code == 409
    assert client.post(
        f"/api/admin/feedback/{ticket_id}/review", headers=admin_headers,
        json={"action": "apply", "applied_record_type": "job_version",
              "applied_record_id": "99999"}).status_code == 422


def test_candidate_scope_pagination_detail_delete_and_link_protection(client, session):
    user_a, org_a, headers_a = make_actor(session, "hr", "candidate-a", with_org=True)
    _, _, headers_b = make_actor(session, "hr", "candidate-b", with_org=True)
    job = seed_job(session)
    batch = seed_batch(session, user_a, org_a, job)
    first, first_profile = seed_candidate(session, batch, code="CAND-001", score=90)
    second, _ = seed_candidate(session, batch, code="CAND-002", score=80)
    protected, protected_profile = seed_candidate(
        session, batch, code="CAND-003", score=70)
    batch.total_count = batch.processed_count = batch.succeeded_count = 3
    team = models.Team(
        name="已选团队", organization_id=org_a.id, created_by=user_a.id,
        target_job_id=job.id)
    session.add(team)
    session.flush()
    session.add(models.CandidateSelection(
        batch_candidate_id=protected.id, team_id=team.id, selected_by=user_a.id))
    session.commit()

    page = client.get(
        f"/api/hr/recruitment-batches/{batch.id}/candidates?page=2&size=1",
        headers=headers_a)
    assert page.status_code == 200
    assert page.json()["total"] == 3
    assert [item["id"] for item in page.json()["items"]] == [second.id]
    assert client.get(
        f"/api/hr/recruitment-batches/{batch.id}/candidates",
        headers=headers_b).status_code == 404

    detail = client.get(
        f"/api/hr/recruitment-batches/{batch.id}/candidates/{first.id}",
        headers=headers_a)
    assert detail.status_code == 200
    assert detail.json()["skills"] == ["Java"]
    assert session.query(models.AuditLog).filter_by(
        action="recruitment.candidate.view", target_id=str(first.id)).count() == 1

    assert client.delete(
        f"/api/hr/recruitment-batches/{batch.id}/candidates/{protected.id}",
        headers=headers_a).status_code == 409
    deleted = client.delete(
        f"/api/hr/recruitment-batches/{batch.id}/candidates/{first.id}",
        headers=headers_a)
    assert deleted.status_code == 204
    assert session.get(models.BatchCandidate, first.id) is None
    assert session.get(models.ResumeProfile, first_profile.id) is None
    session.refresh(batch)
    assert (batch.total_count, batch.processed_count, batch.succeeded_count) == (2, 2, 2)
    assert session.get(models.ResumeProfile, protected_profile.id) is not None
    assert session.query(models.AuditLog).filter_by(
        action="recruitment.candidate.delete", target_id=str(first.id)).count() == 1


def test_expired_profile_cleanup_cascades_and_is_idempotent(session):
    user, org, _ = make_actor(session, "hr", "retention", with_org=True)
    job = seed_job(session)
    batch = seed_batch(session, user, org, job, status="completed")
    candidate, expired = seed_candidate(session, batch, code="EXPIRED", score=90)
    active = models.ResumeProfile(
        organization_id=org.id, code="ACTIVE", source_type="batch", skills=["Java"],
        skill_levels={}, authorized=True,
        retention_expires_at=datetime.utcnow() + timedelta(days=30))
    team = models.Team(
        name="留存团队", organization_id=org.id, created_by=user.id,
        target_job_id=job.id)
    session.add_all([active, team])
    session.flush()
    session.add_all([
        models.MatchRun(
            organization_id=org.id, resume_profile_id=expired.id,
            status="completed", result_snapshot={}),
        models.TeamMember(
            team_id=team.id, resume_profile_id=expired.id, display_name="候选A"),
        models.CandidateSelection(
            batch_candidate_id=candidate.id, team_id=team.id, selected_by=user.id),
    ])
    expired.retention_expires_at = datetime.utcnow() - timedelta(minutes=1)
    batch.total_count = batch.processed_count = batch.succeeded_count = 1
    session.commit()
    expired_id = expired.id
    active_id = active.id
    job_id = job.id

    report = cleanup_expired_resume_profiles(session, dry_run=False)
    session.commit()
    assert report["deleted"] == {
        "profiles": 1, "match_runs": 1, "batch_candidates": 1,
        "candidate_selections": 1, "team_members": 1}
    assert session.get(models.ResumeProfile, expired_id) is None
    assert session.get(models.ResumeProfile, active_id) is not None
    assert session.get(models.Job, job_id) is not None
    assert session.query(models.MatchRun).count() == 0
    assert session.query(models.BatchCandidate).count() == 0
    assert session.query(models.CandidateSelection).count() == 0
    assert session.query(models.TeamMember).count() == 0
    session.refresh(batch)
    assert (batch.total_count, batch.processed_count, batch.succeeded_count,
            batch.failed_count, batch.status) == (0, 0, 0, 0, "completed")

    second = cleanup_expired_resume_profiles(session, dry_run=False)
    session.commit()
    assert second["deleted"]["profiles"] == 0
    assert second["profile_ids"] == []


def test_failed_retry_and_confirmed_manual_correction_rerank(
        client, session, monkeypatch):
    user, org, headers = make_actor(session, "hr", "retry", with_org=True)
    job = seed_job(session)
    batch = seed_batch(session, user, org, job, status="completed_with_errors")
    failed, _ = seed_candidate(session, batch, code="FAILED", status="failed")
    other, _ = seed_candidate(session, batch, code="OTHER", score=80)
    other.rank = 1
    batch.total_count = batch.processed_count = 2
    batch.succeeded_count = batch.failed_count = 1
    session.commit()
    monkeypatch.setattr(
        "app.services.recruitment.resume.parse_resume",
        lambda text: {"skills": ["Java"], "skill_levels": {"Java": "proficient"},
                      "years_experience": 3, "education": "本科"})

    retried = client.post(
        f"/api/hr/recruitment-batches/{batch.id}/candidates/{failed.id}/retry",
        headers=headers, files={"file": ("replacement.txt", b"replacement", "text/plain")},
        data={"authorization_confirmed": "true", "retention_days": "30"})
    assert retried.status_code == 200
    assert retried.json()["candidate"]["status"] == "succeeded"
    assert retried.json()["candidate"]["error_code"] is None
    assert retried.json()["batch"]["status"] == "completed"
    assert retried.json()["batch"]["progress"] == {
        "total": 2, "processed": 2, "succeeded": 2, "failed": 0}
    assert retried.json()["candidate"]["rank"] == 1

    correction_url = (
        f"/api/hr/recruitment-batches/{batch.id}/candidates/{failed.id}/skills")
    assert client.patch(correction_url, headers=headers, json={
        "skills": ["Python"], "confirmed": False}).status_code == 422
    assert client.patch(correction_url, headers=headers, json={
        "skills": ["Java"], "skill_levels": {"Java": "13800138000"},
        "confirmed": True}).status_code == 422

    corrected = client.patch(correction_url, headers=headers, json={
        "skills": ["Python", "Python"], "skill_levels": {"Python": "proficient"},
        "confirmed": True, "note": "人工复核通过"})
    assert corrected.status_code == 200
    assert corrected.json()["skills"] == ["Python"]
    assert corrected.json()["skill_levels"] == {"Python": "proficient"}
    assert corrected.json()["note"] == "人工复核通过"
    assert corrected.json()["rank"] == 2
    session.refresh(other)
    assert other.rank == 1
    assert session.query(models.AuditLog).filter(
        models.AuditLog.action.in_({
            "recruitment.candidate.retry", "recruitment.candidate.correct"})).count() == 2
