"""Fourth-round backend domain/security regression tests (SQLite only)."""
from __future__ import annotations

from datetime import datetime, timedelta
import sys
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import guards, models
from app.auth import Actor, ROLE_PERMISSIONS, add_audit, token_hash
from app.db import Base, get_db
from app.main import app
from app.services import evolution, resume, role_contract
from data.seed_local_demo import _ensure_jobs


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


@pytest.fixture(autouse=True)
def clear_overrides():
    yield
    app.dependency_overrides.clear()


@pytest.fixture()
def client(monkeypatch, session):
    monkeypatch.setattr(guards.settings, "read_only", True, raising=False)
    monkeypatch.setattr(resume, "parse_resume", lambda text: {
        "skills": ["Java"] if "Java" in text else [],
        "skill_levels": {"Java": "proficient"} if "Java" in text else {},
        "years_experience": 0, "education": "", "candidate_name": "",
        "projects": [], "titles": [],
    })
    monkeypatch.setattr("app.services.extraction.parse_jd", lambda text: {
        "required_skills": [{"name": "Java", "level": "expert"}],
        "bonus_skills": [], "fine_skills": [],
    })
    app.dependency_overrides[get_db] = lambda: session
    user = models.AppUser(username="default-api-user", password_hash="unused",
                          role="user", status="active")
    session.add(user)
    session.flush()
    session.add(models.UserSession(
        user_id=user.id, token_hash=token_hash("default-api-token"),
        expires_at=datetime.utcnow() + timedelta(hours=1)))
    session.commit()
    value = TestClient(app)
    value.headers.update({"Authorization": "Bearer default-api-token"})
    return value


def make_actor(session, role: str, suffix: str, organization: bool = False):
    user = models.AppUser(username=f"{role}-{suffix}", password_hash="unused",
                          role=role, status="active")
    session.add(user)
    session.flush()
    org = None
    if organization:
        org = models.Organization(name=f"Org-{suffix}", status="active", created_by=user.id)
        session.add(org)
        session.flush()
        session.add(models.OrganizationMember(organization_id=org.id, user_id=user.id,
                                               role="hr", status="active"))
    raw = f"token-{role}-{suffix}"
    session.add(models.UserSession(user_id=user.id, token_hash=token_hash(raw),
                                   expires_at=datetime.utcnow() + timedelta(hours=1)))
    session.commit()
    return user, org, {"Authorization": f"Bearer {raw}"}


def test_local_demo_seed_populates_reconciled_version_snapshots(session):
    admin = models.AppUser(username="demo-seed-admin", password_hash="unused",
                           role="admin", status="active")
    session.add(admin)
    session.flush()
    _ensure_jobs(session, admin)
    session.commit()

    jobs = session.query(models.Job).order_by(models.Job.id).all()
    assert len(jobs) == 17
    for job in jobs:
        versions = session.query(models.JobVersion).filter_by(job_id=job.id).order_by(
            models.JobVersion.version).all()
        assert [row.version for row in versions] == list(range(1, job.version + 1))
        contract_ids = {row.contract_snapshot["contract_id"] for row in versions}
        assert len(contract_ids) == len(versions), job.name
        active_count = session.query(models.JobSkill).filter_by(
            job_id=job.id, status="active").count()
        for row in versions:
            contract = row.contract_snapshot
            assert contract["version"] == row.version
            assert contract["status"] == "ready", f"{job.name} v{row.version}"
            assert 8 <= contract["summary"]["cluster_count"] <= 12, job.name
            assert contract["evidence_window"] == row.evidence_window
            assert row.evidence_window["jd_count"] == active_count * 3
            assert row.evidence_window["employer_count"] == 3
            assert row.evidence_window["dimensions"] == {
                "job_name": job.name,
                "seniority": job.level,
                "recruitment_type": job.recruitment_type,
                "track": job.track,
                "industry": job.industry,
            }
            version_skills = (session.query(models.JobVersionSkill, models.Skill)
                              .join(models.Skill, models.Skill.id == models.JobVersionSkill.skill_id)
                              .filter(models.JobVersionSkill.job_version_id == row.id,
                                      models.JobVersionSkill.status == "active").all())
            assert len(version_skills) == active_count
            expected_weights = {skill.name: snapshot.weight
                                for snapshot, skill in version_skills}
            contract_weights = {
                skill["name"]: skill["weight"]
                for cluster in contract["clusters"]
                for skill in cluster["skills"]
            }
            assert contract_weights == expected_weights
        assert evolution.compute_snapshot_diff(
            evolution.current_capabilities(session, job),
            evolution.version_capabilities(session, versions[-1]),
        ) == []


def test_version_contract_revalidates_evidence_groups_and_preserves_dimensions(session):
    admin = models.AppUser(username="version-evidence-admin", password_hash="unused",
                           role="admin", status="active")
    session.add(admin)
    session.flush()
    _ensure_jobs(session, admin)
    session.flush()
    job = session.query(models.Job).filter_by(name="Java开发工程师").one()
    version = (session.query(models.JobVersion).filter_by(job_id=job.id, version=1).one())
    original_dimensions = {
        key: version.contract_snapshot[key]
        for key in ("job_name", "seniority", "recruitment_type", "track", "industry")
    }
    parent = models.Employer(name="演示母集团", normalized_name="demo-parent", status="active")
    session.add(parent)
    session.flush()
    employers = session.query(models.Employer).filter(
        models.Employer.normalized_name.like("demo-employer-%")).all()
    for employer in employers:
        employer.parent_id = parent.id
    first_snapshot = (session.query(models.JobVersionSkill)
                      .filter_by(job_version_id=version.id).order_by(
                          models.JobVersionSkill.id).first())
    first_raw_id = first_snapshot.evidence_refs[0]["raw_jd_id"]
    session.query(models.RawJD).filter_by(id=first_raw_id).one().is_duplicate = True
    first_snapshot.evidence_refs = [*first_snapshot.evidence_refs,
                                    {"raw_jd_id": 999999, "url": "missing"}]
    version.evidence_window = {"jd_count": 999, "employer_count": 999}
    job.name = "被重命名的当前岗位"
    job.level = "senior"
    job.recruitment_type = "campus"
    job.track = "hardware"
    job.industry = "manufacturing"
    session.flush()

    rebuilt = role_contract.build_contract_from_version(session, job, version)

    assert rebuilt["status"] == "evidence_insufficient"
    assert {key: rebuilt[key] for key in original_dimensions} == original_dimensions
    assert version.evidence_window["jd_count"] == 29
    assert version.evidence_window["employer_count"] == 1
    assert version.evidence_window["dimensions"] == original_dimensions
    assert rebuilt["evidence_window"] == version.evidence_window

    parent.status = "disabled"
    disabled = role_contract.build_contract_from_version(session, job, version)
    assert disabled["status"] == "evidence_insufficient"
    assert disabled["evidence_window"]["jd_count"] == 0
    assert disabled["evidence_window"]["employer_count"] == 0


