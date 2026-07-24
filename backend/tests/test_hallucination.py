"""反幻觉交叉验证聚合测试。"""
from app.services import hallucination as H


def _skill(name, imp="required", level="proficient", cat="人工智能", st="hard"):
    return {"name": name, "importance": imp, "level": level, "category": cat,
            "skill_type": st, "raw": name}


def _jd(req, bonus=None, rid=1, dup=False, lag=10):
    return {"required_skills": [_skill(s) for s in req],
            "bonus_skills": [_skill(s, "bonus") for s in (bonus or [])],
            "lag_days": lag, "is_duplicate": dup, "raw_jd_id": rid, "source": "t"}


def test_cross_validation_confidence_increases_with_sources():
    jds = [_jd(["机器学习"], rid=i) for i in range(1, 6)]
    out = H.aggregate_capabilities(jds)
    ml = [c for c in out["capabilities"] if c["name"] == "机器学习"][0]
    assert ml["source_count"] == 5
    # 单一平台、无外部验证时线性公式封顶约 0.66；status 应为 active
    assert ml["confidence"] > 0.6
    assert ml["status"] == "active"
    assert "factors" in ml


def test_confidence_rewards_platform_diversity_and_authority():
    # 相同支持率下：多平台 + 官方权威来源 → 置信度更高（多样性与权威度因子）
    jds = [_jd(["机器学习"], rid=i) for i in range(1, 6)]
    base = H.aggregate_capabilities(jds)["capabilities"][0]["confidence"]
    meta = {i: {"platform": f"p{i}", "authority": 1.0} for i in range(1, 6)}
    rich = H.aggregate_capabilities(jds, source_meta=meta)["capabilities"][0]
    assert rich["confidence"] > base
    assert rich["confidence"] > 0.85
    assert rich["factors"]["diversity"] == 1.0
    assert rich["factors"]["authority"] == 1.0


def test_single_source_required_demoted_to_bonus():
    # 只有1条来源声称"required"，缺乏交叉验证 -> 降级为 bonus（防幻觉）
    jds = [_jd(["机器学习"], rid=1), _jd(["机器学习"], rid=2), _jd(["量子计算"], rid=3)]
    out = H.aggregate_capabilities(jds)
    q = [c for c in out["capabilities"] if c["name"] == "量子计算"][0]
    assert q["importance"] == "bonus"


def test_duplicates_excluded_from_validation():
    jds = [_jd(["机器学习"], rid=1), _jd(["机器学习"], rid=2, dup=True)]
    out = H.aggregate_capabilities(jds)
    assert out["stats"]["valid_jds"] == 1
    assert out["stats"]["duplicate_jds"] == 1


def test_web_evidence_boosts_confidence():
    jds = [_jd(["智能体"], rid=1)]
    base = H.aggregate_capabilities(jds)["capabilities"][0]["confidence"]
    boosted = H.aggregate_capabilities(jds, web_evidence_skills={"智能体"})["capabilities"][0]["confidence"]
    assert boosted > base


def test_job_confidence_average():
    caps = [{"status": "active", "confidence": 0.8}, {"status": "active", "confidence": 0.6},
            {"status": "candidate", "confidence": 0.1}]
    assert abs(H.job_confidence(caps) - 0.7) < 1e-6
    assert H.job_confidence([]) == 0.0


def test_mode_helper():
    assert H._mode(["a", "b", "a"]) == "a"
    assert H._mode([]) is None
