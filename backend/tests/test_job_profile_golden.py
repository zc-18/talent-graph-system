import json
from pathlib import Path

from app.services.taxonomy import CAPABILITY_CLUSTERS
from data.build_release_fixtures import (
    _job_dimensions,
    _recruitment_type,
    _seniority,
    build_match_pairs,
)
from data.seed_local_demo import JOBS


FIXTURE = Path(__file__).parent / "fixtures" / "job_profile_golden.json"


def test_golden_profiles_cover_named_roles_and_java_slices():
    data = json.loads(FIXTURE.read_text("utf-8"))
    profiles = data["profiles"]
    assert len(profiles) >= 10
    names = {p["canonical_title"] for p in profiles}
    assert {"Java开发工程师", "软件测试工程师", "自动化测试工程师",
            "云计算工程师", "AI智能体开发工程师", "大模型算法工程师",
            "提示词工程师", "生成式人工智能系统测试员"} <= names
    java_slices = {(p["seniority"], p["recruitment_type"])
                   for p in profiles if p["canonical_title"] == "Java开发工程师"}
    assert {("junior", "campus"), ("middle", "social"), ("senior", "social")} <= java_slices


def test_each_golden_has_bounded_same_level_clusters_and_review_content():
    data = json.loads(FIXTURE.read_text("utf-8"))
    allowed = set(CAPABILITY_CLUSTERS)
    assert data["review_status"] == "engineering_baseline_requires_named_expert_signoff"
    for profile in data["profiles"]:
        required, bonus = profile["required_clusters"], profile["bonus_clusters"]
        assert 6 <= len(required) <= 10, profile["id"]
        assert 2 <= len(bonus) <= 4, profile["id"]
        assert len(set(required + bonus)) == len(required + bonus), profile["id"]
        assert set(required + bonus) <= allowed, profile["id"]
        assert profile["prohibited"] and profile["responsibilities"] and profile["scenarios"]


def test_demo_seed_covers_at_least_ten_unique_golden_jobs():
    data = json.loads(FIXTURE.read_text("utf-8"))
    golden_jobs = {profile["canonical_title"] for profile in data["profiles"]}
    seeded_jobs = {row[0] for row in JOBS}
    assert len(golden_jobs & seeded_jobs) >= 10
    assert golden_jobs <= seeded_jobs


def test_release_fixture_stratification_uses_explicit_source_markers():
    campus = "面向应届毕业生的校招岗位，无工作年限要求"
    assert _recruitment_type("算法工程师", campus) == "campus"
    assert _seniority("算法工程师", campus, "campus") == "junior"
    assert _recruitment_type("算法工程师", "要求3年以上相关工作经验") == "social"
    assert _seniority("高级算法工程师", "要求5年以上经验", "social") == "senior"
    assert _job_dimensions("汽车系统测试工程师") == {
        "job": "行业测试工程师", "track": "hardware", "industry": "automotive"}


def test_engineering_match_fixture_does_not_inject_truth_skills_as_input():
    resumes = [{
        "id": "resume-1", "category": "Data Science",
        "ground_truth_skills": ["Python", "机器学习", "TensorFlow"],
    }]
    pairs = build_match_pairs(resumes, count=3)
    assert {pair["ground_truth_label"] for pair in pairs} == {"high", "medium", "low"}
    assert all("resume_skills" not in pair for pair in pairs)
    assert all(pair["truth_independent"] is False for pair in pairs)