def seed_job(session, name="Java开发工程师"):
    job = models.Job(name=name, slug=name.lower(), category="云计算与工程",
                     track="software", industry="internet", recruitment_type="social",
                     level="middle", status="published", version=1,
                     core_responsibilities=[], typical_scenarios=[])
    skill = models.Skill(name="Java", normalized_name="Java", category="编程语言")
    session.add_all([job, skill])
    session.flush()
    session.add(models.JobSkill(job_id=job.id, skill_id=skill.id, importance="required",
                                weight=.9, confidence=.9, source_count=2, status="active"))
    session.commit()
    return job


PUBLISHABLE_SKILLS = [
    "Java", "Spring", "MySQL", "消息队列", "功能测试", "Selenium", "Docker", "机器学习",
]


def seed_discovery_candidate(session, owner, definition, *, verdict="EMERGING",
                             evidence_snapshot=None):
    run = models.DiscoveryRun(
        owner_user_id=owner.id, organization_id=None, query=definition["job_title"],
        evidence_snapshot=evidence_snapshot or [],
        signal_snapshot={"source_verdict": verdict, "emergence_score": .82},
        conclusion="NEW")
    session.add(run)
    session.flush()
    candidate = models.JobCandidate(
        discovery_run_id=run.id, owner_user_id=owner.id,
        organization_id=None, status="submitted", current_revision=1)
    session.add(candidate)
    session.flush()
    session.add(models.JobCandidateRevision(
        candidate_id=candidate.id, revision=1, definition=definition,
        change_note="test", created_by=owner.id))
    session.commit()
    return candidate


def seed_raw_jds(session, employer_count=2, jd_count_per_employer=1):
    rows = []
    content = "任职要求：" + "、".join(PUBLISHABLE_SKILLS)
    for employer_index in range(employer_count):
        employer = models.Employer(
            name=f"证据企业{employer_index}", normalized_name=f"evidence-{employer_index}",
            status="active")
        session.add(employer)
        session.flush()
        for jd_index in range(jd_count_per_employer):
            row = models.RawJD(
                job_title="新型复合工程师", raw_text=content,
                company=employer.name, employer_id=employer.id,
                source="official", platform="career-site",
                source_url=f"https://trusted.test/{employer_index}/{jd_index}",
                publish_date=datetime(2026, 8, employer_index + jd_index + 1),
                is_duplicate=False)
            session.add(row)
            rows.append(row)
    session.flush()
    return rows


def candidate_definition(raw_jd_ids, *, include_candidate=False):
    capabilities = [{
        "name": name, "status": "active",
        "importance": "required" if index < 6 else "bonus",
        "weight": .8, "confidence": .85,
        "employer_count": 999, "source_count": 999,
        "evidence": [{"raw_jd_id": raw_jd_id, "snippet": "forged",
                      "source_url": "https://forged.test"}
                     for raw_jd_id in raw_jd_ids],
    } for index, name in enumerate(PUBLISHABLE_SKILLS)]
    if include_candidate:
        capabilities.append({
            "name": "未验证能力", "status": "candidate", "importance": "required",
            "employer_count": 999, "source_count": 999,
            "evidence": [{"raw_jd_id": raw_jd_ids[0]}] if raw_jd_ids else [],
        })
    return {
        "job_title": "新型复合工程师", "category": "人工智能", "level": "middle",
        "track": "product", "summary": "经审核的新岗位",
        "core_responsibilities": ["复合系统交付"], "typical_scenarios": ["产业应用"],
        "emergence_verdict": "EMERGING", "emergence_score": 99,
        "confidence": 1.0,
        "source_summary": {"evidence_count": 999},
        "authority_evidence": [{"kind": "policy", "title": "伪造政策"}],
        "capabilities": capabilities,
    }


def test_register_login_logout_uses_revocable_session(client, session):
    body = {"username": "Alice", "password": "correct-horse", "role": "user"}
    registered = client.post("/api/auth/register", json=body)
    assert registered.status_code == 201
    token = registered.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/auth/me", headers=headers).json()["username"] == "alice"
    assert client.post("/api/auth/logout", headers=headers).status_code == 204
    assert client.get("/api/auth/me", headers=headers).status_code == 401
    assert session.query(models.UserSession).first().token_hash != token


def test_auth_validation_hr_registration_and_login_audit(client, session):
    assert client.post("/api/auth/register", json={
        "username": "  a", "password": "correct-horse", "role": "user"}).status_code == 422
    assert client.post("/api/auth/register", json={
        "username": "talent-hr", "password": "correct-horse", "role": "hr"}).status_code == 422

    body = {"username": "Talent-HR", "password": "correct-horse", "role": "hr",
            "organization_name": "人才科技"}
    registered = client.post("/api/auth/register", json=body)
    assert registered.status_code == 201
    assert registered.json()["user"]["organization_id"] is not None
    assert session.query(models.OrganizationMember).count() == 1
    assert client.post("/api/auth/register", json=body).status_code == 409
    assert client.post("/api/auth/register", json={
        **body, "username": "another-hr"}).status_code == 409

    denied = client.post("/api/auth/login", json={
        "username": "talent-hr", "password": "wrong-password"})
    assert denied.status_code == 401
    logged_in = client.post("/api/auth/login", json={
        "username": "TALENT-HR", "password": "correct-horse"})
    assert logged_in.status_code == 200
    assert logged_in.json()["user"]["role"] == "hr"
    assert session.query(models.AuditLog).filter_by(
        action="auth.login", result="denied").count() == 1
    assert session.query(models.UsageEvent).filter_by(feature="login").count() == 2


