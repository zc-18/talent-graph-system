"""公开门户聚合口径的护栏。

门户首屏（`/`）是未登录访客看到的第一屏，方案 A2 要求它展示真实的岗位数/JD 数/
雇主覆盖率。但 `main.py` 给全站业务路由统一挂了 `Depends(current_actor)`，
`/api/graph/stats` 对匿名访客返回 401，门户拿不到任何真数。

解决方式是开一个**极小的公开面** `/api/public/stats`，而不是把 `/api/graph` 放开。
这个面一旦放宽就是真实的信息泄露，所以用测试钉死三件事：
匿名可读、字段白名单、且 `/api/graph/stats` 不因此被放开。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.main import app
from app.routers import public as public_router


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
def anon(session):
    """故意不带任何 Authorization 头——这里要验的就是匿名可达。"""
    app.dependency_overrides[get_db] = lambda: session
    value = TestClient(app)
    try:
        yield value
    finally:
        app.dependency_overrides.clear()


def test_public_stats_is_readable_without_a_token(anon):
    response = anon.get("/api/public/stats")
    assert response.status_code == 200
    assert set(response.json()) == set(public_router._PUBLIC_FIELDS)


def test_public_stats_never_leaks_beyond_the_whitelist(anon):
    """上游 stats_overview 将来新增字段，不得从公开面漏出去。"""
    body = anon.get("/api/public/stats").json()
    for leaked in ("factor_averages", "confidence_distribution", "duplicate_jds",
                   "valid_evidence_url_count", "confidence_as_of", "categories",
                   "contract_ready_ratio"):
        assert leaked not in body


def test_opening_the_public_face_did_not_open_the_authenticated_one(anon):
    """开公开面不等于放开 /api/graph/stats——它必须仍要求登录。"""
    assert anon.get("/api/graph/stats").status_code == 401
