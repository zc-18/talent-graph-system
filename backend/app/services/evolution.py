"""既有岗位能力动态更新与演化追踪。

赛题核心功能②：识别既有岗位能力要求变化，明确标注新增/删除/修改，附更新说明与数据源。
"""
from __future__ import annotations
from datetime import datetime
from sqlalchemy.orm import Session
from .. import models
from .hallucination import job_confidence

WEIGHT_DELTA = 0.2   # 权重变化阈值


def compute_changes(old_caps: list[dict], new_caps: list[dict]) -> list[dict]:
    """对比新旧能力项，输出变更列表。

    old_caps/new_caps: [{name, importance, weight, level_required, confidence, source_count}]
    """
    old_map = {c["name"]: c for c in old_caps}
    new_map = {c["name"]: c for c in new_caps if c.get("status", "active") == "active"}
    changes = []

    # 新增
    for name, c in new_map.items():
        if name not in old_map:
            changes.append({
                "change_type": "add", "skill_name": name, "importance": c["importance"],
                "old_value": None,
                "new_value": {"importance": c["importance"], "weight": c.get("weight"),
                              "level_required": c.get("level_required"), "confidence": c.get("confidence")},
                "reason": f"在最新招聘数据中新出现，由{c.get('source_count',1)}个独立来源交叉验证，置信度{c.get('confidence',0):.2f}",
                "confidence": c.get("confidence", 0.0),
                "data_source": {"source_count": c.get("source_count", 0),
                                "support_ratio": c.get("support_ratio", 0),
                                "web_verified": c.get("web_verified", False)},
            })

    # 删除（旧有但新数据中消失且非候选）
    new_all_names = {c["name"] for c in new_caps}
    for name, c in old_map.items():
        if name not in new_all_names:
            changes.append({
                "change_type": "delete", "skill_name": name, "importance": c.get("importance"),
                "old_value": {"importance": c.get("importance"), "weight": c.get("weight")},
                "new_value": None,
                "reason": "近期招聘数据中已不再要求该能力，判定为过时/淘汰能力项",
                "confidence": 0.6,
                "data_source": {"note": "基于最新JD窗口未出现"},
            })

    # 修改（重要度或权重显著变化）
    for name in set(old_map) & set(new_map):
        o, n = old_map[name], new_map[name]
        if o.get("importance") != n.get("importance"):
            changes.append({
                "change_type": "modify", "skill_name": name, "importance": n["importance"],
                "old_value": {"importance": o.get("importance")},
                "new_value": {"importance": n.get("importance")},
                "reason": f"重要度由「{_imp(o.get('importance'))}」变为「{_imp(n.get('importance'))}」，反映市场需求变化",
                "confidence": n.get("confidence", 0.0),
                "data_source": {"source_count": n.get("source_count", 0)},
            })
        elif abs((o.get("weight") or 0) - (n.get("weight") or 0)) >= WEIGHT_DELTA:
            direction = "上升" if (n.get("weight") or 0) > (o.get("weight") or 0) else "下降"
            changes.append({
                "change_type": "modify", "skill_name": name, "importance": n["importance"],
                "old_value": {"weight": round(o.get("weight") or 0, 3)},
                "new_value": {"weight": round(n.get("weight") or 0, 3)},
                "reason": f"需求热度{direction}（权重 {o.get('weight'):.2f}→{n.get('weight'):.2f}）",
                "confidence": n.get("confidence", 0.0),
                "data_source": {"source_count": n.get("source_count", 0)},
            })
    return changes


def _imp(x):
    return {"required": "必备技能", "bonus": "加分技能"}.get(x, x)


def apply_evolution(db: Session, job: models.Job, new_caps: list[dict],
                    changes: list[dict]) -> dict:
    """应用演化：更新 JobSkill、写入变更记录、版本号 +1。"""
    from .graph_service import upsert_skill
    new_version = (job.version or 1) + 1

    active_new = [c for c in new_caps if c.get("status") == "active"]
    new_names = {c["name"] for c in active_new}

    # 删除项：标记 deprecated（保留历史）
    existing = db.query(models.JobSkill).filter(models.JobSkill.job_id == job.id).all()
    existing_map = {}
    for js in existing:
        sk = db.query(models.Skill).get(js.skill_id)
        if sk:
            existing_map[sk.name] = js
    for name, js in existing_map.items():
        if name not in new_names:
            js.status = "deprecated"
            js.last_seen = datetime.utcnow()

    # 新增/更新项
    for c in active_new:
        sk = upsert_skill(db, c["name"], c.get("category"), c.get("skill_type"))
        js = existing_map.get(c["name"])
        if js:
            js.importance = c["importance"]
            js.weight = c.get("weight", js.weight)
            js.confidence = c.get("confidence", js.confidence)
            js.source_count = c.get("source_count", js.source_count)
            js.level_required = c.get("level_required", js.level_required)
            js.status = "active"
            js.last_seen = datetime.utcnow()
        else:
            db.add(models.JobSkill(
                job_id=job.id, skill_id=sk.id, importance=c["importance"],
                weight=c.get("weight", 0.5), level_required=c.get("level_required", "familiar"),
                confidence=c.get("confidence", 0.0), source_count=c.get("source_count", 0),
                status="active", first_seen=datetime.utcnow(), last_seen=datetime.utcnow()))

    # 写变更记录
    for ch in changes:
        db.add(models.CapabilityChange(
            job_id=job.id, version=new_version, change_type=ch["change_type"],
            skill_name=ch["skill_name"], importance=ch.get("importance"),
            old_value=ch.get("old_value"), new_value=ch.get("new_value"),
            reason=ch.get("reason", ""), data_source=ch.get("data_source", {}),
            confidence=ch.get("confidence", 0.0)))

    job.version = new_version
    job.confidence = job_confidence(new_caps)
    job.updated_at = datetime.utcnow()
    db.commit()
    return {"version": new_version, "changes_applied": len(changes),
            "added": sum(1 for c in changes if c["change_type"] == "add"),
            "deleted": sum(1 for c in changes if c["change_type"] == "delete"),
            "modified": sum(1 for c in changes if c["change_type"] == "modify")}
