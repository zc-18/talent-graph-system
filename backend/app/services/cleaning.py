"""多源异构数据清洗与交叉验证。

解决赛题创新点①：数据"时滞(lag)"、"噪音(noise)"、"抄袭(plagiarism)"、"通胀(inflation)"。
- 精确去重：normalized text 的 md5
- 近似去重/抄袭：SimHash 海明距离 + 语义向量余弦
- 时滞：publish_date 与采集时间差，越旧权重越低
- 通胀：单条 JD 技能数显著高于同岗位中位数，且大量低频技能 → 通胀标记
"""
from __future__ import annotations
import re
import hashlib
from datetime import datetime
from collections import Counter

_TOKEN_RE = re.compile(r"[A-Za-z0-9\+\#\.]+|[一-鿿]")
_STOP = {"的", "和", "与", "及", "等", "或", "在", "为", "对", "有", "了", "并", "能", "可"}


def normalize_text(text: str) -> str:
    """归一化：小写、去多余空白、去标点，用于精确去重。"""
    t = (text or "").lower()
    t = re.sub(r"\s+", "", t)
    t = re.sub(r"[\s\W_]+", "", t, flags=re.UNICODE)
    return t


def exact_hash(text: str) -> str:
    return hashlib.md5(normalize_text(text).encode("utf-8")).hexdigest()


def tokenize(text: str) -> list[str]:
    toks = _TOKEN_RE.findall((text or "").lower())
    return [t for t in toks if t not in _STOP and len(t) >= 1]


def simhash(text: str, bits: int = 64) -> int:
    """64 位 SimHash。"""
    tokens = tokenize(text)
    if not tokens:
        return 0
    v = [0] * bits
    weights = Counter(tokens)
    for tok, w in weights.items():
        h = int(hashlib.md5(tok.encode("utf-8")).hexdigest(), 16)
        for i in range(bits):
            v[i] += w if (h >> i) & 1 else -w
    out = 0
    for i in range(bits):
        if v[i] > 0:
            out |= (1 << i)
    return out


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def is_near_duplicate(sh_a: int, sh_b: int, threshold: int = 3) -> bool:
    """海明距离 <= threshold 视为近似重复（抄袭）。"""
    return hamming(sh_a, sh_b) <= threshold


def lag_days(publish_date: datetime | None, ref: datetime | None = None) -> int:
    if not publish_date:
        return 999
    ref = ref or datetime.utcnow()
    return max(0, (ref - publish_date).days)


def freshness_weight(days: int, half_life: int = 180) -> float:
    """新鲜度权重：指数衰减，half_life 天衰减到 0.5。"""
    return float(0.5 ** (days / half_life))


def quality_score(text: str, lag: int, is_dup: bool) -> float:
    """综合质量分：长度合理性 + 新鲜度 - 重复惩罚。"""
    length = len(text or "")
    len_score = min(1.0, length / 400.0) if length < 400 else max(0.3, 1.0 - (length - 2000) / 4000.0)
    len_score = max(0.1, min(1.0, len_score))
    fresh = freshness_weight(lag)
    score = 0.5 * len_score + 0.5 * fresh
    if is_dup:
        score *= 0.3
    return round(score, 4)


def detect_inflation(skill_count: int, median_count: float, rare_ratio: float) -> bool:
    """能力通胀检测。

    通胀特征：单条 JD 技能数显著高于同岗位中位数，且其中大量技能是"非共识/冷门"技能
    （在簇内极少出现）。以 rare_ratio 为主判别量。

    skill_count: 本条 JD 抽取的技能数
    median_count: 同岗位 JD 技能数中位数
    rare_ratio: 本条 JD 中"非共识/冷门"技能占比
    """
    if median_count <= 0:
        return False
    over = skill_count >= max(10, median_count * 1.4) and (skill_count - median_count) >= 4
    return bool(over and rare_ratio > 0.35)
