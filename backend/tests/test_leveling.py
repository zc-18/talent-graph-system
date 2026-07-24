"""岗位分级（初/中/高级）纯逻辑测试：分桶规则 + 晋升语义改写。"""
from app.services import leveling
from app.services.evolution import compute_changes


class FakeRow:
    def __init__(self, level, text="有效JD文本" * 5):
        self.inferred_level = level
        self.raw_text = text


# ---------- 分桶规则 ----------

def test_bucket_rule_requires_min_jds_and_two_buckets():
    # 只有一档达标 → 拒绝
    rows = [FakeRow("junior")] * 5 + [FakeRow("senior")] * 2
    assert leveling.bucket_rows(rows) == {}

    # 两档各≥3 → 通过
    rows = [FakeRow("junior")] * 3 + [FakeRow("senior")] * 4
    b = leveling.bucket_rows(rows)
    assert set(b) == {"junior", "senior"}
    assert len(b["junior"]) == 3 and len(b["senior"]) == 4


def test_bucket_rule_ignores_invalid_rows():
    rows = ([FakeRow("junior")] * 3 + [FakeRow("middle")] * 3 +
            [FakeRow(None)] * 5 + [FakeRow("junior", text="  ")] * 5 +
            [FakeRow("expert")] * 5)
    b = leveling.bucket_rows(rows)
    assert set(b) == {"junior", "middle"}
    assert len(b["junior"]) == 3  # 空文本行不计入


def test_bucket_rule_all_empty():
    assert leveling.bucket_rows([]) == {}
    assert leveling.bucket_rows([FakeRow("middle")] * 10) == {}  # 单档不成立


# ---------- 晋升语义改写 ----------

def _cap(name, importance="required", weight=0.5, level_required="familiar",
         confidence=0.8, source_count=5):
    return {"name": name, "importance": importance, "weight": weight,
            "level_required": level_required, "confidence": confidence,
            "source_count": source_count}


def test_level_diff_add_and_delete_reasons():
    old = [_cap("Python"), _cap("SQL")]
    new = [_cap("Python"), _cap("系统架构设计")]
    changes = compute_changes(old, new)
    leveling.rewrite_promotion_reasons(changes, "高级")
    by = {(c["change_type"], c["skill_name"]): c for c in changes}
    assert by[("add", "系统架构设计")]["reason"] == "晋升到高级需新增掌握 系统架构设计"
    assert by[("delete", "SQL")]["reason"] == "SQL 在高级JD中不再单列，视为默认前提"


def test_level_diff_weight_up_reason():
    old = [_cap("Kubernetes", weight=0.3)]
    new = [_cap("Kubernetes", weight=0.8)]
    changes = compute_changes(old, new)
    leveling.rewrite_promotion_reasons(changes, "高级")
    assert len(changes) == 1
    assert changes[0]["reason"] == "晋升要求强化 Kubernetes（权重 30%→80%）"


def test_level_diff_weight_down_reason():
    old = [_cap("HTML", weight=0.9)]
    new = [_cap("HTML", weight=0.4)]
    changes = compute_changes(old, new)
    leveling.rewrite_promotion_reasons(changes, "中级")
    assert "权重下降" in changes[0]["reason"]


def test_level_diff_importance_change_reason():
    old = [_cap("性能调优", importance="bonus")]
    new = [_cap("性能调优", importance="required")]
    changes = compute_changes(old, new)
    leveling.rewrite_promotion_reasons(changes, "高级")
    assert changes[0]["change_type"] == "modify"
    assert "升为必备项" in changes[0]["reason"]


def test_level_required_up_reason():
    changes = [{"change_type": "modify", "skill_name": "Java",
                "old_value": {"level_required": "familiar"},
                "new_value": {"level_required": "expert"}, "reason": ""}]
    leveling.rewrite_promotion_reasons(changes, "高级")
    assert changes[0]["reason"] == "掌握深度要求提升（familiar→expert）"


def test_small_weight_change_no_modify():
    # 权重变化 < WEIGHT_DELTA(0.2) 不产生 modify
    old = [_cap("Git", weight=0.5)]
    new = [_cap("Git", weight=0.6)]
    assert compute_changes(old, new) == []


def test_level_labels():
    assert leveling.LEVEL_LABELS == {"junior": "初级", "middle": "中级", "senior": "高级"}
