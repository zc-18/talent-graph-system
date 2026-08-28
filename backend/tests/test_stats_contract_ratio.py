"""驾驶舱证据覆盖率口径回归（B1）。

`/api/graph/stats` 增加的 `contract_ready_ratio` 必须跟岗位列表页逐行显示的
`contract_status` 是同一个口径——两者都走 `role_contract.contract_summaries_for_jobs`。
以前前端只能翻页把 `contract_status` 自己数一遍，翻不全就跟驾驶舱对不上。

顺带钉住缓存：这个字段要跑一遍全量岗位的契约投影，比 stats 里其余 COUNT/AVG 重一个
量级，所以带 TTL + 指纹缓存。指纹必须在岗位集合或已验证能力关系变化时立刻失效，
否则跑完 data/run_pipeline.py 之后驾驶舱还端着旧比例——那正是这类缓存最常见的坑。
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models
from app.auth import token_hash
from app.db import Base, get_db
from app.main import app
from app.routers import graph as graph_router

# 九个分属不同能力簇的技能：契约要 ready 需要 >=8 个簇且其中 >=6 个必备。
READY_SKILLS = ["Selenium", "功能测试", "PyTorch", "Spark", "MySQL",
                "消息队列", "Spring", "Docker", "Java"]


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
def _clear_cache():
    graph_router._contract_ratio_cache.update(fingerprint=None, value=None, expires=0.0)
    yield
    graph_router._contract_ratio_cache.update(fingerprint=None, value=None, expires=0.0)


@pytest.fixture()
def client(session):
    # 全站路由现在都挂了登录依赖（main.py 的 _authenticated），读接口也要带 token。
    user = models.AppUser(username="stats-reader", password_hash="unused",
                          role="user", status="active")
    session.add(user)
    session.flush()
    session.add(models.UserSession(user_id=user.id, token_hash=token_hash("stats-token"),
                                   expires_at=datetime.utcnow() + timedelta(hours=1)))
    session.commit()
    app.dependency_overrides[get_db] = lambda: session
    value = TestClient(app)
    value.headers.update({"Authorization": "Bearer stats-token"})
    try:
        yield value
    finally:
        app.dependency_overrides.clear()


def add_job(session, name: str, slug: str, skills: list[str]):
    job = models.Job(name=name, slug=slug, category="云计算与工程", track="software",
                     industry="internet", recruitment_type="social", level="middle",
                     status="published", version=1, confidence=.8,
                     core_responsibilities=[], typical_scenarios=[])
    session.add(job)
    session.flush()
    for skill_name in skills:
        skill = models.Skill(name=skill_name, normalized_name=skill_name, category="通用")
        session.add(skill)
        session.flush()
        # source_count 就是契约里的雇主计数，>=MIN_EMPLOYERS(2) 才过闸门。
        session.add(models.JobSkill(
            job_id=job.id, skill_id=skill.id, importance="required", weight=.9,
            confidence=.9, source_count=2, status="active"))
    session.commit()
    return job


def test_contract_ready_ratio_matches_the_per_job_contract_status(client, session):
    ready = add_job(session, "测试开发工程师", "sdet", READY_SKILLS)
    thin = add_job(session, "证据不足岗位", "thin", [])

    body = client.get("/api/graph/stats").json()
    assert body["contract_evaluated_jobs"] == 2
    assert body["contract_ready_jobs"] == 1
    assert body["contract_ready_ratio"] == .5

    # 与列表页逐行口径逐个核对，而不是只信这个聚合数。
    from app.services import role_contract
    summaries = role_contract.contract_summaries_for_jobs(session, [ready, thin])
    assert summaries[ready.id]["contract_status"] == "ready"
    assert summaries[thin.id]["contract_status"] == "evidence_insufficient"


def test_ratio_recomputes_when_the_graph_changes(client, session):
    add_job(session, "测试开发工程师", "sdet", READY_SKILLS)
    first = client.get("/api/graph/stats").json()
    assert first["contract_ready_ratio"] == 1.0 and first["contract_evaluated_jobs"] == 1

    # 指纹里带了岗位数与 active 关系数，新增岗位必须立刻反映，不能等 TTL 过期。
    add_job(session, "证据不足岗位", "thin", [])
    second = client.get("/api/graph/stats").json()
    assert second["contract_evaluated_jobs"] == 2
    assert second["contract_ready_ratio"] == .5


def test_empty_graph_reports_zero_instead_of_dividing_by_zero(client, session):
    body = client.get("/api/graph/stats").json()
    assert body["contract_evaluated_jobs"] == 0
    assert body["contract_ready_jobs"] == 0
    assert body["contract_ready_ratio"] == 0.0