def test_disabled_organization_removes_hr_context_and_blocks_saved_match(client, session):
    _, org, headers = make_actor(session, "hr", "disabled-org", organization=True)
    org.status = "disabled"
    session.commit()

    me = client.get("/api/auth/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["organization_id"] is None

    parsed = client.post("/api/match/resume/text", headers=headers,
                         json={"text": "Java backend developer"})
    assert parsed.status_code == 200 and parsed.json()["resume_id"] is None
    response = client.post("/api/match/analyze", headers=headers, json={
        "target_job_text": "Java 开发工程师", "skills": ["Java"],
        "generate_suggestions": False, "save": True})
    assert response.status_code == 403
    assert session.query(models.MatchRun).count() == 0
    assert session.query(models.ResumeProfile).count() == 0


def test_admin_cannot_create_unowned_saved_match(client, session):
    _, _, headers = make_actor(session, "admin", "orphan-match")

    parsed = client.post("/api/match/resume/text", headers=headers,
                         json={"text": "Java backend developer"})
    assert parsed.status_code == 200 and parsed.json()["resume_id"] is None
    response = client.post("/api/match/analyze", headers=headers, json={
        "target_job_text": "Java 开发工程师", "skills": ["Java"],
        "generate_suggestions": False, "save": True})
    assert response.status_code == 403
    assert session.query(models.MatchRun).count() == 0
    assert session.query(models.ResumeProfile).count() == 0


def test_read_only_blocks_authenticated_admin_public_write(client, session):
    _, _, headers = make_actor(session, "admin", "readonly")
    response = client.post("/api/jobs", headers=headers, json={
        "name": "公共岗位", "category": "人工智能", "level": "middle",
        "core_responsibilities": [], "typical_scenarios": [],
        "required_skills": [], "bonus_skills": []})
    assert response.status_code == 403
    assert session.query(models.Job).count() == 0


def test_legacy_direct_job_writes_are_retired_in_write_mode(client, session):
    _, _, headers = make_actor(session, "admin", "retired-direct-write")
    job = seed_job(session)
    before = {
        "jobs": session.query(models.Job).count(),
        "skills": session.query(models.JobSkill).count(),
        "versions": session.query(models.JobVersion).count(),
        "changes": session.query(models.CapabilityChange).count(),
        "job_version": job.version,
        "job_status": job.status,
    }
    guards.settings.read_only = False

    created = client.post("/api/jobs", headers=headers, json={
        "name": "绕过审核的新岗位", "category": "人工智能", "level": "middle",
        "core_responsibilities": [], "typical_scenarios": [],
        "required_skills": [], "bonus_skills": []})
    edited = client.post("/api/jobs/manual-edit", headers=headers, json={
        "job_id": job.id, "skill_name": "Spring", "action": "add",
        "importance": "required", "weight": .8})
    archived = client.delete(f"/api/jobs/{job.id}", headers=headers)

    assert created.status_code == 410
    assert created.json()["detail"]["workflow"] == "/api/discovery/runs"
    assert edited.status_code == 410
    assert edited.json()["detail"]["workflow"] == "/api/admin/evolution-runs"
    assert archived.status_code == 410
    assert archived.json()["detail"]["workflow"] == "/api/admin/evolution-runs"
    session.refresh(job)
    assert {
        "jobs": session.query(models.Job).count(),
        "skills": session.query(models.JobSkill).count(),
        "versions": session.query(models.JobVersion).count(),
        "changes": session.query(models.CapabilityChange).count(),
        "job_version": job.version,
        "job_status": job.status,
    } == before


def test_list_endpoints_enforce_bounded_stable_pagination(client, session):
    job = seed_job(session)
    session.add_all([
        models.TalentProfile(code="PAGE-001", source_type="sample", skills=[]),
        models.TalentProfile(code="PAGE-002", source_type="sample", skills=[]),
        models.Team(name="Page team 1"),
        models.Team(name="Page team 2"),
        models.SkillAlias(alias="jvm-one", canonical="Java", talent_count=2),
        models.SkillAlias(alias="jvm-two", canonical="Java", talent_count=1),
        models.CapabilityChange(job_id=job.id, version=1, change_type="add",
                                skill_name="Spring", confidence=.8),
        models.CapabilityChange(job_id=job.id, version=2, change_type="modify",
                                skill_name="Java", confidence=.9),
    ])
    session.commit()

    jobs = client.get("/api/jobs?page=0&size=0").json()
    profiles = client.get("/api/talent/profiles?page=-3&size=-8").json()
    teams = client.get("/api/talent/teams?page=2&size=1").json()
    aliases = client.get("/api/talent/aliases?page=2&limit=1").json()
    changes = client.get(f"/api/evolution/{job.id}/changes?page=2&size=1").json()

    assert (jobs["page"], jobs["size"], len(jobs["items"])) == (1, 1, 1)
    assert (profiles["page"], profiles["size"], profiles["total"],
            len(profiles["items"])) == (1, 1, 2, 1)
    assert (teams["page"], teams["size"], teams["total"],
            len(teams["items"])) == (2, 1, 2, 1)
    assert (aliases["page"], aliases["size"], aliases["total"],
            len(aliases["items"])) == (2, 1, 2, 1)
    assert (changes["page"], changes["size"], changes["total"],
            len(changes["items"])) == (2, 1, 2, 1)


def test_read_only_allows_user_match_history_with_fixed_version(client, session):
    user, _, headers = make_actor(session, "user", "match")
    job = seed_job(session)
    response = client.post("/api/match/analyze", headers=headers, json={
        "job_id": job.id, "skills": ["Java"], "skill_levels": {},
        "generate_suggestions": False, "save": True})
    assert response.status_code == 200
    history = client.get("/api/me/matches", headers=headers).json()
    assert history["total"] == 1
    assert history["items"][0]["job_version"] == 1
    row = session.query(models.MatchRun).one()
    assert row.owner_user_id == user.id and row.contract_snapshot


def test_match_uses_requested_seniority_and_recruitment_slice(client, session):
    _, _, headers = make_actor(session, "user", "slice")
    job = seed_job(session, "切片岗位")
    python = models.Skill(name="Python", normalized_name="Python", category="编程语言")
    management = models.Skill(name="项目管理", normalized_name="项目管理", category="软技能")
    session.add_all([python, management])
    session.flush()
    session.add_all([
        models.JobLevelSkill(job_id=job.id, level="junior", recruitment_type="campus",
                             track="software", industry="internet", skill_id=python.id,
                             importance="required", weight=.8, confidence=.9,
                             source_count=2, jd_count=20),
        models.JobLevelSkill(job_id=job.id, level="senior", recruitment_type="social",
                             track="software", industry="internet", skill_id=management.id,
                             importance="required", weight=.9, confidence=.9,
                             source_count=2, jd_count=20),
    ])
    session.commit()
    junior_response = client.post("/api/match/analyze", headers=headers, json={
        "job_id": job.id, "seniority": "junior", "recruitment_type": "campus",
        "track": "software", "industry": "internet", "skills": ["Python"],
        "generate_suggestions": False, "save": True}).json()
    junior = junior_response["contract"]
    senior = client.post("/api/match/analyze", headers=headers, json={
        "job_id": job.id, "seniority": "senior", "recruitment_type": "social",
        "track": "software", "industry": "internet", "skills": ["项目管理"],
        "generate_suggestions": False, "save": False}).json()["contract"]
    assert junior["slice_source"] == senior["slice_source"] == "job_level_skill"
    assert junior["seniority"] == "junior" and junior["recruitment_type"] == "campus"
    assert senior["seniority"] == "senior" and senior["recruitment_type"] == "social"
    assert {c["name"] for c in junior["clusters"]} != {c["name"] for c in senior["clusters"]}

    # Historical matching must retain the selected slice and version instead of
    # rebuilding from a later current job profile.
    job.version = 2
    session.commit()
    history = client.get(
        f"/api/me/matches/{junior_response['match_id']}", headers=headers).json()
    assert history["job_version"] == 1
    assert history["contract_snapshot"] == junior
    assert {key: history["contract_snapshot"][key] for key in (
        "seniority", "recruitment_type", "track", "industry", "slice_source")} == {
            "seniority": "junior", "recruitment_type": "campus",
            "track": "software", "industry": "internet",
            "slice_source": "job_level_skill"}


def test_cross_user_history_is_hidden(client, session):
    user_a, _, headers_a = make_actor(session, "user", "a")
    user_b, _, headers_b = make_actor(session, "user", "b")
    row = models.MatchRun(owner_user_id=user_b.id, job_id=None, job_version=None,
                          status="completed", contract_snapshot={"job_name": "临时岗位"},
                          result_snapshot={"overall_score": 88}, learning_path=[])
    session.add(row)
    session.commit()
    assert client.get(f"/api/me/matches/{row.id}", headers=headers_a).status_code == 404
    assert client.get(f"/api/me/matches/{row.id}", headers=headers_b).status_code == 200


def test_hr_private_batch_writes_in_read_only_and_is_org_scoped(client, session, monkeypatch):
    _, org_a, headers_a = make_actor(session, "hr", "a", organization=True)
    _, _, headers_b = make_actor(session, "hr", "b", organization=True)
    job = seed_job(session)
    created = client.post("/api/hr/recruitment-batches", headers=headers_a, json={
        "name": "后端候选批次", "target_job_id": job.id, "idempotency_key": "batch-a"})
    assert created.status_code == 201
    batch_id = created.json()["id"]
    monkeypatch.setattr(resume, "parse_resume", lambda text: {
        "skills": ["Java"], "skill_levels": {"Java": "proficient"},
        "years_experience": 3, "education": "本科"})
    uploaded = client.post(
        f"/api/hr/recruitment-batches/{batch_id}/files", headers=headers_a,
        files=[("files", ("candidate.txt", b"Java backend developer", "text/plain"))],
        data={"authorization_confirmed": "true", "retention_days": "30"})
    assert uploaded.status_code == 200
    assert uploaded.json()["status"] == "queued"
    latest = client.get(f"/api/hr/recruitment-batches/{batch_id}", headers=headers_a).json()
    assert latest["progress"] == {"total": 1, "processed": 1,
                                  "succeeded": 1, "failed": 0}
    assert session.query(models.ResumeProfile).one().organization_id == org_a.id
    assert client.get(f"/api/hr/recruitment-batches/{batch_id}",
                      headers=headers_b).status_code == 404


def test_hr_progress_keeps_upload_total_while_candidates_materialize(session):
    from app.routers.hr import _refresh_counts

    hr, org, _ = make_actor(session, "hr", "progress", organization=True)
    job = seed_job(session, "异步进度岗位")
    batch = models.RecruitmentBatch(
        organization_id=org.id, created_by=hr.id, name="24人批次",
        target_job_id=job.id, target_job_version=1, status="processing",
        total_count=24)
    session.add(batch)
    session.flush()
    session.add(models.BatchCandidate(
        batch_id=batch.id, file_hash="candidate-1", display_code="CAND-001",
        parse_status="succeeded", overall_score=80, dimension_scores={},
        result_snapshot={}))
    session.flush()

    _refresh_counts(session, batch)

    assert batch.total_count == 24
    assert batch.processed_count == batch.succeeded_count == 1


def test_hr_batch_counts_same_content_with_distinct_names_and_replays_idempotently(
        client, session, monkeypatch):
    _, _, headers = make_actor(session, "hr", "file-identity", organization=True)
    job = seed_job(session, "文件身份岗位")
    created = client.post("/api/hr/recruitment-batches", headers=headers, json={
        "name": "文件身份批次", "target_job_id": job.id})
    batch_id = created.json()["id"]
    monkeypatch.setattr(resume, "parse_resume", lambda text: {
        "skills": ["Java"], "skill_levels": {}, "years_experience": 2,
        "education": "本科"})

    uploaded = client.post(
        f"/api/hr/recruitment-batches/{batch_id}/files", headers=headers,
        files=[
            ("files", ("candidate-a.txt", b"same resume template", "text/plain")),
            ("files", ("candidate-b.txt", b"same resume template", "text/plain")),
        ], data={"authorization_confirmed": "true", "retention_days": "30"})
    assert uploaded.status_code == 200
    terminal = client.get(
        f"/api/hr/recruitment-batches/{batch_id}", headers=headers).json()
    assert terminal["status"] == "completed"
    assert terminal["progress"] == {
        "total": 2, "processed": 2, "succeeded": 2, "failed": 0}

    replay = client.post(
        f"/api/hr/recruitment-batches/{batch_id}/files", headers=headers,
        files=[("files", ("candidate-a.txt", b"same resume template", "text/plain"))],
        data={"authorization_confirmed": "true", "retention_days": "30"})
    assert replay.status_code == 200
    assert replay.json()["idempotent_replay"] is True
    assert replay.json()["progress"] == terminal["progress"]


def test_hr_completed_with_errors_ranking_and_idempotent_selection(
        client, session, monkeypatch):
    hr, org, headers = make_actor(session, "hr", "ranking", organization=True)
    job = seed_job(session, "平台工程师")
    payload = {"name": "平台候选批次", "target_job_id": job.id,
               "idempotency_key": "ranking-batch"}
    created = client.post("/api/hr/recruitment-batches", headers=headers, json=payload)
    assert created.status_code == 201
    replay = client.post("/api/hr/recruitment-batches", headers=headers, json=payload)
    assert replay.status_code == 201 and replay.json()["idempotent_replay"] is True

    batch = session.get(models.RecruitmentBatch, created.json()["id"])
    profile = models.ResumeProfile(
        organization_id=org.id, code="CAND-001", source_type="upload",
        skills=["Java"], skill_levels={"Java": "proficient"},
        years_experience=4, authorized=True)
    session.add(profile)
    session.flush()
    succeeded = models.BatchCandidate(
        batch_id=batch.id, resume_profile_id=profile.id, file_hash="ok-hash",
        display_code="CAND-001", parse_status="succeeded", overall_score=91,
        dimension_scores={"required": 95}, result_snapshot={}, rank=1)
    failed = models.BatchCandidate(
        batch_id=batch.id, file_hash="failed-hash", display_code="CAND-002",
        parse_status="failed", error_code="CORRUPT_FILE",
        error_detail="文件损坏")
    session.add_all([succeeded, failed])
    batch.status = "completed_with_errors"
    batch.total_count = batch.processed_count = 2
    batch.succeeded_count, batch.failed_count = 1, 1
    session.commit()

    listed = client.get(
        "/api/hr/recruitment-batches?status=completed_with_errors", headers=headers).json()
    assert listed["total"] == 1 and listed["items"][0]["failures"][0]["code"] == "CAND-002"
    ranking = client.get(
        f"/api/hr/recruitment-batches/{batch.id}/ranking?min_score=90", headers=headers).json()
    assert ranking["total"] == 1 and ranking["items"][0]["candidate_id"] == succeeded.id
    assert client.post(f"/api/hr/recruitment-batches/{batch.id}/select", headers=headers,
                       json={"candidate_ids": [failed.id]}).status_code == 422

    def fake_gap(db, team_id, job_id):
        member_count = db.query(models.TeamMember).filter_by(team_id=team_id).count()
        return {"coverage_rate": .75 if member_count else .25}

    monkeypatch.setattr("app.routers.hr.talent_service.team_gap", fake_gap)
    selected = client.post(f"/api/hr/recruitment-batches/{batch.id}/select", headers=headers,
                           json={"candidate_ids": [succeeded.id]})
    assert selected.status_code == 200
    assert selected.json()["selected"] == 1 and selected.json()["coverage_delta"] == .5
    team_id = selected.json()["team_id"]
    replay_selection = client.post(
        f"/api/hr/recruitment-batches/{batch.id}/select", headers=headers,
        json={"candidate_ids": [succeeded.id], "team_id": team_id})
    assert replay_selection.status_code == 200
    assert replay_selection.json()["selected"] == 0
    assert session.query(models.TeamMember).filter_by(team_id=team_id).count() == 1
    selection = session.query(models.CandidateSelection).one()
    assert selection.before_coverage == .25 and selection.after_coverage == .75


def test_discovery_revision_review_and_transactional_publish(client, session, monkeypatch):
    _, _, user_headers = make_actor(session, "user", "candidate")
    _, _, admin_headers = make_actor(session, "admin", "candidate")
    monkeypatch.setattr("app.routers.discovery.discovery.discover_candidates", lambda keyword, **kwargs: {
        "keyword": keyword, "verdict": "EMERGING", "evidence": [{"title": "e", "content": "c"}],
        "resolution": {"canonical_title": keyword, "track": "algorithm",
                       "industry": "general", "seniority": "middle",
                       "recruitment_type": "social"}, "signals": {"authority_strength": 1}})
    definition = {"job_title": "具身智能应用技术员", "category": "人工智能",
                  "level": "middle", "track": "algorithm", "summary": "新岗位",
                  "core_responsibilities": ["应用"], "typical_scenarios": ["机器人"],
                  "capabilities": [{"name": "Python", "importance": "required",
                                    "weight": .9, "confidence": .9, "source_count": 2,
                                    "status": "active"}]}
    monkeypatch.setattr("app.routers.discovery.discovery.define_new_job",
                        lambda keyword, evidence: definition)
    monkeypatch.setattr("app.routers.admin.discovery_service.candidate_publishability",
                        lambda db, candidate, value: {
                            "publishable": True, "reasons": [],
                            "active_capability_count": 1, "contract_cluster_count": 1,
                            "emergence_score": .8,
                            "validated_capabilities": value["capabilities"],
                            "validated_authority_evidence": [],
                            "evidence_window": {"start": None, "end": None},
                            "discovery_evidence_count": 1})
    run = client.post("/api/discovery/runs", headers=user_headers,
                      json={"keyword": "具身智能应用技术员", "idempotency_key": "discover-1"})
    assert run.status_code == 201 and run.json()["classification"] == "NEW"
    candidate_id = run.json()["candidate_id"]
    revised = {**definition, "summary": "修订后的新岗位"}
    assert client.patch(f"/api/discovery/candidates/{candidate_id}", headers=user_headers,
                        json={"definition": revised, "change_note": "专家修订"}).status_code == 200
    assert client.post(f"/api/discovery/candidates/{candidate_id}/submit",
                       headers=user_headers).status_code == 200
    blocked = client.post(f"/api/admin/candidates/{candidate_id}/review", headers=admin_headers,
                          json={"action": "approve", "comment": "通过", "publish": True})
    assert blocked.status_code == 403 and session.query(models.Job).count() == 0

    guards.settings.read_only = False
    published = client.post(f"/api/admin/candidates/{candidate_id}/review", headers=admin_headers,
                            json={"action": "approve", "comment": "通过", "publish": True})
    assert published.status_code == 200
    job = session.query(models.Job).one()
    assert job.version == 1
    version = session.query(models.JobVersion).filter_by(job_id=job.id, version=1).one()
    assert version.contract_snapshot["version"] == 1
    assert version.contract_snapshot["contract_id"]
    assert "clusters" in version.contract_snapshot and "summary" in version.contract_snapshot
    assert session.query(models.JobVersionSkill).count() == 1


def test_established_discovery_resolves_formal_job_name_and_replays_contract(
        client, session, monkeypatch):
    _, _, headers = make_actor(session, "user", "established")
    job = seed_job(session)
    job.slug = "demo-job-1"
    job.version = 2
    session.commit()
    monkeypatch.setattr("app.routers.discovery.discovery.discover_candidates", lambda keyword, **kwargs: {
        "keyword": keyword, "verdict": "ESTABLISHED",
        "existing_job": "Java开发工程师", "evidence": [],
        "resolution": {"canonical_title": "Java开发工程师", "track": "software"},
        "signals": {"mature_veto": True},
    })
    payload = {"keyword": "初级 Java 开发工程师", "idempotency_key": "established-1"}

    created = client.post("/api/discovery/runs", headers=headers, json=payload)
    replayed = client.post("/api/discovery/runs", headers=headers, json=payload)

    assert created.status_code == replayed.status_code == 201
    expected = {"id": job.id, "name": job.name, "version": 2}
    assert created.json()["matched_job"] == expected
    assert created.json()["run"]["matched_job_id"] == job.id
    assert replayed.json()["idempotent_replay"] is True
    assert replayed.json()["matched_job"] == expected
    assert created.json()["evolution_run_id"] == replayed.json()["evolution_run_id"]
    assert session.query(models.EvolutionRun).count() == 1


def test_established_job_evolution_publishes_reconciled_v2_across_reads(
        client, session, monkeypatch):
    _, _, user_headers = make_actor(session, "user", "evolution-e2e")
    _, _, admin_headers = make_actor(session, "admin", "evolution-e2e")
    job = seed_job(session)
    python = models.Skill(name="Python", normalized_name="Python", category="编程语言")
    session.add(python)
    session.flush()
    session.add(models.JobSkill(
        job_id=job.id, skill_id=python.id, importance="required", weight=.7,
        confidence=.8, source_count=2, status="active"))
    session.commit()
    monkeypatch.setattr("app.routers.discovery.discovery.discover_candidates", lambda keyword, **kwargs: {
        "keyword": keyword, "verdict": "ESTABLISHED",
        "existing_job": job.name, "evidence": [{"title": "Java market update"}],
        "resolution": {"canonical_title": job.name, "track": "software"},
        "signals": {"mature_veto": True},
    })

    guest = client.post("/api/discovery/discover", headers={"Authorization": ""},
                        json={"keyword": "Java", "save": True})
    assert guest.status_code == 401
    preview = client.post("/api/discovery/discover", headers=user_headers,
                          json={"keyword": "Java", "save": True})
    assert preview.status_code == 200
    assert preview.json()["conflict"]["job_id"] == job.id
    assert session.query(models.EvolutionRun).count() == 0
    discovered = client.post("/api/discovery/runs", headers=user_headers, json={
        "keyword": "Java", "idempotency_key": "java-to-evolution"})
    assert discovered.status_code == 201
    run_id = discovered.json()["evolution_run_id"]
    assert run_id is not None
    listed = client.get("/api/admin/evolution-runs?page=1&size=1",
                        headers=admin_headers).json()
    assert listed["total"] == 1 and listed["items"][0]["id"] == run_id

    proposal = {"capabilities": [
        {"name": "Java", "importance": "required", "weight": .75,
         "confidence": .9, "source_count": 2, "level_required": "familiar"},
        {"name": "Spring", "importance": "required", "weight": .8,
         "confidence": .85, "source_count": 2, "level_required": "proficient",
         "category": "后端框架"},
    ]}
    proposed = client.post(f"/api/admin/evolution-runs/{run_id}/propose",
                           headers=admin_headers,
                           json={"proposed_snapshot": proposal})
    assert proposed.status_code == 200
    assert proposed.json()["status"] == "proposed"
    assert {(item["change_type"], item["skill_name"])
            for item in proposed.json()["diff"]} == {
                ("modify", "Java"), ("delete", "Python"), ("add", "Spring")}
    rejected = client.post(f"/api/admin/evolution-runs/{run_id}/review",
                           headers=admin_headers,
                           json={"action": "reject", "comment": "补充权重解释"})
    assert rejected.status_code == 200 and rejected.json()["status"] == "rejected"
    assert client.post(f"/api/admin/evolution-runs/{run_id}/propose",
                       headers=admin_headers,
                       json={"proposed_snapshot": proposal}).status_code == 200
    approved = client.post(f"/api/admin/evolution-runs/{run_id}/review",
                           headers=admin_headers,
                           json={"action": "approve", "comment": "证据充分"})
    assert approved.status_code == 200 and approved.json()["status"] == "approved"
    assert [item["action"] for item in approved.json()["reviews"]] == ["reject", "approve"]

    blocked = client.post(f"/api/admin/evolution-runs/{run_id}/publish",
                          headers=admin_headers)
    assert blocked.status_code == 403
    session.refresh(job)
    assert job.version == 1 and session.query(models.JobVersion).count() == 0

    guards.settings.read_only = False
    published = client.post(f"/api/admin/evolution-runs/{run_id}/publish",
                            headers=admin_headers)
    assert published.status_code == 200
    assert published.json()["job"]["version"] == 2
    session.refresh(job)
    assert job.version == 2
    versions = client.get(f"/api/jobs/{job.id}/versions", headers=user_headers).json()
    assert versions["total"] == 2
    assert [item["version"] for item in versions["items"]] == [2, 1]
    assert all(item["contract"]["version"] == item["version"] for item in versions["items"])
    v2 = session.query(models.JobVersion).filter_by(job_id=job.id, version=2).one()
    assert session.query(models.JobVersionSkill).filter_by(
        job_version_id=v2.id, status="active").count() == 2
    assert session.query(models.CapabilityChange).filter_by(
        job_id=job.id, version=2).count() == 3

    detail = client.get(f"/api/jobs/{job.id}").json()
    assert detail["version"] == 2
    assert "Spring" in {item["name"] for item in detail["required_skills"]}
    panorama = client.get("/api/graph/panorama?mode=skill").json()
    assert "Spring" in {node["name"] for node in panorama["nodes"]}
    matched = client.post("/api/match/analyze", headers=user_headers, json={
        "job_id": job.id, "skills": ["Java", "Spring"],
        "generate_suggestions": False, "save": True})
    assert matched.status_code == 200 and matched.json()["contract"]["version"] == 2
    assert session.query(models.MatchRun).order_by(models.MatchRun.id.desc()).first().job_version == 2


def test_evolution_reconciliation_failure_rolls_back_publication(
        client, session, monkeypatch):
    _, _, admin_headers = make_actor(session, "admin", "evolution-rollback")
    job = seed_job(session)
    created = client.post("/api/admin/evolution-runs", headers=admin_headers, json={
        "job_id": job.id, "idempotency_key": "rollback-run"})
    run_id = created.json()["run"]["id"]
    proposal = {"capabilities": [
        {"name": "Java", "importance": "required", "weight": .9,
         "confidence": .9, "source_count": 2},
        {"name": "Spring", "importance": "required", "weight": .8,
         "confidence": .85, "source_count": 2},
    ]}
    assert client.post(f"/api/admin/evolution-runs/{run_id}/propose",
                       headers=admin_headers,
                       json={"proposed_snapshot": proposal}).status_code == 200
    assert client.post(f"/api/admin/evolution-runs/{run_id}/review",
                       headers=admin_headers,
                       json={"action": "approve", "comment": "通过"}).status_code == 200
    guards.settings.read_only = False
    monkeypatch.setattr(
        "app.routers.admin.evolution_service.assert_snapshot_reconciled",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("forced reconciliation failure")))

    response = client.post(f"/api/admin/evolution-runs/{run_id}/publish",
                           headers=admin_headers)

    assert response.status_code == 409
    session.refresh(job)
    assert job.version == 1
    assert session.query(models.JobVersion).count() == 0
    assert session.query(models.CapabilityChange).count() == 0
    assert session.query(models.JobSkill).count() == 1
    run = session.get(models.EvolutionRun, run_id)
    session.refresh(run)
    assert run.status == "approved" and "forced reconciliation failure" in run.error
    assert session.query(models.EvolutionReview).filter_by(
        evolution_run_id=run_id).count() == 1


