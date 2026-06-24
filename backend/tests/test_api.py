"""只读 API 端点测试（针对已构建好的云端图谱，不触发大模型）。"""
import pytest
from fastapi.testclient import TestClient

try:
    from app.main import app
    client = TestClient(app)
    _db_ok = client.get("/api/graph/stats").status_code == 200
except Exception:  # noqa: BLE001
    _db_ok = False

pytestmark = pytest.mark.skipif(not _db_ok, reason="云数据库不可达，跳过 API 集成测试")


def test_health():
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_root():
    assert client.get("/").status_code == 200


def test_categories():
    r = client.get("/api/graph/categories")
    assert "人工智能" in r.json()["categories"]


def test_stats():
    s = client.get("/api/graph/stats").json()
    assert s["total_jobs"] >= 1
    assert s["total_skills"] >= 1
    assert s["duplicate_jds"] >= 0


def test_panorama_graph():
    g = client.get("/api/graph/panorama").json()
    assert "nodes" in g and "edges" in g
    assert g["stats"]["jobs"] >= 1
    job_nodes = [n for n in g["nodes"] if n["type"] == "job"]
    skill_nodes = [n for n in g["nodes"] if n["type"] == "skill"]
    assert job_nodes and skill_nodes


def test_skill_tree():
    t = client.get("/api/graph/skill-tree").json()
    assert t["name"] == "新一代信息技术"
    assert isinstance(t["children"], list)


def test_jobs_list_and_detail():
    lst = client.get("/api/jobs?size=5").json()
    assert lst["total"] >= 1
    job_id = lst["items"][0]["id"]
    detail = client.get(f"/api/jobs/{job_id}").json()
    assert detail["id"] == job_id
    assert "required_skills" in detail
    # 证据溯源
    ev = client.get(f"/api/jobs/{job_id}/evidence").json()
    assert "items" in ev


def test_job_filter_by_category():
    r = client.get("/api/jobs?category=人工智能").json()
    for it in r["items"]:
        assert it["category"] == "人工智能"


def test_match_analyze_no_llm():
    lst = client.get("/api/jobs?size=1").json()
    job_id = lst["items"][0]["id"]
    r = client.post("/api/match/analyze", json={
        "job_id": job_id, "skills": ["Python", "机器学习"],
        "skill_levels": {}, "generate_suggestions": False})
    assert r.status_code == 200
    body = r.json()
    assert "result" in body
    assert "overall_score" in body["result"]
    assert "learning_path" in body


def test_job_not_found():
    assert client.get("/api/jobs/99999999").status_code == 404
