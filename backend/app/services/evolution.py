"""既有岗位能力动态更新与演化追踪。

赛题核心功能②：识别既有岗位能力要求变化，明确标注新增/删除/修改，附更新说明与数据源。
"""
from __future__ import annotations
from datetime import datetime
from sqlalchemy.orm import Session
from .. import models
from .hallucination import job_confidence

WEIGHT_DELTA = 0.2   # 权重变化阈值


def compute_changes(old_caps: list[dict], new_caps: list[dict],
                    window_skill_names: set[str] | None = None,
                    window_jd_count: int = 0) -> list[dict]:
    """对比新旧能力项，输出变更列表。

    old_caps/new_caps: [{name, importance, weight, level_required, confidence, source_count}]

    window_skill_names: 最新窗口里**真实出现过**的技能名集合（先验注入之前）。
        调用方会把旧能力作为 "history" 先验来源注入聚合，所以旧能力名几乎不可能
        从 new_caps 里消失——只看 new_caps 判淘汰会永远判不出来。给了这个集合就
        改用"最新 N 条 JD 里一次都没出现"这个直接判据，可解释也可举证。
    window_jd_count: 最新窗口的 JD 条数。样本太少时不下"淘汰"结论（缺证据不等于反证）。
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

    # 删除（能力在最新窗口中确实消失）
    MIN_JDS_FOR_DELETE = 20          # 样本不足时不下淘汰结论：缺证据不等于反证。
    # 20 是实测定的：门槛设 10 时，只有 16 条 JD 的运维岗把 Nginx 判成了"淘汰"——
    # 窗口太薄，一个常用技能碰巧没被提到就会造出假阳性，而这种错误一眼就能被看穿。
    new_all = {c["name"]: c for c in new_caps}
    can_judge_absence = window_skill_names is not None and window_jd_count >= MIN_JDS_FOR_DELETE
    for name, c in old_map.items():
        absent = (name not in window_skill_names) if window_skill_names is not None \
            else (name not in new_all)
        # 只对**必备能力**的消失下"淘汰"结论：加分项本就零散、进出频繁，
        # 且不同语料的加分项用词差异极大，把它们记成淘汰会淹没真正的信号
        # （实测一次能刷出 160+ 条加分项"淘汰"，其中大量只是语料词汇差异）。
        was_required = c.get("importance") == "required"
        if absent and was_required and (can_judge_absence or window_skill_names is None):
            note = (f"在最新 {window_jd_count} 条 JD 中未再出现" if window_skill_names is not None
                    else "基于最新JD窗口未出现")
            changes.append({
                "change_type": "delete", "skill_name": name, "importance": c.get("importance"),
                "old_value": {"importance": c.get("importance"), "weight": c.get("weight")},
                "new_value": None,
                "reason": f"{note}，判定为需求消退/淘汰能力项",
                "confidence": 0.6,
                "data_source": {"note": note, "window_jd_count": window_jd_count},
            })
        elif not absent and name in new_all \
                and new_all[name].get("status", "active") != "active" \
                and name not in new_map:
            # 仍出现在最新 JD 里，但交叉验证支持减弱被降为候选。
            # 这类变更既不是新增也不是淘汰，若不在这里记账就会三个分支都进不去、被静默丢弃。
            n = new_all[name]
            changes.append({
                "change_type": "modify", "skill_name": name, "importance": c.get("importance"),
                "old_value": {"importance": c.get("importance"),
                              "confidence": c.get("confidence")},
                "new_value": {"importance": c.get("importance"),
                              "confidence": n.get("confidence"), "status": "candidate"},
                "reason": (f"最新窗口内仅 {n.get('source_count', 0)} 个来源提及"
                           f"（置信度 {n.get('confidence', 0):.2f}），未通过交叉验证阈值，"
                           f"降级为候选能力项（保留观察，未淘汰）"),
                "confidence": n.get("confidence", 0.0),
                "data_source": {"source_count": n.get("source_count", 0),
                                "support_ratio": n.get("support_ratio", 0),
                                "note": "支持减弱，降级为候选"},
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
    # 技能名一次批量取（历史实现逐条 Skill.get，是 N+1）
    name_of = dict(db.query(models.Skill.id, models.Skill.name).filter(
        models.Skill.id.in_({js.skill_id for js in existing})).all()) if existing else {}
    existing_map = {}
    for js in existing:
        nm = name_of.get(js.skill_id)
        if nm is not None:
            existing_map[nm] = js
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