def test_legacy_evolution_update_is_preview_only_in_all_modes(
        client, session, monkeypatch):
    _, _, admin_headers = make_actor(session, "admin", "legacy-preview")
    job = seed_job(session)
    monkeypatch.setattr("app.routers.evolution.extraction.parse_jd", lambda text: {
        "required_skills": [{"name": "Spring"}], "bonus_skills": [],
        "fine_skills": []})
    monkeypatch.setattr("app.routers.evolution.hallucination.aggregate_capabilities",
                        lambda rows, web_evidence_skills: {
                            "capabilities": [
                                {"name": "Java", "importance": "required", "weight": .9,
                                 "confidence": .9, "source_count": 2, "status": "active"},
                                {"name": "Spring", "importance": "required", "weight": .8,
                                 "confidence": .85, "source_count": 2, "status": "active"},
                            ], "stats": {"jd_count": 1}})
    payload = {"job_id": job.id, "new_jds": ["Spring demand"], "use_web": False}

    guest_preview = client.post(
        "/api/evolution/update", headers={"Authorization": ""}, json=payload)
    assert guest_preview.status_code == 401

    guards.settings.read_only = False
    admin_preview = client.post("/api/evolution/update", headers=admin_headers, json=payload)
    assert admin_preview.status_code == 200
    body = admin_preview.json()
    assert body["dry_run"] is True and body["proposal_required"] is True
    assert body["admin_evolution_runs_endpoint"] == "/api/admin/evolution-runs"
    assert {item["name"] for item in body["proposed_snapshot"]["capabilities"]} == {
        "Java", "Spring"}
    assert body["proposed_snapshot"]["from_version"] == 1
    session.refresh(job)
    assert job.version == 1
    assert session.query(models.JobSkill).count() == 1
    assert session.query(models.JobVersion).count() == 0
    assert session.query(models.CapabilityChange).count() == 0
    assert session.query(models.EvolutionRun).count() == 0


