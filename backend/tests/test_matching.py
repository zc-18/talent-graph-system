"""人岗匹配与学习路径测试。"""
from app.services import matching


def _cap(name, imp="required", weight=0.8, level="familiar", cat="人工智能"):
    return {"name": name, "importance": imp, "weight": weight, "level_required": level,
            "category": cat, "confidence": 0.9, "status": "active"}


def test_full_match():
    caps = [_cap("机器学习"), _cap("Python"), _cap("PyTorch", "bonus", 0.4)]
    res = matching.match(caps, ["机器学习", "Python", "PyTorch"], use_semantic=False)
    assert res["overall_score"] >= 85
    assert res["level"] == "高度匹配"
    assert res["missing_required"] == []


def test_partial_match_identifies_gaps():
    caps = [_cap("机器学习"), _cap("Python"), _cap("深度学习")]
    res = matching.match(caps, ["Python"], use_semantic=False)
    missing = {m["name"] for m in res["missing_required"]}
    assert missing == {"机器学习", "深度学习"}
    assert res["overall_score"] < 70


def test_role_contract_cluster_matches_once_via_any_child_skill():
    caps = [{**_cap("编程与工程基础"),
             "skills": [{"name": "Java"}, {"name": "Python"}]}]
    one = matching.match(caps, ["Java"], use_semantic=False)
    both = matching.match(caps, ["Java", "Python"], use_semantic=False)
    assert one["summary"]["required_matched"] == 1
    assert both["summary"]["required_matched"] == 1
    assert len(both["matched_skills"]) == 1
    assert one["matched_skills"][0]["name"] == "编程与工程基础"
    assert one["matched_skills"][0]["matched_skill"] == "Java"
    assert one["matched_skills"][0]["cluster_skills"] == ["Java", "Python"]


def test_missing_cluster_exposes_child_skill_options():
    caps = [{**_cap("数据与存储"),
             "skills": [{"name": "MySQL"}, {"name": "Redis"}]}]
    result = matching.match(caps, [], use_semantic=False)
    assert result["missing_required"][0]["name"] == "数据与存储"
    assert result["missing_required"][0]["skills"] == ["MySQL", "Redis"]


def test_level_match_penalty():
    caps = [_cap("机器学习", level="expert")]
    low = matching.match(caps, ["机器学习"], {"机器学习": "familiar"}, use_semantic=False)
    high = matching.match(caps, ["机器学习"], {"机器学习": "expert"}, use_semantic=False)
    assert high["overall_score"] >= low["overall_score"]


def test_semantic_guard_skips_english(monkeypatch):
    # 英文技能即便嵌入退化(返回相同向量)也不应误匹配
    monkeypatch.setattr(matching.clients, "embed_batch",
                        lambda xs: [[1.0, 0.0] for _ in xs])
    caps = [_cap("Spark")]
    res = matching.match(caps, ["Hive"], use_semantic=True)
    assert {m["name"] for m in res["missing_required"]} == {"Spark"}


def test_build_learning_path_topological():
    missing = [{"name": "深度学习", "weight": 0.9}, {"name": "机器学习", "weight": 0.8},
               {"name": "Python", "weight": 0.7}]
    rels = {"深度学习": ["机器学习"], "机器学习": ["Python"]}
    path = matching.build_learning_path(missing, rels)
    order = [p["skill"] for p in path]
    assert order.index("Python") < order.index("机器学习") < order.index("深度学习")


def test_grade_bands():
    assert matching._grade(90) == "高度匹配"
    assert matching._grade(60) == "基本匹配"
    assert matching._grade(10) == "差距较大"


def test_has_cjk():
    assert matching._has_cjk("机器学习")
    assert not matching._has_cjk("PyTorch")
