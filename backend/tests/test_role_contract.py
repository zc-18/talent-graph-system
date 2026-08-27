from app.services.quality_eval import build_shadow_report, evaluate_contract, shadow_diff
from app.services.role_contract import build_role_contract, matching_capabilities


def test_persisted_level_slice_changes_role_contract():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app import models
    from app.db import Base
    from app.services.role_contract import build_contract_from_job

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        job = models.Job(name="Java开发工程师", slug="java-contract", status="published",
                         category="云计算与工程", level="middle", track="software")
        java = models.Skill(name="Java", normalized_name="Java", category="编程语言")
        spring = models.Skill(name="Spring", normalized_name="Spring", category="云计算与工程")
        k8s = models.Skill(name="Kubernetes", normalized_name="Kubernetes", category="云计算与工程")
        db.add_all([job, java, spring, k8s])
        db.flush()
        db.add_all([
            models.JobLevelSkill(job_id=job.id, level="junior", skill_id=java.id,
                                 importance="required", weight=0.9, confidence=0.9,
                                 source_count=2, jd_count=8),
            models.JobLevelSkill(job_id=job.id, level="junior", skill_id=spring.id,
                                 importance="required", weight=0.7, confidence=0.85,
                                 source_count=2, jd_count=8),
            models.JobLevelSkill(job_id=job.id, level="senior", skill_id=java.id,
                                 importance="required", weight=0.8, confidence=0.9,
                                 source_count=3, jd_count=10),
            models.JobLevelSkill(job_id=job.id, level="senior", skill_id=k8s.id,
                                 importance="required", weight=0.85, confidence=0.88,
                                 source_count=3, jd_count=10),
            models.JobLevelSkill(job_id=job.id, level="middle", recruitment_type="campus",
                                 track="software", industry="general", skill_id=spring.id,
                                 importance="required", weight=0.75, confidence=0.84,
                                 source_count=2, jd_count=6),
            models.JobLevelSkill(job_id=job.id, level="middle", recruitment_type="social",
                                 track="software", industry="general", skill_id=k8s.id,
                                 importance="required", weight=0.82, confidence=0.87,
                                 source_count=3, jd_count=9),
        ])
        db.commit()
        junior = build_contract_from_job(db, job, seniority="junior")
        senior = build_contract_from_job(db, job, seniority="senior")
        assert junior["slice_source"] == senior["slice_source"] == "job_level_skill"
        assert {c["name"] for c in junior["clusters"]} == {
            "编程与工程基础", "后端框架与服务"}
        assert {c["name"] for c in senior["clusters"]} == {
            "编程与工程基础", "云原生与运维"}
        campus = build_contract_from_job(
            db, job, seniority="middle", recruitment_type="campus", track="software")
        social = build_contract_from_job(
            db, job, seniority="middle", recruitment_type="social", track="software")
        assert [c["name"] for c in campus["clusters"]] == ["后端框架与服务"]
        assert [c["name"] for c in social["clusters"]] == ["云原生与运维"]
        assert campus["slice_resolution"]["exact"] is True
        assert social["slice_resolution"]["exact"] is True
        assert junior["slice_resolution"]["exact"] is False
        assert {"recruitment_type", "track"} <= set(
            junior["slice_resolution"]["fallback_dimensions"])
    finally:
        db.close()


def _cap(name, importance="required", weight=0.8, employers=3, **extra):
    return {"name": name, "importance": importance, "weight": weight,
            "confidence": 0.88, "support_ratio": 0.7,
            "level_required": "proficient", "status": "active",
            "employer_count": employers, **extra}


def test_contract_converges_to_same_level_8_to_12_clusters():
    caps = [
        _cap("Java"), _cap("Spring"), _cap("MySQL"), _cap("消息队列"),
        _cap("功能测试"), _cap("Selenium"), _cap("Git"), _cap("Docker"),
        _cap("机器学习"), _cap("LangChain"),
        _cap("Spark", "bonus", 0.5), _cap("数字电路", "bonus", 0.5),
        _cap("TCP/IP", "bonus", 0.4), _cap("团队协作", "bonus", 0.3),
    ]
    contract = build_role_contract(caps, job_name="复合工程岗位", track="product")
    assert contract["status"] == "ready"
    assert 8 <= len(contract["clusters"]) <= 12
    assert contract["summary"]["required_count"] <= 10
    assert contract["summary"]["bonus_count"] <= 4
    assert len({c["name"] for c in contract["clusters"]}) == len(contract["clusters"])
    assert len(matching_capabilities(contract)) == len(contract["clusters"])


def test_contract_requires_employer_gate_and_blocks_offtrack_skills():
    caps = [_cap("Java", employers=1), _cap("电磁兼容测试"), _cap("Selenium")]
    contract = build_role_contract(caps, job_name="软件测试工程师", track="software")
    assert [s["name"] for c in contract["clusters"] for s in c["skills"]] == ["Selenium"]
    assert contract["status"] == "evidence_insufficient"
    assert contract["summary"]["rejected"] == {
        "inactive": 0, "employer_gate": 1, "track_conflict": 1}


def test_java_contract_blocks_frontend_noise():
    contract = build_role_contract(
        [_cap("Java"), _cap("CSS"), _cap("jQuery"), _cap("Spring")],
        job_name="Java开发工程师", track="software")
    names = {skill["name"] for cluster in contract["clusters"] for skill in cluster["skills"]}
    assert names == {"Java", "Spring"}
    assert contract["summary"]["rejected"]["track_conflict"] == 2


def test_cluster_importance_uses_weighted_boundary_not_any_required_vote():
    contract = build_role_contract([
        _cap("Java", "required", weight=0.1, support_ratio=0.1),
        _cap("Python", "bonus", weight=0.9, support_ratio=0.9),
    ], job_name="复合工程岗位", track="product")
    cluster = contract["clusters"][0]
    assert cluster["name"] == "编程与工程基础"
    assert cluster["importance"] == "bonus"
    assert cluster["importance_evidence"]["bonus_score"] > (
        cluster["importance_evidence"]["required_score"])


def test_quality_eval_and_shadow_diff_are_auditable():
    contract = {"clusters": [
        {"name": "软件测试设计", "importance": "required",
         "skills": [{"name": "功能测试"}]},
        {"name": "测试自动化", "importance": "required",
         "skills": [{"name": "Selenium"}]},
    ]}
    result = evaluate_contract(contract, {
        "required_clusters": ["软件测试设计", "测试自动化"],
        "bonus_clusters": [], "prohibited": ["电磁兼容"]})
    assert result["precision_at_10"] == 1.0
    assert result["passed"] is True

    diff = shadow_diff("Java开发工程师",
                       [{"name": "Java", "status": "active"}],
                       [{"name": "Java", "status": "candidate"}])
    assert diff["transitions"] == {"active->candidate": 1}
    assert diff["changed_capabilities"] == ["Java"]
    report = build_shadow_report([{
        "job_name": "Java开发工程师",
        "before": [{"name": "Java", "status": "active"}],
        "after": [{"name": "Java", "status": "candidate"}],
    }])
    assert report["before"]["active"] == 1
    assert report["after"]["candidate"] == 1