def test_manual_jd_can_only_propose_level_upgrade_for_existing_skill(client, session):
    job = seed_job(session)

    response = client.post("/api/evolution/update", json={
        "job_id": job.id,
        "new_jds": ["任职要求：精通 Java，负责现有服务的性能优化。"],
        "use_web": False,
    })

    assert response.status_code == 200
    body = response.json()
    assert body["dry_run"] is True
    assert len(body["changes"]) == 1
    change = body["changes"][0]
    assert change["change_type"] == "modify"
    assert change["skill_name"] == "Java"
    assert change["old_value"]["level_required"] == "familiar"
    assert change["new_value"]["level_required"] == "expert"
    assert change["data_source"] == {
        "source": "manual_jd_preview",
        "jd_count": 1,
        "employer_validated": False,
        "manual_review_required": True,
    }
    proposed = body["proposed_snapshot"]["capabilities"]
    assert proposed[0]["name"] == "Java"
    assert proposed[0]["level_required"] == "expert"
    assert proposed[0]["source_count"] == 2
    assert session.query(models.JobVersion).count() == 0
    assert session.query(models.EvolutionRun).count() == 0


def test_candidate_rejects_forged_emergence_counts_and_missing_evidence(client, session):
    admin, _, headers = make_actor(session, "admin", "forged-candidate")
    candidate = seed_discovery_candidate(
        session, admin, candidate_definition([]), verdict="INSUFFICIENT_EVIDENCE")
    guards.settings.read_only = False

    response = client.post(
        f"/api/admin/candidates/{candidate.id}/review", headers=headers,
        json={"action": "approve", "comment": "伪造字段不能通过", "publish": True})

    assert response.status_code == 409
    assert set(response.json()["detail"]["reasons"]) >= {
        "emergence_not_confirmed", "no_employer_validated_capability"}
    session.refresh(candidate)
    assert candidate.status == "submitted"
    assert session.query(models.CandidateReview).count() == 0
    assert session.query(models.Job).count() == 0
    assert session.query(models.JobSkill).count() == 0
    assert session.query(models.JobVersion).count() == 0


