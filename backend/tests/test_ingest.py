"""岗位标题归一化 / 领域过滤 / PII 打码的行为测试（纯内存，不连数据库）。"""
from app.services import ingest
from app.services.job_resolution import title_on_target
from data.import_raw import infer_title_cluster, is_it_domain
from data.collect.base import mask_pii

LS, PS = " ", " "   # Unicode 行/段分隔符（源码里用转义写，避免被编辑器吃掉）


# ---------------- title_key ----------------
def test_governed_exact_title_blocks_conflicting_cluster_hint():
    """明确的成熟岗位不能因采集检索词而被混入另一个岗位簇。"""
    assert ingest.title_key("Java开发工程师", "大模型算法") == "Java开发工程师"


def test_unknown_cluster_hint_falls_back_to_title():
    assert ingest.title_key("Java开发工程师", "__不存在的簇__") == "Java开发工程师"


def test_internal_map_hit_after_decoration_strip():
    assert ingest.title_key("高级Java开发工程师（杭州）", None) == "Java开发工程师"


def test_keyword_backfill_llm_synonym():
    """queries.json 只有「大模型」，真实标题常写「大语言模型」，须归到同一岗位簇。"""
    assert ingest.title_key("资深大语言模型算法（上海）", None) == "大模型算法工程师"


def test_longer_keyword_wins():
    """长词优先：推理加速 → 推理优化岗，而不是被 llm 抢走。"""
    assert ingest.title_key("LLM推理加速工程师", None) == "大模型推理优化工程师"


def test_non_it_titles_stay_unmapped():
    """非信息技术岗不得落到任何规范岗位名，否则图里会长出一次性噪声岗位。"""
    canon = ingest.canonical_job_names()
    for t in ["整车附件产品经理", "行政经理", "人力资源专员", "会计主管", "销售Agent"]:
        assert ingest.title_key(t, None) not in canon, t


def test_ambiguous_test_title_uses_governed_track_or_industry_slice():
    assert ingest.title_key("测试工程师", None, "software", "general") == "系统测试工程师"
    assert ingest.title_key("测试工程师", None, "hardware", "general") == "硬件系统测试工程师"
    assert ingest.title_key("测试工程师", None, None, "automotive") == "行业测试工程师"


def test_import_dimensions_reject_unknown_values_and_title_track_conflicts():
    java = ingest.resolve_job_query("高级Java开发工程师")
    dimensions = ingest._resolved_dimensions({
        "track": "hardware", "industry": "not-an-industry",
        "recruitment_type": "vip", "inferred_level": "expert"}, java)
    assert dimensions == {
        "track": "software", "industry": "general",
        "recruitment_type": "unspecified", "inferred_level": "senior"}
    ambiguous = ingest._resolved_dimensions({}, ingest.resolve_job_query("测试工程师"))
    assert ambiguous["track"] is None


def test_canonical_job_names_is_exactly_the_governed_title_map():
    """查询解析词表不是发布岗位清单，只有 title_map 策展的 32 岗能独立建图。"""
    canon = ingest.canonical_job_names()
    assert len(canon) == 32
    assert "大模型算法工程师" in canon
    assert "Java开发工程师" in canon
    # 这些标题仍可被自由文本查询识别，但语料不足、未进 title_map，不能在全量重建时
    # 长成只有 1–14 条 JD 的一次性公开岗位。
    for name in ["前端开发工程师", "Python开发工程师", "C++开发工程师",
                 "算法工程师", "软件测试工程师", "自动化测试工程师",
                 "硬件系统测试工程师", "系统测试工程师", "行业测试工程师",
                 "ETL开发工程师"]:
        assert name not in canon, name
    assert "整车附件产品经理" not in canon


# ---------------- 领域过滤 ----------------
def test_is_it_domain_by_title():
    assert is_it_domain("大模型算法工程师", "负责相关工作") is True


def test_is_it_domain_rejects_non_tech():
    body = "负责公司日常行政事务、会议组织、办公用品采购与员工关怀。"
    assert is_it_domain("行政经理", body) is False


def test_is_it_domain_by_body_when_title_generic():
    """泛标题但正文有 ≥3 个不同技术词也保留。"""
    body = "熟悉 Python、Kubernetes、MySQL，有分布式系统经验"
    assert is_it_domain("研发岗", body) is True


# ---------------- PII 打码 ----------------
def test_mask_pii_replaces_unicode_line_separators():
    """U+2028/U+2029 会让 splitlines() 把一条 jsonl 记录劈成两半，落盘前必须换掉。"""
    out = mask_pii(f"上一行{LS}下一行{PS}末行")
    assert LS not in out and PS not in out
    assert out == "上一行\n下一行\n末行"


def test_mask_pii_still_masks_contacts():
    out = mask_pii("电话13812345678 邮箱 abc.def@example.com 微信:zhangsan123")
    assert "13812345678" not in out and "1**********" in out
    assert "abc.def@example.com" not in out and "***@***" in out
    assert "zhangsan123" not in out


def test_mask_pii_empty():
    assert mask_pii("") == ""


# ---------------- 全目录标题归簇 / 配额键

def test_infer_title_cluster_for_full_catalog_rows():
    """飞书全目录没有 query，必须从标题得到簇，不能把全公司都塞进 None 配额。"""
    assert infer_title_cluster("智能座舱软件工程师") == "车联网"
    assert infer_title_cluster("机器人运动控制算法工程师") == "机器人算法"
    assert infer_title_cluster("数字孪生平台开发工程师") == "数字孪生"


def test_infer_title_cluster_rejects_non_engineering_and_prefers_longest_keyword():
    assert infer_title_cluster("机器人销售专员") is None
    assert infer_title_cluster("普通产品经理") is None
    assert infer_title_cluster("大模型产品经理") == "AI产品"
    assert title_on_target("数字人产品经理", "数字人") is False
    assert title_on_target("数字人销售专员", "数字人") is False
    assert title_on_target("人工智能数字人训练师", "数字人") is True
    # 同时命中大模型算法与推理优化正则时，queries.json 的最长关键词应钉住更具体的簇。
    assert infer_title_cluster("大模型推理优化算法工程师") == "大模型推理优化"
