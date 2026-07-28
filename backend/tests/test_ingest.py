"""岗位标题归一化 / 领域过滤 / PII 打码的行为测试（纯内存，不连数据库）。"""
from app.services import ingest
from data.import_raw import is_it_domain
from data.collect.base import mask_pii

LS, PS = " ", " "   # Unicode 行/段分隔符（源码里用转义写，避免被编辑器吃掉）


# ---------------- title_key ----------------
def test_cluster_hint_wins_over_title():
    """采集时的簇提示优先级最高，标题里写什么都不影响。"""
    assert ingest.title_key("Java开发工程师", "大模型算法") == "大模型算法工程师"


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


def test_canonical_job_names_covers_both_sources():
    canon = ingest.canonical_job_names()
    assert "大模型算法工程师" in canon      # title_map.json
    assert "Java开发工程师" in canon        # 内置标题映射
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