def test_candidate_rejects_multiple_jds_from_one_employer(client, session):
    admin, _, headers = make_actor(session, "admin", "one-employer")
    raw_jds = seed_raw_jds(session, employer_count=1, jd_count_per_employer=2)
    candidate = seed_discovery_candidate(
        session, admin, candidate_definition([row.id for row in raw_jds]))
    guards.settings.read_only = False

    response = client.post(
        f"/api/admin/candidates/{candidate.id}/review", headers=headers,
        json={"action": "approve", "comment": "单一雇主不能通过", "publish": True})

    assert response.status_code == 409
    assert "no_employer_validated_capability" in response.json()["detail"]["reasons"]
    assert session.query(models.CandidateReview).count() == 0
    assert session.query(models.Job).count() == 0
    assert session.query(models.JobSkill).count() == 0
    assert session.query(models.JobVersion).count() == 0


def test_candidate_publish_uses_only_server_validated_evidence(client, session):
    admin, _, headers = make_actor(session, "admin", "validated-candidate")
    raw_jds = seed_raw_jds(session, employer_count=2)
    candidate = seed_discovery_candidate(
        session, admin,
        candidate_definition([row.id for row in raw_jds], include_candidate=True),
        evidence_snapshot=[{
            "kind": "policy", "title": "真实政策", "provider": "人社部",
            "url": "https://official.test/policy", "content": "新职业政策依据",
        }])
    guards.settings.read_only = False

    response = client.post(
        f"/api/admin/candidates/{candidate.id}/review", headers=headers,
        json={"action": "approve", "comment": "证据核验通过", "publish": True})

    assert response.status_code == 200
    job = session.query(models.Job).one()
    assert job.emergence_score == .82
    assert job.confidence == pytest.approx(.8533)
    assert job.confidence != 1.0
    assert job.source_summary == {
        "origin": "governed_discovery", "discovery_run_id": candidate.discovery_run_id,
        "evidence_count": 1}
    relations = session.query(models.JobSkill).all()
    assert len(relations) == 8
    assert {row.source_count for row in relations} == {2}
    assert "未验证能力" not in {row.name for row in session.query(models.Skill).all()}
    evidence = session.query(models.Evidence).all()
    assert len(evidence) == 16
    assert all(row.raw_jd_id in {raw.id for raw in raw_jds} for row in evidence)
    assert all((row.source_url or "").startswith("https://trusted.test/") for row in evidence)
    version = session.query(models.JobVersion).one()
    assert version.evidence_window == {
        "start": "2026-08-01",
        "end": "2026-08-02",
        "jd_count": 2,
        "employer_count": 2,
        "dimensions": {
            "job_name": "新型复合工程师",
            "seniority": "middle",
            "recruitment_type": "mixed",
            "track": "product",
            "industry": "general",
        },
    }
    assert version.contract_snapshot["status"] == "ready"
    assert session.query(models.JobVersionSkill).count() == 8
    authority = session.query(models.AuthorityEvidence).one()
    assert authority.title == "真实政策"
    session.refresh(candidate)
    assert candidate.status == "published" and candidate.published_job_id == job.id


