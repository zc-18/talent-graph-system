"""语料切片 `url_coverage` 的口径回归（内存 SQLite，不碰云库）。

`/api/evolution/{job_id}/timeline` 曾用「去重 URL 条数 ÷ JD 条数」当覆盖率，分子分母
不是同一批总体：一条 JD 可以挂多个 `evidence.source_url`，比值就冲破 1，前端
（`frontend/src/pages/Evolution.tsx` 的 `Math.round(slice.url_coverage * 100)`）直接
显示成「URL 120%」；反过来多条 JD 共用同一个 URL 又会低报。

现在的口径是「该年切片内至少有一条 http(s) URL 的 JD 数 ÷ 该切片 JD 数」，按构造落在
[0,1]。`valid_url_count` 保持数 URL 条数不变——那个字段问的本来就是 URL 有多少条。
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
    # 演化路由挂了 _authenticated，读接口也要带 token。
    user = models.AppUser(username="timeline-reader", password_hash="unused",
                          role="user", status="active")
    session.add(user)
    session.flush()
    session.add(models.UserSession(user_id=user.id, token_hash=token_hash("timeline-token"),
                                   expires_at=datetime.utcnow() + timedelta(hours=1)))
    session.commit()
    app.dependency_overrides[get_db] = lambda: session
    value = TestClient(app)
    value.headers.update({"Authorization": "Bearer timeline-token"})
    try:
        yield value
    finally:
        app.dependency_overrides.clear()


@pytest.fixture()
def job_skill(session):
    """一个已发布岗位 + 一条 active 能力项，证据都挂在它下面。"""
    job = models.Job(name="测试开发工程师", slug="sdet-timeline", category="云计算与工程",
                     track="software", industry="internet", recruitment_type="social",
                     level="middle", status="published", version=1, confidence=.8,
                     core_responsibilities=[], typical_scenarios=[])
    session.add(job)
    session.flush()
    skill = models.Skill(name="Selenium", normalized_name="Selenium", category="通用")
    session.add(skill)
    session.flush()
    js = models.JobSkill(job_id=job.id, skill_id=skill.id, importance="required",
                         weight=.9, confidence=.9, source_count=2, status="active")
    session.add(js)
    session.commit()
    return job, js


def add_jd(session, job_skill, year: int, urls: list[str], *, raw_url: str | None = None):
    """一条非重复、可追溯的 JD，外加 len(urls) 条 jd 类证据。"""
    _, js = job_skill
    raw = models.RawJD(job_title="测试开发工程师", company="示例科技", source="official",
                       source_url=raw_url, raw_text="岗位职责：编写自动化用例。",
                       publish_date=datetime(year, 6, 1), platform="official",
                       is_duplicate=False, duplicate_of=None)
    session.add(raw)
    session.flush()
    # 没有 URL 的 JD 也必须有证据行，否则它压根进不了切片（查询是从 Evidence 出发的）。
    for url in urls or [None]:
        session.add(models.Evidence(job_skill_id=js.id, raw_jd_id=raw.id,
                                    source_type="jd", source_url=url, snippet="自动化测试"))
    session.commit()
    return raw


def slices_by_year(client, job) -> dict[int, dict]:
    body = client.get(f"/api/evolution/{job.id}/timeline").json()
    return {item["year"]: item for item in body["corpus_slices"]}


def test_one_jd_with_two_urls_caps_at_one_instead_of_two(client, session, job_skill):
    """回归：一条 JD 两个 URL 曾算出 2.0，前端渲染成「URL 200%」。"""
    job, _ = job_skill
    add_jd(session, job_skill, 2024,
           ["https://careers.example.com/a", "https://careers.example.com/a-mirror"])

    item = slices_by_year(client, job)[2024]
    assert item["jd_count"] == 1
    assert item["url_coverage"] == 1.0
    # 口径分家：URL 条数照旧数 URL，不跟着覆盖率被压回 1。
    assert item["valid_url_count"] == 2


def test_half_the_jds_carrying_a_url_reports_half(client, session, job_skill):
    job, _ = job_skill
    add_jd(session, job_skill, 2025, ["https://careers.example.com/b"])
    add_jd(session, job_skill, 2025, [])

    item = slices_by_year(client, job)[2025]
    assert item["jd_count"] == 2
    assert item["url_coverage"] == .5
    assert item["valid_url_count"] == 1


def test_bucket_without_any_url_reports_zero(client, session, job_skill):
    job, _ = job_skill
    add_jd(session, job_skill, 2026, [])
    add_jd(session, job_skill, 2026, [])

    item = slices_by_year(client, job)[2026]
    assert item["jd_count"] == 2
    assert item["url_coverage"] == 0.0
    assert item["valid_url_count"] == 0


def test_two_jds_sharing_one_url_are_both_counted(client, session, job_skill):
    """镜像 bug：同一条 URL 去重后只剩 1 条，旧口径把 2/2 低报成 0.5。"""
    job, _ = job_skill
    shared = "https://careers.example.com/shared"
    add_jd(session, job_skill, 2023, [shared])
    add_jd(session, job_skill, 2023, [shared])

    item = slices_by_year(client, job)[2023]
    assert item["jd_count"] == 2
    assert item["url_coverage"] == 1.0
    assert item["valid_url_count"] == 1


def test_non_http_and_raw_level_urls_are_scored_per_jd(client, session, job_skill):
    """URL 取 evidence.source_url or raw.source_url，且必须是 http(s)。"""
    job, _ = job_skill
    add_jd(session, job_skill, 2022, [], raw_url="https://careers.example.com/from-raw")
    add_jd(session, job_skill, 2022, ["ftp://careers.example.com/c"])
    add_jd(session, job_skill, 2022, [])

    item = slices_by_year(client, job)[2022]
    assert item["jd_count"] == 3
    assert item["valid_url_count"] == 1
    assert item["url_coverage"] == round(1 / 3, 4)


def test_every_slice_stays_within_zero_and_one(client, session, job_skill):
    """按构造的上下界——这条挂了说明分子分母又不是同一批总体了。"""
    job, _ = job_skill
    add_jd(session, job_skill, 2024, ["https://a.example.com/1", "https://a.example.com/2"])
    add_jd(session, job_skill, 2025, ["https://b.example.com/1"])
    add_jd(session, job_skill, 2025, [])
    add_jd(session, job_skill, 2026, [])

    items = slices_by_year(client, job)
    assert set(items) == {2024, 2025, 2026}
    assert all(0.0 <= item["url_coverage"] <= 1.0 for item in items.values())
