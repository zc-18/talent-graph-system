"""新岗位发现后台化的回归测试（不连云库、不打大模型、不联网）。

锁的是三件事：

1. **状态机** queued -> running -> completed。这条路径的价值在于 `POST /discovery/runs`
   原本是同步 `def`，里面串着联网检索 + LLM，最坏 200 秒把工作线程和浏览器一起挂住。
   改成后台任务之后，"立刻拿到 run_id" 和 "稍后轮询到结果" 必须都成立，缺一个这个
   改造就是白做。
2. **失败要留痕**。后台任务抛异常时前端看不到栈，只能看 `status`/`error` 两列；
   失败的幂等键还必须能重跑，否则用户换个关键词都绕不开这条卡死的记录。
3. **READ_ONLY 下 dry-run 仍出结果**。演示站常年 `READ_ONLY=1`，发现流程写的是
   owner/org 私域工作流行（DiscoveryRun / JobCandidate），不碰公共图谱，所以不该被
   写闸门拦下。后台化很容易顺手把这条演示路径掐了，这里把它钉住。

TestClient 会在响应生成后、请求返回前把 BackgroundTasks 跑完，所以 POST 的响应体里
是 queued（当时还没跑），随后的 GET 已经是终态——这两者的差异正是要断言的东西。
"""
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

DEFINITION = {
    "job_title": "空间智能交互工程师", "category": "人工智能", "level": "middle",
    "track": "algorithm", "summary": "负责空间计算场景下的多模态交互实现",
    "core_responsibilities": ["设计交互管线"], "typical_scenarios": ["XR 头显"],
    "capabilities": [{"name": "Unity", "importance": "required", "weight": .9,
                      "confidence": .9, "source_count": 2, "status": "active"}],
}


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


def make_user(session, suffix: str, role: str = "user"):
    user = models.AppUser(username=f"{role}-bg-{suffix}", password_hash="unused",
                          role=role, status="active")
    session.add(user)
    session.flush()
    raw = f"bg-token-{role}-{suffix}"
    session.add(models.UserSession(user_id=user.id, token_hash=token_hash(raw),
                                   expires_at=datetime.utcnow() + timedelta(hours=1)))
    session.commit()
    return user, {"Authorization": f"Bearer {raw}"}


def stub_new_job(monkeypatch, *, on_search=None):
    """Replace the two expensive calls with deterministic stubs."""
    def discover(keyword, **kwargs):
        if on_search is not None:
            on_search(kwargs.get("db"))
        return {"keyword": keyword, "verdict": "EMERGING", "emergence_score": .77,
                "evidence": [{"title": "e1", "content": "c1", "url": "http://a"}],
                "resolution": {"canonical_title": keyword, "track": "algorithm"},
                "signals": {"authority_strength": .8}}

    monkeypatch.setattr("app.routers.discovery.discovery.discover_candidates", discover)
    monkeypatch.setattr("app.routers.discovery.discovery.define_new_job",
                        lambda keyword, evidence: dict(DEFINITION))


def test_background_run_reports_queued_then_running_then_completed(
        client, session, monkeypatch):
    _, headers = make_user(session, "state")
    seen: list[str] = []

    def capture(worker_db):
        # The worker flipped the row to `running` before touching search or the LLM;
        # observing it from inside the expensive call is the only honest way to prove it.
        row = worker_db.query(models.DiscoveryRun).order_by(
            models.DiscoveryRun.id.desc()).first()
        seen.append(row.status)

    stub_new_job(monkeypatch, on_search=capture)

    created = client.post("/api/discovery/runs?async_mode=true", headers=headers,
                          json={"keyword": "空间智能交互工程师"})
    assert created.status_code == 201
    body = created.json()
    # The response is serialized before the background task runs: queued, no verdict yet.
    assert body["status"] == "queued"
    assert body["classification"] is None
    assert body["candidate_id"] is None
    run_id = body["run_id"]
    assert run_id

    assert seen == ["running"]

    polled = client.get(f"/api/discovery/runs/{run_id}", headers=headers)
    assert polled.status_code == 200
    result = polled.json()
    assert result["status"] == "completed"
    assert result["error"] is None
    assert result["classification"] == "NEW"
    assert result["candidate_id"] is not None
    assert result["candidate"]["definition"]["job_title"] == DEFINITION["job_title"]
    assert result["run"]["evidence"] and result["run"]["signals"]["source_verdict"] == "EMERGING"

    listed = client.get("/api/discovery/runs", headers=headers).json()
    assert listed["total"] == 1
    assert listed["items"][0]["status"] == "completed"


