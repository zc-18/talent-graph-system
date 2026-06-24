"""数据清洗模块测试。"""
from datetime import datetime, timedelta
from app.services import cleaning


def test_exact_hash_normalizes():
    assert cleaning.exact_hash("Java 开发  工程师") == cleaning.exact_hash("java开发工程师")
    assert cleaning.exact_hash("abc") != cleaning.exact_hash("abd")


def test_simhash_identical_and_near():
    a = cleaning.simhash("机器学习工程师 负责模型训练 熟悉PyTorch 深度学习")
    b = cleaning.simhash("机器学习工程师 负责模型训练 熟悉PyTorch 深度学习")
    assert a == b
    assert cleaning.hamming(a, b) == 0
    assert cleaning.is_near_duplicate(a, b, threshold=3)


def test_simhash_different():
    a = cleaning.simhash("Java后端开发 Spring MySQL 分布式")
    b = cleaning.simhash("前端工程师 React Vue CSS 浏览器渲染")
    assert cleaning.hamming(a, b) > 3
    assert not cleaning.is_near_duplicate(a, b, threshold=3)


def test_lag_and_freshness():
    assert cleaning.lag_days(None) == 999
    d = datetime.utcnow() - timedelta(days=180)
    assert cleaning.lag_days(d) >= 179
    assert 0.45 < cleaning.freshness_weight(180) < 0.55
    assert cleaning.freshness_weight(0) == 1.0


def test_quality_score_penalizes_duplicates():
    s_dup = cleaning.quality_score("a" * 500, 10, True)
    s_ok = cleaning.quality_score("a" * 500, 10, False)
    assert s_ok > s_dup
    assert 0 <= s_dup <= 1


def test_detect_inflation():
    # 技能数远高于中位数 + 大量冷门技能 -> 通胀
    assert cleaning.detect_inflation(14, 7, 0.5) is True
    # 正常 JD
    assert cleaning.detect_inflation(8, 7, 0.1) is False
    assert cleaning.detect_inflation(10, 0, 0.9) is False


def test_tokenize_filters_stopwords():
    toks = cleaning.tokenize("熟悉 的 Java 和 Spring")
    assert "的" not in toks and "和" not in toks
    assert "java" in toks
