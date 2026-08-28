"""HR 工作台后端韧性回归（不连云库、不打大模型）。

三条都是线上实测出来的问题，不是假想：

1. **排名口径**。批次 4 的 `progress.succeeded` 是 14，`/ranking` 却返回空数组，
   前端于是渲染成"暂无候选人排名"。原因不是权限也不是岗位版本，而是**同一个终态
   有两种写法**：`recruitment.process_file` 写 `succeeded`，展示种子写 `completed`，
   而 ranking 只认前者。计数走批次列、行走这个谓词，两边各说各话。
2. **读接口里的写**。`list_candidates` / `get_candidate` 在 GET 里 `db.commit()` 落审计日志，
   一旦这次 commit 失败，整张列表 500——而调用方要的数据其实早就查出来了。
3. **卡死的批次没人收尸**。worker 挂掉后批次永远停在 processing：前端无限轮询，
   上传又被"批次正在处理中"挡住，只能等人手工改库。
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models
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
def client(session):
    app.dependency_overrides[get_db] = lambda: session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture()
def hr(session):
    user = models.AppUser(username="hr-resilience", password_hash="unused",
                          role="hr", status="active")
    session.add(user)
    session.flush()
    org = models.Organization(name="韧性测试组织", status="active", created_by=user.id)
    session.add(org)
    session.flush()
    session.add(models.OrganizationMember(organization_id=org.id, user_id=user.id,
                                          role="hr", status="active"))
    session.add(models.UserSession(user_id=user.id, token_hash=token_hash("hr-resilience-token"),
                                   expires_at=datetime.utcnow() + timedelta(hours=1)))
    session.commit()
    return user, org, {"Authorization": "Bearer hr-resilience-token"}


def seed_batch(session, user, org, *, status="completed_with_errors",
               updated_at=None):
    job = models.Job(name="具身智能工程师", slug="embodied-ai", category="人工智能",
                     status="published", version=1, level="middle")
    session.add(job)
    session.flush()
    stamp = updated_at or datetime.utcnow()
    batch = models.RecruitmentBatch(
        organization_id=org.id, created_by=user.id, name="安全与测试人才池",
        target_job_id=job.id, target_job_version=1, status=status,
        contract_snapshot={"clusters": []},
        total_count=3, processed_count=3, succeeded_count=2, failed_count=1,
        created_at=stamp, updated_at=stamp)
    session.add(batch)
    session.flush()
    return job, batch


def test_ranking_returns_candidates_written_with_the_completed_spelling(
        client, session, hr):
    """线上真因：种子写 `completed`，ranking 只认 `succeeded`，于是排名整页为空。"""
    user, org, headers = hr
    _, batch = seed_batch(session, user, org)
    session.add_all([
        models.BatchCandidate(batch_id=batch.id, file_hash="h1", display_code="SC-04-001",
                              parse_status="completed", overall_score=.924, rank=1,
                              dimension_scores={"skill": .9}),
        models.BatchCandidate(batch_id=batch.id, file_hash="h2", display_code="SC-04-002",
                              parse_status="succeeded", overall_score=.906, rank=2,
                              dimension_scores={"skill": .8}),
        models.BatchCandidate(batch_id=batch.id, file_hash="h3", display_code="SC-04-003",
                              parse_status="failed", error_code="UNSUPPORTED_FORMAT"),
    ])
    session.commit()

    ranking = client.get(f"/api/hr/recruitment-batches/{batch.id}/ranking?page=1&size=200",
                         headers=headers)
    assert ranking.status_code == 200
    body = ranking.json()
    assert body["total"] == 2
    assert [item["code"] for item in body["items"]] == ["SC-04-001", "SC-04-002"]

    # 同一批候选也必须能被 Top-K 选入团队，否则排名看得见、选不动。
    selected = client.post(f"/api/hr/recruitment-batches/{batch.id}/select", headers=headers,
                           json={"candidate_ids": [body["items"][0]["candidate_id"]]})
    assert selected.status_code == 200 and selected.json()["selected"] == 1

    # 候选列表也要能按这个状态过滤，否则筛选框里的选项形同虚设。
    listed = client.get(f"/api/hr/recruitment-batches/{batch.id}/candidates?status=completed",
                        headers=headers)
    assert listed.status_code == 200 and listed.json()["total"] == 1


def test_unscored_candidate_is_not_dropped_by_the_null_score_comparison(
        client, session, hr):
    """NULL >= 0 在 SQL 里是 NULL，裸比较会把未打分的行悄悄吞掉。"""
    user, org, headers = hr
    _, batch = seed_batch(session, user, org)
    session.add(models.BatchCandidate(
        batch_id=batch.id, file_hash="h9", display_code="SC-04-009",
        parse_status="completed", overall_score=None, rank=None))
    session.commit()

    body = client.get(f"/api/hr/recruitment-batches/{batch.id}/ranking",
                      headers=headers).json()
    assert body["total"] == 1 and body["items"][0]["overall_score"] is None


def test_audit_write_failure_does_not_break_the_candidate_read(
        client, session, hr, monkeypatch):
    user, org, headers = hr
    _, batch = seed_batch(session, user, org)
    session.add(models.BatchCandidate(
        batch_id=batch.id, file_hash="h1", display_code="SC-04-001",
        parse_status="completed", overall_score=.9, rank=1))
    session.commit()

    def broken_commit():
        raise OperationalError("COMMIT", None, Exception("Lock wait timeout exceeded"))

    monkeypatch.setattr(session, "commit", broken_commit)

    listed = client.get(f"/api/hr/recruitment-batches/{batch.id}/candidates", headers=headers)
    assert listed.status_code == 200
    assert [item["code"] for item in listed.json()["items"]] == ["SC-04-001"]

    candidate_id = listed.json()["items"][0]["id"]
    detail = client.get(
        f"/api/hr/recruitment-batches/{batch.id}/candidates/{candidate_id}", headers=headers)
    assert detail.status_code == 200 and detail.json()["code"] == "SC-04-001"


def test_stale_processing_batch_is_reaped_on_read(client, session, hr):
    user, org, headers = hr
    stale_stamp = datetime.utcnow() - timedelta(hours=2)
    _, batch = seed_batch(session, user, org, status="processing", updated_at=stale_stamp)
    session.commit()

    listed = client.get("/api/hr/recruitment-batches", headers=headers).json()
    assert [item["status"] for item in listed["items"]] == ["failed"]
    session.expire_all()
    assert session.get(models.RecruitmentBatch, batch.id).status == "failed"


def test_recent_processing_batch_is_left_alone(client, session, hr):
    user, org, headers = hr
    _, batch = seed_batch(session, user, org, status="processing",
                          updated_at=datetime.utcnow() - timedelta(minutes=2))
    session.commit()

    detail = client.get(f"/api/hr/recruitment-batches/{batch.id}", headers=headers)
    assert detail.json()["status"] == "processing"