def test_candidate_publishability_rejection_rolls_back_review(client, session):
    admin, _, headers = make_actor(session, "admin", "quality-gate")
    candidate = models.JobCandidate(
        owner_user_id=admin.id, status="submitted", current_revision=1)
    session.add(candidate)
    session.flush()
    session.add(models.JobCandidateRevision(
        candidate_id=candidate.id, revision=1, created_by=admin.id,
        definition={
            "job_title": "证据不足的新岗位",
            "emergence_verdict": "UNCERTAIN",
            "capabilities": [{"name": "Python", "status": "active",
                              "employer_count": 1}],
        }))
    session.commit()

    guards.settings.read_only = False
    response = client.post(
        f"/api/admin/candidates/{candidate.id}/review", headers=headers,
        json={"action": "approve", "comment": "尝试发布", "publish": True})

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "CANDIDATE_NOT_PUBLISHABLE"
    assert set(response.json()["detail"]["reasons"]) >= {
        "emergence_not_confirmed", "no_employer_validated_capability"}
    session.refresh(candidate)
    assert candidate.status == "submitted"
    assert session.query(models.CandidateReview).count() == 0
    assert session.query(models.Job).count() == 0


def test_feedback_cross_owner_and_audit_redaction(client, session):
    user_a, _, headers_a = make_actor(session, "user", "feedback-a")
    _, _, headers_b = make_actor(session, "user", "feedback-b")
    created = client.post("/api/feedback", headers=headers_a, json={
        "target_type": "job", "target_id": "1", "category": "correction",
        "content": "能力项需要纠正", "evidence": []})
    assert created.status_code == 201
    assert client.get(f"/api/feedback/{created.json()['id']}", headers=headers_b).status_code == 404
    actor = Actor(user_a, "user", None, ROLE_PERMISSIONS["user"])
    log = add_audit(session, actor, "test", "object", 1,
                    summary={"status": "ok", "token": "secret",
                             "resume_text": "phone 13800138000"})
    session.flush()
    assert log.summary == {"status": "ok"}


