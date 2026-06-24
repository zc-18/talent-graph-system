"""抽取后处理与客户端纯函数测试。"""
from app.services import extraction
from app import clients


def test_postprocess_dedup_and_norm():
    data = {"job_title": "ML工程师", "category": "人工智能", "level": "middle",
            "core_responsibilities": ["训练模型", ""],
            "required_skills": [{"name": "pytorch", "level": "proficient"},
                                {"name": "PyTorch", "level": "familiar"}],
            "bonus_skills": [{"name": "k8s"}, {"name": "pytorch"}],
            "typical_scenarios": ["推荐"]}
    out = extraction._postprocess(data)
    req_names = [s["name"] for s in out["required_skills"]]
    assert req_names.count("PyTorch") == 1          # 同义去重
    bonus_names = [s["name"] for s in out["bonus_skills"]]
    assert "PyTorch" not in bonus_names             # bonus 与 required 不重复
    assert "Kubernetes" in bonus_names


def test_postprocess_invalid_category_defaults():
    out = extraction._postprocess({"category": "玄学", "required_skills": []})
    assert out["category"] == "人工智能"


def test_rule_based_parse():
    out = extraction.parse_jd_rule_based("熟悉 Spark 与 Hadoop，了解 Kafka")
    names = {s["name"] for s in out["required_skills"]}
    assert {"Spark", "Hadoop", "Kafka"}.issubset(names)


def test_safe_json_variants():
    assert clients._safe_json('{"a":1}') == {"a": 1}
    assert clients._safe_json('```json\n{"a":2}\n```') == {"a": 2}
    assert clients._safe_json('前缀{"a":3}后缀') == {"a": 3}
    assert clients._safe_json("not json") == {}


def test_cosine():
    assert abs(clients.cosine([1, 0], [1, 0]) - 1.0) < 1e-6
    assert abs(clients.cosine([1, 0], [0, 1])) < 1e-6
    assert clients.cosine([], [1]) == 0.0
