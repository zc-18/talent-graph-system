"""Organization team lifecycle regressions (isolated SQLite only)."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import guards, models
from app.auth import token_hash
from app.db import Base, get_db
from app.main import app


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
def client(monkeypatch, session):
    monkeypatch.setattr(guards.settings, "read_only", True, raising=False)
    app.dependency_overrides[get_db] = lambda: session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def make_hr(session, suffix: str):
    user = models.AppUser(username=f"hr-team-{suffix}", password_hash="unused",
                          role="hr", status="active")
    session.add(user)
    session.flush()
    org = models.Organization(name=f"Team Org {suffix}", status="active", created_by=user.id)
    session.add(org)
    session.flush()
    session.add(models.OrganizationMember(
        organization_id=org.id, user_id=user.id, role="hr", status="active"))
    raw = f"team-token-{suffix}"
    session.add(models.UserSession(
        user_id=user.id, token_hash=token_hash(raw),
        expires_at=datetime.utcnow() + timedelta(hours=1)))
    session.commit()
    return user, org, {"Authorization": f"Bearer {raw}"}


def seed_job(session):
    job = models.Job(name="团队后端工程师", slug="team-backend", category="云计算与工程",
                     track="software", industry="internet", recruitment_type="social",
                     level="middle", status="published", version=1,
                     core_responsibilities=[], typical_scenarios=[])
    skill = models.Skill(name="Java", normalized_name="Java", category="编程语言")
    session.add_all([job, skill])
    session.flush()
    session.add(models.JobSkill(
        job_id=job.id, skill_id=skill.id, importance="required", weight=.9,
        confidence=.9, source_count=2, status="active"))
    session.commit()
    return job


def test_hr_team_create_add_remove_and_reopen_history(client, session):
    user, org, headers = make_hr(session, "a")
    job = seed_job(session)
    profile = models.ResumeProfile(
        organization_id=org.id, code="TEAM-CAND-001", source_type="batch",
        skills=["Java"], skill_levels={"Java": "proficient"}, authorized=True,
        retention_expires_at=datetime.utcnow() + timedelta(days=30))
    session.add(profile)
    session.commit()

    created = client.post("/api/talent/teams", headers=headers, json={
        "name": "平台交付组", "description": "终验团队", "target_job_id": job.id})
    assert created.status_code == 201
    team_id = created.json()["id"]
    added = client.post(f"/api/talent/teams/{team_id}/members", headers=headers, json={
        "resume_profile_id": profile.id, "display_name": "候选A", "role_label": "后端"})
    assert added.status_code == 201
    assert added.json()["before"]["member_count"] == 0
    assert added.json()["after"]["member_count"] == 1
    member_id = added.json()["member_id"]

    detail = client.get(f"/api/talent/teams/{team_id}", headers=headers).json()
    assert detail["target_job_id"] == job.id
    assert detail["members"][0]["resume_profile_id"] == profile.id
    gap = client.get(
        f"/api/talent/teams/{team_id}/gap?job_id={job.id}", headers=headers).json()
    assert gap["team"]["size"] == 1 and gap["coverage_rate"] == 1.0

    removed = client.delete(
        f"/api/talent/teams/{team_id}/members/{member_id}", headers=headers)
    assert removed.status_code == 200
    assert removed.json()["after"]["member_count"] == 0
    history = client.get(
        f"/api/talent/teams/{team_id}/history?page=1&size=10", headers=headers).json()
    assert history["total"] == 3
    assert [item["action"] for item in reversed(history["items"])] == [
        "created", "member_added", "member_removed"]
    assert session.query(models.AuditLog).filter(
        models.AuditLog.actor_user_id == user.id,
        models.AuditLog.action.in_({"team.create", "team.member.add", "team.member.remove"})
    ).count() == 3


def test_team_mutation_is_org_scoped_and_rejects_expired_profile(client, session):
    _, org_a, headers_a = make_hr(session, "scope-a")
    _, _, headers_b = make_hr(session, "scope-b")
    job = seed_job(session)
    team_id = client.post("/api/talent/teams", headers=headers_a, json={
        "name": "隔离团队", "target_job_id": job.id}).json()["id"]
    expired = models.ResumeProfile(
        organization_id=org_a.id, code="EXPIRED", source_type="batch",
        skills=["Java"], skill_levels={}, authorized=True,
        retention_expires_at=datetime.utcnow() - timedelta(seconds=1))
    session.add(expired)
    session.commit()

    assert client.post(f"/api/talent/teams/{team_id}/members", headers=headers_a, json={
        "resume_profile_id": expired.id, "display_name": "过期候选"}).status_code == 404
    assert client.get(
        f"/api/talent/teams/{team_id}/history", headers=headers_b).status_code == 404
    assert client.post(f"/api/talent/teams/{team_id}/members", headers=headers_b, json={
        "resume_profile_id": expired.id, "display_name": "越权候选"}).status_code == 404


def test_public_demo_team_is_readable_but_not_writable(client, session):
    """种子演示团队（organization_id 为 NULL）要「列得出来也读得出来」。

    回归：这类团队被 GET /teams 刻意列给所有人，但 history / 加成员 / 移成员
    过去统一走 ownership.require_org，而它对 organization_id IS NULL 一律判 404。
    页面默认又选中列表第一项（正是演示团队），于是每次打开「团队变化历史」都是
    被前端吞掉的 404，显示成「暂无团队变更」，看起来像数据丢了。
    """
    _, org, headers = make_hr(session, "public")
    job = seed_job(session)
    public_team = models.Team(name="AI 算法组", description="公共演示团队",
                              organization_id=None, target_job_id=job.id)
    session.add(public_team)
    session.commit()
    profile = models.ResumeProfile(
        organization_id=org.id, code="PUB-CAND-001", source_type="batch",
        skills=["Java"], skill_levels={}, authorized=True,
        retention_expires_at=datetime.utcnow() + timedelta(days=30))
    session.add(profile)
    session.commit()

    listed = client.get("/api/talent/teams", headers=headers).json()["items"]
    public_row = next(t for t in listed if t["id"] == public_team.id)
    assert public_row["organization_id"] is None
    assert public_row["editable"] is False

    # 读：列表里有就必须点得进去，三个读接口口径一致
    assert client.get(f"/api/talent/teams/{public_team.id}",
                      headers=headers).status_code == 200
    assert client.get(f"/api/talent/teams/{public_team.id}/gap?job_id={job.id}",
                      headers=headers).status_code == 200
    history = client.get(f"/api/talent/teams/{public_team.id}/history", headers=headers)
    assert history.status_code == 200
    assert history.json()["total"] == 0

    # 写：403 而不是 404 —— 它的存在已经公开了，用 404 掩饰只会让人以为数据没了
    denied = client.post(f"/api/talent/teams/{public_team.id}/members", headers=headers,
                         json={"resume_profile_id": profile.id, "display_name": "候选"})
    assert denied.status_code == 403
    assert "公共演示团队" in denied.json()["detail"]

    # 自建团队仍然可写，且 editable 为 True
    own_id = client.post("/api/talent/teams", headers=headers, json={
        "name": "本组织团队", "target_job_id": job.id}).json()["id"]
    own_row = next(t for t in client.get("/api/talent/teams", headers=headers).json()["items"]
                   if t["id"] == own_id)
    assert own_row["editable"] is True
    assert client.post(f"/api/talent/teams/{own_id}/members", headers=headers, json={
        "resume_profile_id": profile.id, "display_name": "候选"}).status_code == 201
    # 新建团队的「创建」事件当场可读 —— 用户报的「创建完没有历史记录」就是这条
    own_history = client.get(f"/api/talent/teams/{own_id}/history", headers=headers).json()
    assert [item["action"] for item in reversed(own_history["items"])] == [
        "created", "member_added"]


def test_cross_org_team_still_404_not_403(client, session):
    """公共团队放宽到 403 之后，跨租户仍须是 404，不能被拿来枚举他人 id。"""
    _, _, headers_a = make_hr(session, "probe-a")
    _, _, headers_b = make_hr(session, "probe-b")
    job = seed_job(session)
    team_id = client.post("/api/talent/teams", headers=headers_a, json={
        "name": "他组织团队", "target_job_id": job.id}).json()["id"]
    for response in (
        client.get(f"/api/talent/teams/{team_id}", headers=headers_b),
        client.get(f"/api/talent/teams/{team_id}/history", headers=headers_b),
        client.delete(f"/api/talent/teams/{team_id}/members/1", headers=headers_b),
    ):
        assert response.status_code == 404
