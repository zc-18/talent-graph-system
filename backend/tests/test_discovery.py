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
    evidence = [{"title": "t1", "content": "LangChain 与提示工程是 Agent 核心能力", "url": "http://a"},
                {"title": "t2", "content": "向量数据库 用于检索", "url": "http://b"}]
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
