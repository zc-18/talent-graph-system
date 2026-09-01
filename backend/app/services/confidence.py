"""统一置信度引擎 —— 全系统唯一的置信度公式（2026-07 整改）。

老师意见④：置信度必须有具体、讲得明白的公式。此前系统有两套不一致的公式
（hallucination.py 的 sigmoid 加权、discovery.py 的拼凑加法），本模块将其统一为
一个可解释的线性加权和：

    C = 0.35·支持率 + 0.20·来源多样性 + 0.15·时效性 + 0.20·来源权威度 + 0.10·外部验证

五个因子均归一化到 [0,1]，权重和为 1，因此 C 天然落在 [0,1]，每一分都可以拆解
解释（前端点击置信度徽章可查看因子分解）。

因子口径（写入文档的同一份定义）：
- 支持率 support     = 提及该技能的独立 JD 数 / 该岗位有效（非重复）JD 总数
- 来源多样性 diversity = 独立雇主实体数 / DIVERSITY_CAP（当前为 3，封顶 1.0；渠道仅作质量因子展示）
- 时效性 freshness   = 各来源新鲜度均值（cleaning.freshness_weight，半衰期 180 天）
- 来源权威度 authority = 各来源权威度均值（企业官网/政府平台 1.0，公开数据集 0.7，网络检索 0.6）
- 外部验证 external   = 有 Tavily/Serper 检索或权威文件（人社部/头部报告）佐证则 1，否则 0

阈值机制（准入/降级）不属于公式本身，保持 hallucination.py 原有行为不变。
"""
from __future__ import annotations

# 权重：真实语料重建后校准一次并冻结（校准过程记录在测试报告）
WEIGHTS: dict[str, float] = {
    "support": 0.35,      # 支持率
    "diversity": 0.20,    # 来源多样性
    "freshness": 0.15,    # 时效性
    "authority": 0.20,    # 来源权威度
    "external": 0.10,     # 外部验证
}

FACTOR_LABELS: dict[str, str] = {
    "support": "支持率",
    "diversity": "来源多样性",
    "freshness": "时效性",
    "authority": "来源权威度",
    "external": "外部验证",
}

FORMULA_TEXT = "C = 0.35×支持率 + 0.20×来源多样性 + 0.15×时效性 + 0.20×来源权威度 + 0.10×外部验证"

# 来源类型 → 权威度（RawJD.source_authority 的默认映射）
SOURCE_AUTHORITY: dict[str, float] = {
    "official": 1.0,      # 企业官方招聘官网
    "gov": 1.0,           # 政府公共招聘平台
    "dataset": 0.7,       # 公开数据集（天池/Kaggle 等）
    "aggregator": 0.8,    # 主流招聘平台（隔离批次）
    "web": 0.6,           # 网络检索证据
}

DIVERSITY_CAP = 3  # 独立雇主实体达到 3 即视为充分多样；平台只是传播渠道，不能重复计独立来源。


def _clip01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def compute(factors: dict[str, float]) -> float:
    """线性加权和：C = Σ w_i · f_i，四舍五入 4 位。缺失因子按 0 计。"""
    score = sum(WEIGHTS[k] * _clip01(factors.get(k, 0.0)) for k in WEIGHTS)
    return round(score, 4)


def support_ratio(factors: dict | None) -> float:
    """从持久化的 factors 里取支持率，供不做置信度计算、只需展示这一项的调用方。

    factors 是 MySQL JSON 列，读回来可能是 None、可能缺键、数值末位也可能漂
    （见 tests/test_confidence.py 的浮点往返用例），所以统一走这里而不是各处
    自己 ``.get("support", 0)``：岗位详情页曾因为构造契约时漏掉这一项，
    每个能力簇都显示 0%。
    """
    return _clip01((factors or {}).get("support", 0.0) or 0.0)


def factors_from_jd(
    support_ratio: float,
    platforms: set[str] | None,
    avg_freshness: float,
    avg_authority: float,
    has_web: bool,
) -> dict[str, float]:
    """既有岗位路径的因子。

    ``platforms`` 是为兼容既有调用保留的参数名，传入值必须是去重后的雇主实体键；
    为空集时多样性为 0；未知雇主不能获得独立来源分。
    """
    n_platforms = len(platforms or ())
    return {
        "support": _clip01(support_ratio),
        "diversity": _clip01(n_platforms / DIVERSITY_CAP),
        "freshness": _clip01(avg_freshness),
        "authority": _clip01(avg_authority),
        "external": 1.0 if has_web else 0.0,
    }


def factors_from_web(
    in_evidence: bool,
    providers: set[str] | None,
    ev_count: int,
    has_authority_doc: bool = False,
    avg_freshness: float = 1.0,
) -> dict[str, float]:
    """新岗位发现路径（网络证据为主）的因子。

    - 支持率：技能名是否出现在证据文本中（出现=1，未出现=0.35，与旧 base 行为对齐）
    - 多样性：独立雇主实体数 / DIVERSITY_CAP 封顶；检索渠道和证据条数不计作雇主
    - 权威度：有部委/报告级文件佐证 1.0，仅网络检索 0.6
    """
    # Discovery passes independent employer entity keys here. An empty set is
    # genuinely no source diversity, not a synthetic first source.
    n_prov = len(providers or ())
    _ = ev_count  # retained for call compatibility; never used as employer diversity
    return {
        "support": 1.0 if in_evidence else 0.35,
        "diversity": _clip01(n_prov / DIVERSITY_CAP),
        "freshness": _clip01(avg_freshness),
        "authority": 1.0 if has_authority_doc else SOURCE_AUTHORITY["web"],
        "external": 1.0 if (in_evidence or has_authority_doc) else 0.0,
    }


def explain(factors: dict[str, float]) -> dict:
    """供 API 直出的解释结构：因子值、权重、贡献分、公式文本、总分。"""
    items = []
    for k in WEIGHTS:
        v = _clip01(factors.get(k, 0.0))
        items.append({
            "key": k,
            "label": FACTOR_LABELS[k],
            "value": round(v, 4),
            "weight": WEIGHTS[k],
            "contribution": round(WEIGHTS[k] * v, 4),
        })
    return {
        "formula": FORMULA_TEXT,
        "factors": items,
        "score": compute(factors),
    }
