"""新岗位定义后处理测试：五要素齐全 + 证据交叉验证（纯函数，无需联网/LLM）。"""
from app.services import discovery


def test_define_five_elements_and_evidence_crosscheck():
    data = {
        "job_title": "AI智能体开发工程师", "category": "人工智能", "level": "senior",
        "summary": "负责智能体研发与落地",
        "core_responsibilities": ["设计Agent编排", "构建工具调用框架", ""],
        "required_skills": [{"name": "LangChain", "level": "proficient"},
                            {"name": "提示工程"}, {"name": ""}],
        "bonus_skills": [{"name": "向量数据库"}, {"name": "LangChain"}],  # 重复应剔除
        "typical_scenarios": ["智能客服", "AI助手"],
    }
    evidence = [{"title": "t1", "content": "LangChain 与提示工程是 Agent 核心能力", "url": "http://a",
                 "company": "甲科技有限公司", "provider": "官网"},
                {"title": "t2", "content": "LangChain、提示工程、向量数据库用于检索", "url": "http://b",
                 "company": "乙智能有限公司", "provider": "公共平台"}]
    out = discovery._postprocess_definition(data, "AI智能体开发工程师", evidence)

    # 岗位定义五要素齐全
    assert out["job_title"] == "AI智能体开发工程师"
    assert out["category"] == "人工智能"
    assert out["core_responsibilities"] and "" not in out["core_responsibilities"]
    assert out["typical_scenarios"] == ["智能客服", "AI助手"]
    assert out["capabilities"]

    names = [c["name"] for c in out["capabilities"]]
    assert "LangChain" in names
    assert names.count("LangChain") == 1          # bonus 中重复被剔除
    # 证据中出现的技能 → web_verified + 必备
    lc = next(c for c in out["capabilities"] if c["name"] == "LangChain")
    assert lc["web_verified"] is True
    assert lc["importance"] == "required"
    assert lc["confidence"] >= 0.45 and lc["status"] == "active"
    # 含数据源溯源
    assert out["source_summary"]["evidence_count"] == 2


def test_define_bad_category_falls_back():
    out = discovery._postprocess_definition(
        {"job_title": "X", "category": "不存在的类目", "required_skills": [], "bonus_skills": []},
        "X", [])
    assert out["category"] == "人工智能"


def test_define_empty_skills_filtered():
    out = discovery._postprocess_definition(
        {"required_skills": [{"name": ""}, {"name": "   "}], "bonus_skills": []}, "kw", [])
    assert out["capabilities"] == []


def test_mature_java_veto_happens_before_network(monkeypatch):
    monkeypatch.setattr(discovery.clients, "multi_source_search",
                        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("不得联网")))
    out = discovery.discover_candidates("初级 Java 开发工程师")
    assert out["verdict"] == "ESTABLISHED"
    assert out["existing_job"] == "Java开发工程师"
    assert out["emergence_score"] == 0.0


def test_ambiguous_test_role_requires_track_before_network(monkeypatch):
    monkeypatch.setattr(discovery.clients, "multi_source_search",
                        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("不得联网")))
    out = discovery.discover_candidates("测试工程师")
    assert out["verdict"] == "AMBIGUOUS"
    assert out["resolution"]["requires_disambiguation"] is True
    assert len(out["resolution"]["candidates"]) >= 4


def test_emergence_uses_employers_history_and_authority_not_result_volume():
    evidence = [
        {"kind": "policy", "company": "甲机器人有限公司", "provider": "政府",
         "region": "上海", "industry": "manufacturing"},
        {"company": "乙智能有限公司", "provider": "招聘平台", "region": "深圳",
         "industry": "internet"},
    ]
    out = discovery.score_emergence(
        "具身智能机器人应用技术员", evidence, history={"2018": 0, "2024": 0, "2026": 8})
    assert out["verdict"] == "EMERGING"
    assert out["employer_count"] == 2
    assert out["signals"]["historical_novelty"] == 1.0


def test_missing_history_does_not_fabricate_novelty():
    out = discovery.score_emergence(
        "具身智能机器人应用技术员",
        [{"kind": "policy", "company": "甲公司"}, {"company": "乙公司"}])
    assert out["signals"]["history_available"] is False
    assert out["signals"]["historical_novelty"] is None
    historical = next(item for item in out["signals"]["signal_details"]
                      if item["key"] == "historical_novelty")
    assert historical["empty_reason"] == "历史样本不足"


def test_skill_confidence_is_computed_from_each_skills_supporting_employers():
    data = {"required_skills": [{"name": "LangChain"}, {"name": "模型微调"}],
            "bonus_skills": []}
    evidence = [
        {"title": "a", "content": "LangChain", "company": "甲公司", "provider": "p1"},
        {"title": "b", "content": "LangChain", "company": "乙公司", "provider": "p2"},
        {"title": "c", "content": "模型微调", "company": "甲公司", "provider": "p1"},
    ]
    caps = discovery._postprocess_definition(data, "具身智能应用工程师", evidence)["capabilities"]
    langchain = next(c for c in caps if c["name"] == "LangChain")
    tuning = next(c for c in caps if c["name"] == "模型微调")
    assert langchain["employer_count"] == 2 and langchain["status"] == "active"
    assert tuning["employer_count"] == 1 and tuning["status"] == "candidate"
    assert langchain["factors"] != tuning["factors"]


def test_skill_evidence_matching_uses_persisted_jd_text_not_claimed_counts():
    assert discovery._evidence_supports_skill(
        {"title": "后端岗位", "content": "负责 Spring 服务开发"}, "Spring")
    assert not discovery._evidence_supports_skill(
        {"title": "后端岗位", "content": "负责 Java 服务开发"}, "Selenium")
