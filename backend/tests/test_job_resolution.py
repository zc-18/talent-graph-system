from app.services.job_resolution import resolve_job_query, track_conflict


def test_java_query_resolves_all_dimensions():
    out = resolve_job_query("校招初级 Java 开发工程师")
    assert out.canonical_title == "Java开发工程师"
    assert out.track == "software"
    assert out.seniority == "junior"
    assert out.recruitment_type == "campus"
    assert out.is_established is True


def test_generic_test_engineer_requires_disambiguation():
    out = resolve_job_query("高级测试工程师")
    assert out.requires_disambiguation is True
    assert {c["track"] for c in out.candidates} == {"software", "hardware"}
    assert {c["canonical_title"] for c in out.candidates} == {
        "软件测试工程师", "硬件系统测试工程师", "系统测试工程师", "行业测试工程师"}


def test_software_system_test_does_not_mix_hardware_track():
    out = resolve_job_query("软件系统测试工程师")
    assert out.canonical_title == "系统测试工程师"
    assert out.track == "software"
    assert out.requires_disambiguation is False
    assert track_conflict(out.track, "电磁兼容测试") is True
    assert track_conflict(out.track, "Selenium 自动化测试") is False


def test_industry_and_social_recruitment_are_structured():
    out = resolve_job_query("社招高级汽车硬件系统测试工程师")
    assert out.industry == "automotive"
    assert out.track == "hardware"
    assert out.recruitment_type == "social"
    assert out.seniority == "senior"


def test_mature_counterexamples_are_established():
    for query in ["Java", "初级前端开发工程师", "软件测试工程师", "运维工程师",
                  "软件系统测试工程师", "汽车系统测试工程师"]:
        assert resolve_job_query(query).is_established, query


def test_industry_test_and_etl_resolve_to_governed_tracks():
    automotive = resolve_job_query("社招汽车系统测试工程师")
    assert automotive.canonical_title == "行业测试工程师"
    assert automotive.industry == "automotive"
    assert automotive.track == "hardware"
    etl = resolve_job_query("高级ETL开发工程师")
    assert etl.canonical_title == "ETL开发工程师"
    assert etl.track == "data"
