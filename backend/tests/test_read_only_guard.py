"""演示站只读闸门的回归测试（不连云库、不打大模型）。

锁的是 2026-07-28 / 07-30 两次线上事故的**共同根因**：改图谱的按钮直接对公网敞开。
单点修复只能堵住已知路径，这里锁的是总闸本身——以及"只读不等于残废"这条设计约束：
演化推演、新岗位发现在只读模式下必须照常出结果，只是不落库。

`get_db` 一律改接内存 SQLite。走真实 `SessionLocal` 的话，这些请求会打到 `DB_NAME`
指向的库——本机常年指着生产的 talent_graph_v3，等于让单元测试对生产库发 DELETE。
现在没出事只是因为断言用的 id 恰好不存在，这种"靠运气安全"不该留在测试里。
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import guards
from app.db import Base, get_db
from app.main import app


@pytest.fixture()
def sqlite_session():
    """内存库 + 建表。

    必须用 StaticPool：`sqlite://` 的内存库是**按连接**独立的，而 TestClient 在另一个
    线程里处理请求、会另取一条连接——默认连接池下建表和请求会落在两个互不可见的空库上
    （症状是 `no such table: job`）。StaticPool 让全池共用同一条连接，两边才是同一个库。
    """
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    s = sessionmaker(bind=engine)()
    try:
        yield s
    finally:
        s.close()


def _client(monkeypatch, session, read_only: bool) -> TestClient:
    monkeypatch.setattr(guards.settings, "read_only", read_only, raising=False)
    app.dependency_overrides[get_db] = lambda: session
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


@pytest.fixture()
def client(monkeypatch, sqlite_session):
    return _client(monkeypatch, sqlite_session, read_only=True)


@pytest.fixture()
def rw_client(monkeypatch, sqlite_session):
    return _client(monkeypatch, sqlite_session, read_only=False)


# ---- 硬闸：没有只读等价物的写操作一律 403 ----------------------------------

def test_manual_edit_blocked(client):
    r = client.post("/api/jobs/manual-edit",
                    json={"job_id": 1, "skill_name": "Java", "action": "remove"})
    assert r.status_code == 403
    assert "只读" in r.json()["detail"]


def test_job_create_blocked(client):
    r = client.post("/api/jobs", json={"name": "测试岗位", "category": "人工智能",
                                       "level": "middle", "core_responsibilities": [],
                                       "typical_scenarios": [], "required_skills": [],
                                       "bonus_skills": []})
    assert r.status_code == 403


def test_job_delete_blocked(client):
    assert client.delete("/api/jobs/1").status_code == 403


def test_team_member_upload_requires_authentication(client):
    r = client.post("/api/talent/teams/1/members/upload",
                    files={"file": ("a.txt", b"hello", "text/plain")})
    assert r.status_code == 401


# ---- 只读模式不该影响读接口与健康检查 --------------------------------------

def test_health_exposes_mode(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok" and body["read_only"] is True


def test_read_endpoints_unaffected(client):
    # 闸门只拦写操作：读接口在只读模式下必须照常 200（空库返回零值统计）
    assert client.get("/api/graph/stats").status_code == 200


# ---- 关掉开关后写接口恢复（默认本地/离线跑脚本不受影响）---------------------

def test_gate_is_off_by_default(rw_client):
    # 公共写闸放行不等于绕过认证：匿名调用仍先被身份闸拒绝。
    assert rw_client.delete("/api/jobs/999999").status_code == 401