def test_feedback_revision_listing_and_admin_state_machine(client, session):
    _, _, user_headers = make_actor(session, "user", "feedback-flow")
    _, _, admin_headers = make_actor(session, "admin", "feedback-flow")
    payload = {"target_type": "job", "target_id": "7", "category": "correction",
               "content": "初始反馈", "evidence": []}
    created = client.post("/api/feedback", headers=user_headers, json=payload)
    ticket_id = created.json()["id"]
    revised = client.patch(f"/api/feedback/{ticket_id}", headers=user_headers, json={
        **payload, "content": "补充证据", "evidence": [{"url": "https://example.test/e"}]})
    assert revised.status_code == 200 and revised.json()["current_revision"] == 2
    listed = client.get("/api/feedback?status=submitted&page=0&size=200",
                        headers=user_headers).json()
    assert listed["total"] == 1 and len(listed["items"][0]["revisions"]) == 2
    assert client.get("/api/admin/feedback", headers=user_headers).status_code == 403
    admin_list = client.get("/api/admin/feedback?status=submitted&page=0&size=200",
                            headers=admin_headers)
    assert admin_list.status_code == 200
    admin_item = admin_list.json()["items"][0]
    assert admin_item["content"] == "补充证据"
    assert admin_item["owner_username"] == "user-feedback-flow"
    assert admin_item["current_revision"] == 2
    assert client.get("/api/admin/feedback?status=invalid",
                      headers=admin_headers).status_code == 422

    assert client.post(f"/api/admin/feedback/{ticket_id}/review", headers=admin_headers,
                       json={"action": "triage"}).json()["status"] == "triaged"
    assert client.patch(f"/api/feedback/{ticket_id}", headers=user_headers,
                        json=payload).status_code == 409
    assert client.post(f"/api/admin/feedback/{ticket_id}/review", headers=admin_headers,
                       json={"action": "approve"}).json()["status"] == "approved"
    assert client.post(f"/api/admin/feedback/{ticket_id}/review", headers=admin_headers,
                       json={"action": "apply"}).status_code == 422
    assert client.post(f"/api/admin/feedback/{ticket_id}/review", headers=admin_headers,
                       json={"action": "apply", "applied_record_type": "job_version",
                             "applied_record_id": "12"}).status_code == 422
    job = seed_job(session, "反馈验证岗位")
    version = models.JobVersion(job_id=job.id, version=1, status="published")
    session.add(version)
    session.commit()
    applied = client.post(f"/api/admin/feedback/{ticket_id}/review", headers=admin_headers,
                          json={"action": "apply", "applied_record_type": "job_version",
                                "applied_record_id": str(version.id)})
    assert applied.status_code == 200 and applied.json()["status"] == "applied"
    assert client.post(f"/api/admin/feedback/{ticket_id}/review", headers=admin_headers,
                       json={"action": "reject"}).status_code == 409
    assert client.get(f"/api/feedback/{ticket_id}",
                      headers=admin_headers).json()["applied_record_id"] == str(version.id)


def test_admin_user_org_audit_and_usage_management(client, session):
    admin, _, admin_headers = make_actor(session, "admin", "management")
    target, _, user_headers = make_actor(session, "user", "managed")
    assert client.get("/api/admin/users", headers=user_headers).status_code == 403
    assert client.post("/api/admin/organizations", headers=admin_headers,
                       json={"name": ""}).status_code == 422
    created = client.post("/api/admin/organizations", headers=admin_headers,
                          json={"name": "研发中心"})
    assert created.status_code == 201
    org_id = created.json()["id"]
    assert client.post("/api/admin/organizations", headers=admin_headers,
                       json={"name": "研发中心"}).status_code == 409
    assert client.patch(f"/api/admin/users/{target.id}", headers=admin_headers,
                        json={"role": "hr"}).status_code == 422
    promoted = client.patch(f"/api/admin/users/{target.id}", headers=admin_headers,
                            json={"role": "hr", "organization_id": org_id})
    assert promoted.status_code == 200 and promoted.json()["role"] == "hr"
    users = client.get("/api/admin/users?role=hr&status=active&page=0&size=200",
                       headers=admin_headers).json()
    assert users["total"] == 1 and users["items"][0]["organization_name"] == "研发中心"
    assert client.patch(f"/api/admin/users/{admin.id}", headers=admin_headers,
                        json={"status": "disabled"}).status_code == 409
    permissions = client.get("/api/admin/permissions", headers=admin_headers).json()["items"]
    assert {item["role"] for item in permissions} == {"user", "hr", "admin"}

    organizations = client.get("/api/admin/organizations?status=active&page=0&size=200",
                               headers=admin_headers).json()
    assert organizations["total"] == 1 and organizations["items"][0]["member_count"] == 1
    assert client.patch(f"/api/admin/organizations/{org_id}", headers=admin_headers,
                        json={"status": "invalid"}).status_code == 422
    disabled = client.patch(f"/api/admin/organizations/{org_id}", headers=admin_headers,
                            json={"status": "disabled"})
    assert disabled.status_code == 200 and disabled.json()["status"] == "disabled"

    session.add_all([
        models.UsageEvent(user_id=admin.id, feature="login", duration_ms=10, success=True),
        models.UsageEvent(user_id=target.id, organization_id=org_id, feature="match",
                          duration_ms=100, success=False),
    ])
    session.commit()
    usage = client.get("/api/admin/usage/daily?days=0", headers=admin_headers).json()
    assert usage["days"] == 1 and usage["items"][0]["total"] == 2
    assert usage["items"][0]["p50_ms"] == 10 and usage["items"][0]["p95_ms"] == 100
    assert usage["items"][0]["error_rate"] == .5
    audits = client.get(
        "/api/admin/audit-logs?action=admin.user.update&result=success",
        headers=admin_headers).json()
    assert audits["total"] == 1 and audits["items"][0]["actor_username"] == admin.username


def test_resume_file_error_codes(monkeypatch):
    with pytest.raises(resume.ResumeFileError) as old_doc:
        resume.extract_text("legacy.doc", b"binary")
    assert old_doc.value.code == "UNSUPPORTED_DOC"
    with pytest.raises(resume.ResumeFileError) as empty:
        resume.extract_text("empty.txt", b"")
    assert empty.value.code == "EMPTY_FILE"
    with pytest.raises(resume.ResumeFileError) as corrupt_docx:
        resume.extract_text("broken.docx", b"not-a-zip")
    assert corrupt_docx.value.code == "CORRUPT_FILE"

    class ScannedPage:
        images = [{}]
        def extract_text(self):
            return ""
    class FakePdf:
        pages = [ScannedPage()]
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return None
    fake_pdf = FakePdf()
    monkeypatch.setitem(sys.modules, "pdfplumber",
                        SimpleNamespace(open=lambda stream: fake_pdf))
    with pytest.raises(resume.ResumeFileError) as scanned:
        resume.extract_text("scan.pdf", b"%PDF-fake")
    assert scanned.value.code == "SCANNED_PDF"

    monkeypatch.setitem(sys.modules, "pdfplumber",
                        SimpleNamespace(open=lambda stream: (_ for _ in ()).throw(
                            RuntimeError("password encrypted"))))
    with pytest.raises(resume.ResumeFileError) as encrypted:
        resume.extract_text("encrypted.pdf", b"%PDF-fake")
    assert encrypted.value.code == "ENCRYPTED_FILE"
    with pytest.raises(resume.ResumeFileError) as standard_encrypted:
        resume.extract_text("standard-encrypted.pdf", b"%PDF-1.7 /Encrypt 12 0 R")
    assert standard_encrypted.value.code == "ENCRYPTED_FILE"


def test_resume_upload_rejects_file_over_8mb(client):
    response = client.post(
        "/api/match/resume/upload",
        files={"file": ("large.txt", b"x" * (8 * 1024 * 1024 + 1), "text/plain")})

    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "FILE_TOO_LARGE"


def test_resume_upload_diagnoses_real_encrypted_pdf(client):
    from io import BytesIO
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.encrypt("resume-password")
    stream = BytesIO()
    writer.write(stream)
    encrypted_pdf = stream.getvalue()
    assert b"/Encrypt" in encrypted_pdf

    response = client.post(
        "/api/match/resume/upload",
        files={"file": ("encrypted.pdf", encrypted_pdf, "application/pdf")})

    assert response.status_code == 422
    assert response.json()["detail"] == {
        "code": "ENCRYPTED_FILE", "message": "PDF 已加密，无法解析"}