def test_background_failure_is_recorded_and_the_key_can_be_retried(
        client, session, monkeypatch):
    _, headers = make_user(session, "failure")
    monkeypatch.setattr("app.routers.discovery.discovery.discover_candidates",
                        lambda keyword, **kwargs: (_ for _ in ()).throw(
                            RuntimeError("tavily unreachable")))

    created = client.post("/api/discovery/runs?async_mode=true", headers=headers,
                          json={"keyword": "空间智能交互工程师", "idempotency_key": "bg-fail"})
    run_id = created.json()["run_id"]

    failed = client.get(f"/api/discovery/runs/{run_id}", headers=headers).json()
    assert failed["status"] == "failed"
    assert "tavily unreachable" in failed["error"]
    assert failed["classification"] is None

    # A dead run must not hold its idempotency key hostage: the same key re-runs.
    stub_new_job(monkeypatch)
    retried = client.post("/api/discovery/runs?async_mode=true", headers=headers,
                          json={"keyword": "空间智能交互工程师", "idempotency_key": "bg-fail"})
    assert retried.status_code == 201 and retried.json()["run_id"] == run_id
    recovered = client.get(f"/api/discovery/runs/{run_id}", headers=headers).json()
    assert recovered["status"] == "completed" and recovered["error"] is None
    assert session.query(models.DiscoveryRun).count() == 1


def test_read_only_dry_run_still_produces_a_definition(client, session, monkeypatch):
    """READ_ONLY=1 是线上常态；后台化不能顺手把演示用的 dry-run 结果掐掉。"""
    monkeypatch.setattr(guards.settings, "read_only", True, raising=False)
    _, headers = make_user(session, "readonly")
    stub_new_job(monkeypatch)

    created = client.post("/api/discovery/runs?async_mode=true", headers=headers,
                          json={"keyword": "空间智能交互工程师"})
    assert created.status_code == 201 and created.json()["status"] == "queued"
    run_id = created.json()["run_id"]

    polled = client.get(f"/api/discovery/runs/{run_id}", headers=headers).json()
    assert polled["status"] == "completed"
    assert polled["classification"] == "NEW"
    capabilities = polled["candidate"]["definition"]["capabilities"]
    assert [item["name"] for item in capabilities] == ["Unity"]
    # 私域工作流行照常落库，公共图谱一行没动。
    assert session.query(models.JobCandidateRevision).count() == 1
    assert session.query(models.Job).count() == 0
    assert session.query(models.JobSkill).count() == 0


def test_synchronous_mode_stays_the_default_for_non_polling_callers(
        client, session, monkeypatch):
    _, headers = make_user(session, "sync")
    stub_new_job(monkeypatch)

    created = client.post("/api/discovery/runs", headers=headers,
                          json={"keyword": "空间智能交互工程师"})
    body = created.json()
    assert created.status_code == 201
    assert body["status"] == "completed"
    assert body["classification"] == "NEW"
    assert body["candidate_id"] is not None


def test_runs_are_owner_scoped_and_hide_existence_from_other_users(
        client, session, monkeypatch):
    _, owner_headers = make_user(session, "owner")
    _, other_headers = make_user(session, "other")
    stub_new_job(monkeypatch)

    run_id = client.post("/api/discovery/runs?async_mode=true", headers=owner_headers,
                         json={"keyword": "空间智能交互工程师"}).json()["run_id"]

    # ownership.require_owner answers 404, not 403, so an id cannot be probed.
    assert client.get(f"/api/discovery/runs/{run_id}", headers=other_headers).status_code == 404
    assert client.get("/api/discovery/runs", headers=other_headers).json()["total"] == 0
    assert client.get("/api/discovery/runs", headers=owner_headers).json()["total"] == 1
