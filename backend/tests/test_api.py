"""Read-only API integration tests on an isolated in-memory graph."""
from __future__ import annotations

from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models
from app.db import Base, get_db
from app.main import app


@pytest.fixture()
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine)()
    job = models.Job(
        name="Java开发工程师", slug="java-engineer", category="云计算与工程",
        level="middle", track="software", industry="internet",
        recruitment_type="social", summary="负责企业级后端服务",
        core_responsibilities=["后端服务开发"], typical_scenarios=["互联网平台"],
        status="published", version=1, confidence=.92, evidence_count=1,
    )
    skill = models.Skill(name="Java", normalized_name="Java", category="编程语言")
    db.add_all([job, skill])
    db.flush()
    relation = models.JobSkill(
        job_id=job.id, skill_id=skill.id, importance="required", weight=.9,
        confidence=.95, source_count=2, status="active", level_required="proficient",
        factors={"support": .9, "diversity": .8, "freshness": .9, "authority": .9,
                 "external": .8},
    )
    raw = models.RawJD(
        job_title=job.name, company="示例科技", source="official", platform="official",
        raw_text="要求熟练掌握 Java", publish_date=datetime.utcnow(),
        employer_id=None, track="software", industry="internet", recruitment_type="social",
        is_duplicate=False, inflation_flag=False,
    )
    db.add_all([relation, raw])
    db.flush()
    db.add(models.Evidence(job_skill_id=relation.id, raw_jd_id=raw.id, source_type="jd",
                           source_name="企业官网", snippet="要求熟练掌握 Java"))
    db.commit()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture()
def client(session):
    app.dependency_overrides[get_db] = lambda: session
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_health(client):
    response = client.get("/api/health")
    assert response.status_code == 200 and response.json()["status"] == "ok"


def test_root(client):
    assert client.get("/").status_code == 200


def test_categories(client):
    response = client.get("/api/graph/categories")
    assert "人工智能" in response.json()["categories"]


def test_stats(client):
    stats = client.get("/api/graph/stats").json()
    assert stats["total_jobs"] == 1
    assert stats["total_skills"] == 1
    assert stats["duplicate_jds"] == 0


def test_panorama_graph(client):
    graph = client.get("/api/graph/panorama?mode=skill").json()
    assert graph["stats"]["jobs"] == 1
    assert any(node["type"] == "job" for node in graph["nodes"])
    assert any(node["type"] == "skill" for node in graph["nodes"])


def test_skill_tree(client):
    tree = client.get("/api/graph/skill-tree").json()
    assert tree["name"] == "新一代信息技术"
    assert isinstance(tree["children"], list)


def test_jobs_list_detail_and_evidence(client):
    listing = client.get("/api/jobs?size=5").json()
    assert listing["total"] == 1
    job_id = listing["items"][0]["id"]
    detail = client.get(f"/api/jobs/{job_id}").json()
    assert detail["id"] == job_id and detail["required_skills"][0]["name"] == "Java"
    evidence = client.get(f"/api/jobs/{job_id}/evidence").json()
    assert evidence["items"][0]["skill"] == "Java"


def test_job_filter_by_category(client):
    response = client.get("/api/jobs?category=云计算与工程").json()
    assert response["total"] == 1
    assert response["items"][0]["category"] == "云计算与工程"


def test_match_analyze_no_llm(client):
    job_id = client.get("/api/jobs?size=1").json()["items"][0]["id"]
    response = client.post("/api/match/analyze", json={
        "job_id": job_id, "skills": ["Java"], "skill_levels": {"Java": "proficient"},
        "generate_suggestions": False,
    })
    assert response.status_code == 200
    body = response.json()
    assert body["result"]["overall_score"] > 0
    assert len(body["contract"]["clusters"]) <= 12


def test_job_not_found(client):
    assert client.get("/api/jobs/99999999").status_code == 404
