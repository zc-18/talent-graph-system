"""统一置信度引擎测试（2026-07 整改：老师意见④）。"""
import math
from app.services import confidence as C


def test_weights_sum_to_one():
    assert abs(sum(C.WEIGHTS.values()) - 1.0) < 1e-9


def test_compute_bounds_and_rounding():
    assert C.compute({}) == 0.0
    full = {k: 1.0 for k in C.WEIGHTS}
    assert C.compute(full) == 1.0
    # 超界因子被裁剪
    assert C.compute({k: 5.0 for k in C.WEIGHTS}) == 1.0
    assert C.compute({k: -1.0 for k in C.WEIGHTS}) == 0.0


def test_compute_is_monotonic_in_each_factor():
    base = {k: 0.5 for k in C.WEIGHTS}
    s0 = C.compute(base)
    for k in C.WEIGHTS:
        up = dict(base)
        up[k] = 0.9
        assert C.compute(up) > s0, f"factor {k} not monotonic"


def test_factors_from_jd():
    f = C.factors_from_jd(support_ratio=0.8, platforms={"a", "b", "c"},
                          avg_freshness=0.9, avg_authority=1.0, has_web=True)
    assert f["support"] == 0.8
    assert abs(f["diversity"] - min(1.0, 3 / C.DIVERSITY_CAP)) < 1e-9
    assert f["authority"] == 1.0
    assert f["external"] == 1.0
    assert 0 <= C.compute(f) <= 1


def test_unknown_employer_gets_no_diversity_credit():
    factors = C.factors_from_jd(0.8, set(), 0.9, 0.7, False)
    assert factors["diversity"] == 0.0
    web = C.factors_from_web(True, set(), 99)
    assert web["diversity"] == 0.0


def test_factors_from_web_authority_doc_beats_plain_web():
    plain = C.factors_from_web(in_evidence=True, providers={"tavily"}, ev_count=4)
    with_doc = C.factors_from_web(in_evidence=True, providers={"tavily"}, ev_count=4,
                                  has_authority_doc=True)
    assert C.compute(with_doc) > C.compute(plain)
    assert with_doc["authority"] == 1.0


def test_explain_contributions_sum_to_score():
    f = C.factors_from_jd(0.6, {"a", "b"}, 0.8, 0.7, False)
    ex = C.explain(f)
    total = sum(item["contribution"] for item in ex["factors"])
    assert math.isclose(total, ex["score"], abs_tol=1e-3)
    assert "支持率" in ex["formula"]
    assert len(ex["factors"]) == 5
