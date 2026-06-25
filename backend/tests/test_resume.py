"""简历解析后处理与 PII 脱敏测试（纯函数，无需 LLM/DB）。"""
from app.services import resume


def test_postprocess_normalizes_and_dedups():
    data = {"candidate_name": "张三", "years_experience": "3",
            "skills": ["torch", "ML", "Python", "python"],   # 同义词 + 重复
            "skill_levels": {"torch": "expert"},
            "projects": ["项目A"], "titles": ["算法工程师"]}
    text = "精通 PyTorch、k8s、机器学习，熟悉分布式系统"
    out = resume._postprocess_resume(data, text)
    assert "PyTorch" in out["skills"]            # torch -> PyTorch
    assert "机器学习" in out["skills"]            # ML -> 机器学习
    assert out["skills"].count("Python") == 1     # 去重
    assert "Kubernetes" in out["skills"]          # 词典兜底：text 中 k8s
    assert out["years_experience"] == 3.0
    assert out["skill_levels"]["PyTorch"] == "expert"
    assert "PyTorch" in out["skill_categories"]


def test_postprocess_bad_years_defaults_zero():
    out = resume._postprocess_resume({"skills": [], "years_experience": "abc"}, "x" * 20)
    assert out["years_experience"] == 0.0


def test_redact_for_storage_strips_pii():
    parsed = {"candidate_name": "李四", "skills": ["Python"],
              "skill_levels": {"Python": "proficient"},
              "projects": ["保密项目"], "titles": ["CTO"],
              "years_experience": 5, "education": "硕士"}
    red = resume.redact_for_storage(parsed)
    # PII 字段被剔除
    assert "candidate_name" not in red
    assert "projects" not in red
    assert "titles" not in red
    # 非身份的技能要素保留
    assert red["skills"] == ["Python"]
    assert red["years_experience"] == 5
    assert red["education"] == "硕士"


def test_parse_resume_empty_text_no_llm():
    out = resume.parse_resume("")
    assert out["skills"] == [] and out["raw_skill_count"] == 0
