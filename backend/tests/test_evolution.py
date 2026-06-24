"""岗位演化变更计算测试。"""
from app.services import evolution


def _cap(name, imp="required", weight=0.6, conf=0.9, src=3):
    return {"name": name, "importance": imp, "weight": weight, "level_required": "familiar",
            "confidence": conf, "source_count": src, "status": "active"}


def test_compute_add():
    old = [_cap("Java")]
    new = [_cap("Java"), _cap("大语言模型")]
    changes = evolution.compute_changes(old, new)
    adds = [c for c in changes if c["change_type"] == "add"]
    assert len(adds) == 1 and adds[0]["skill_name"] == "大语言模型"


def test_compute_delete():
    old = [_cap("Java"), _cap("Struts2")]
    new = [_cap("Java")]
    changes = evolution.compute_changes(old, new)
    dels = [c for c in changes if c["change_type"] == "delete"]
    assert len(dels) == 1 and dels[0]["skill_name"] == "Struts2"


def test_compute_modify_importance():
    old = [_cap("Docker", imp="bonus")]
    new = [_cap("Docker", imp="required")]
    changes = evolution.compute_changes(old, new)
    mods = [c for c in changes if c["change_type"] == "modify"]
    assert len(mods) == 1


def test_compute_modify_weight():
    old = [_cap("Kafka", weight=0.3)]
    new = [_cap("Kafka", weight=0.7)]
    changes = evolution.compute_changes(old, new)
    mods = [c for c in changes if c["change_type"] == "modify"]
    assert len(mods) == 1
    assert "上升" in mods[0]["reason"]


def test_no_change():
    old = [_cap("Java")]
    new = [_cap("Java")]
    assert evolution.compute_changes(old, new) == []
