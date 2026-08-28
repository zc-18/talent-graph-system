"""既有岗位能力动态更新与演化追踪。

赛题核心功能②：识别既有岗位能力要求变化，明确标注新增/删除/修改，附更新说明与数据源。
"""
from __future__ import annotations
from datetime import datetime
from sqlalchemy.orm import Session
from .. import models
from .confidence_batch import REAL_EXTERNAL_TYPES
from .hallucination import MIN_SOURCES_REQUIRED, job_confidence
from .taxonomy import normalize_skill

WEIGHT_DELTA = 0.2   # 权重变化阈值

# 落库准入闸门：与 hallucination 聚合同一个阈值常量，不另立第二套口径。
MIN_EMPLOYERS_FOR_ACTIVE = MIN_SOURCES_REQUIRED


def admission_status(cap: dict) -> str:
    """判定一条能力项**能否以 active 落库**——交叉验证闸门，不是调用方说了算。

    防的是 2026-08 复盘查到的这类事故：`apply_evolution` 的新增分支历史上把
    `status="active"` 硬编码进 `models.JobSkill(...)`，更新分支同样无条件写
    `js.status = "active"`。于是调用方只要在 cap 里声称 `status='active'`，
    哪怕 `source_count=0`、`factors={}`、`evidence` 一条都没有，也会凭空落出一行
    绕过「≥2 独立雇主交叉验证」的 active 能力——而这道闸门正是本作品的核心主张。

    生产库 `talent_graph_v3` 里因此留下 **345 行幽灵能力**：source_count=0、
    factors 全零、confidence=0、evidence 表里一条关联记录都没有，集中在 15 个
    version>1 的岗位上，其中 93.7% 能被 `capability_change` 的 add 记录逐条对上。
    后果不止是难看：`job.confidence` 是能力项置信度的加权均值，被这些 0 分行拖死
    （后端开发工程师 28 个 active 里 27 个是幽灵 → 岗位置信度 0.0334）。

    判据（与 `hallucination.aggregate_capabilities` 完全一致，不放宽）：
      · 独立雇主数 ≥ MIN_EMPLOYERS_FOR_ACTIVE → active；
      · 否则只有拿得出 web/权威外部佐证（web_verified / factors.external / 证据
        里带非 JD 的外部来源）才算过闸；
      · 都没有 → candidate（保留观察、保留证据链，**不是**淘汰、**不**删行）。

    向后兼容：正常调用方（`data/run_evolution_batch.py` 与治理发布路径）传进来的
    是聚合结果或经审核的快照，本来就带着 ≥2 的 source_count 与完整 factors/evidence，
    判定结果仍是 active，行为不变。
    """
    if int(cap.get("source_count") or cap.get("employer_count") or 0) >= MIN_EMPLOYERS_FOR_ACTIVE:
        return "active"
    if cap.get("web_verified"):
        return "active"
    if float((cap.get("factors") or {}).get("external") or 0) >= 1.0:
        return "active"
    for ev in cap.get("evidence") or []:
        if str(ev.get("source_type") or "").casefold() in REAL_EXTERNAL_TYPES:
            return "active"
    return "candidate"


def _gated(caps: list[dict], admission: dict[str, str]) -> list[dict]:
    """把闸门判定回填进能力集副本（不改调用方的 dict），供岗位级口径复用。"""
    return [c if c.get("status") != "active"
            else {**c, "status": admission.get(c["name"], "candidate")}
            for c in caps]


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
        elif can_judge_absence and not absent and name in new_all \
                and new_all[name].get("status", "active") != "active" \
                and name not in new_map:
            # 仍出现在最新 JD 里，但交叉验证支持减弱被降为候选。
            # 这类变更既不是新增也不是淘汰，若不在这里记账就会三个分支都进不去、被静默丢弃。
            #
            # `can_judge_absence` 这道闸和 delete 分支共用，理由也一样：交互式演化只贴
            # 1-3 条 JD 时，调用方会把**每一条**旧能力当作 history 先验注入聚合，它们
            # 只拿得到 1 个来源、必然过不了「≥2 来源」闸门 → 必然落进这个分支。结果是
            # 一次点击就给整个岗位刷出几十条「支持减弱」，而窗口薄本来就说明不了任何事。
            # 实测线上误点两次，Java 被刷出 40 条这样的记录（v3/v4 各 20 条），演化记录
            # 总数 621→661、演化页首屏全是「确认能力项→候选能力项」——而 apply_evolution
            # 并不消费这类变更，库里 20 行原封未动。日志说降级、库里没降级，方向与 2026-07
            # 那次事故相反，但同样是「日志与事实背离」。
            # 窗口不足以判「消失」，就同样不足以判「支持减弱」——缺证据不等于反证。
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
                    changes: list[dict], *, commit: bool = True) -> dict:
    """应用演化：更新 JobSkill、写入变更记录、版本号 +1。

    **落库状态由交叉验证闸门决定，不由调用方声称的 status 决定**（见
    `admission_status` 的完整事故说明）：声称 active 但拿不出 ≥2 独立雇主、
    也拿不出外部权威佐证的能力项一律落 candidate，绝不写成 active。
    这是 2026-08 复盘 345 行幽灵能力后加的闸门。
    """
    from .graph_service import upsert_skill, write_evidence
    new_version = (job.version or 1) + 1

    active_new = [c for c in new_caps if c.get("status") == "active"]
    # 准入判定先算好：写行、算岗位置信度、算 evidence_count 三处必须用同一份判定，
    # 否则又是一次「库里一个样、卡片上另一个样」。
    admission = {c["name"]: admission_status(c) for c in active_new}

    existing = db.query(models.JobSkill).filter(models.JobSkill.job_id == job.id).all()
    # 技能名一次批量取（历史实现逐条 Skill.get，是 N+1）
    name_of = dict(db.query(models.Skill.id, models.Skill.name).filter(
        models.Skill.id.in_({js.skill_id for js in existing})).all()) if existing else {}
    existing_map = {}
    for js in existing:
        nm = name_of.get(js.skill_id)
        if nm is not None:
            existing_map[nm] = js

    # 淘汰项：**只降级 compute_changes 判定为 delete 的能力项**。
    #
    # 这里历史上是 `if name not in new_names`（new_names = 本轮聚合出的 active 集合），
    # 与 compute_changes 的淘汰判据完全脱钩，后果是变更日志与库表各说各话：
    # 交互式演化只贴 1-3 条 JD，旧能力仅靠 history 先验注入拿到 1 个来源，过不了
    # 「≥2 来源」闸门 → 不在 new_names 里 → 被静默降级；而 compute_changes 因为
    # 先验注入使旧能力仍存在于 new_caps，一条 delete 记录都不会写。实测线上误点
    # 三次，AI产品经理 102 行被降级、delete 记录 0 条，Java 211 行被降级、v3-v5
    # delete 记录 0 条。对一个以「可溯源、反幻觉」为卖点的系统，日志与事实背离
    # 比丢数据更致命。
    #
    # 改为以变更日志为唯一真相源：日志说淘汰什么，库里就淘汰什么，两者按构造一致。
    # 少量 JD 的交互式演化因此只会新增/修改，不会淘汰——这正是 compute_changes
    # 里 MIN_JDS_FOR_DELETE 想表达的「缺证据不等于反证」，只是当初没落到写库这一侧。
    delete_names = {ch["skill_name"] for ch in changes
                    if ch.get("change_type") == "delete"}
    # 「支持减弱→候选」同样以日志为准落库。此前 compute_changes 会记这类变更，而这里
    # 只消费 delete，于是日志说降级、库里纹丝不动——和上面那段描述的事故同一个病
    # （日志与事实背离），只是方向相反。两边都按日志走，才谈得上"可溯源"。
    demote = {ch["skill_name"]: (ch.get("new_value") or {})
              for ch in changes
              if ch.get("change_type") == "modify"
              and (ch.get("new_value") or {}).get("status") == "candidate"}
    for name, js in existing_map.items():
        if name in delete_names and js.status == "active":
            js.status = "deprecated"
            js.last_seen = datetime.utcnow()
        elif name in demote and js.status == "active":
            js.status = "candidate"
            nv = demote[name]
            if nv.get("confidence") is not None:
                js.confidence = nv["confidence"]
            js.last_seen = datetime.utcnow()

    # 新增/更新项
    for c in active_new:
        # parent_name 是两级技能体系的唯一驱动键（graph_service.upsert_skill 靠它设
        # Skill.parent_id，granularity 再由 parent_id 派生）。此处历史上没传，
        # 于是演化新增的细粒度技能点全部落成粗粒度，和新岗位发现路径同一个坑。
        sk = upsert_skill(db, c["name"], c.get("category"), c.get("skill_type"),
                          parent_name=c.get("parent"))
        js = existing_map.get(c["name"])
        if js:
            js.importance = c["importance"]
            js.weight = c.get("weight", js.weight)
            js.confidence = c.get("confidence", js.confidence)
            js.source_count = c.get("source_count", js.source_count)
            js.level_required = c.get("level_required", js.level_required)
            js.factors = c.get("factors", js.factors) or {}
            js.status = admission[c["name"]]
            js.last_seen = datetime.utcnow()
        else:
            js = models.JobSkill(
                job_id=job.id, skill_id=sk.id, importance=c["importance"],
                weight=c.get("weight", 0.5), level_required=c.get("level_required", "familiar"),
                confidence=c.get("confidence", 0.0), source_count=c.get("source_count", 0),
                factors=c.get("factors") or {},
                status=admission[c["name"]],
                first_seen=datetime.utcnow(), last_seen=datetime.utcnow())
            db.add(js)
            db.flush()
        # 证据落库：聚合结果里本来就带着 raw_jd_id，此前被整个忽略，于是每一条经
        # 演化新增/刷新的能力在「溯源证据」页都落到「暂无独立JD证据」分支——
        # 一个卖点是可溯源的系统，演化出来的能力反而无据可查。按 raw_jd_id 去重，
        # 演化跑多次不会堆叠。
        write_evidence(db, js.id, c.get("evidence", []))

    # 写变更记录
    for ch in changes:
        db.add(models.CapabilityChange(
            job_id=job.id, version=new_version, change_type=ch["change_type"],
            skill_name=ch["skill_name"], importance=ch.get("importance"),
            old_value=ch.get("old_value"), new_value=ch.get("new_value"),
            reason=ch.get("reason", ""), data_source=ch.get("data_source", {}),
            confidence=ch.get("confidence", 0.0)))

    job.version = new_version
    # 岗位级两个数都按**闸门判定后的实际落库状态**算：没过闸的落成 candidate，就不能
    # 再算进岗位置信度和 JD 支撑数，否则卡片上的数字与库里的行又对不上。
    job.confidence = job_confidence(_gated(new_caps, admission))
    # 卡片上的「JD 支撑」口径与 upsert_job 保持一致（active 项的 source_count 之和）。
    # 此前只有建图时算一次，演化改了能力集却不重算，岗位列表的数字越跑越旧。
    job.evidence_count = sum(c.get("source_count", 0) for c in active_new
                             if admission[c["name"]] == "active")
    job.updated_at = datetime.utcnow()
    if commit:
        db.commit()
    return {"version": new_version, "changes_applied": len(changes),
            "added": sum(1 for c in changes if c["change_type"] == "add"),
            "deleted": sum(1 for c in changes if c["change_type"] == "delete"),
            "modified": sum(1 for c in changes if c["change_type"] == "modify")}


def snapshot_job_version(db: Session, job: models.Job, *, created_by: int | None = None
                         ) -> models.JobVersion:
    """Persist a complete, immutable capability snapshot for the current Job.version."""
    existing = db.query(models.JobVersion).filter(
        models.JobVersion.job_id == job.id,
        models.JobVersion.version == (job.version or 1)).first()
    if existing:
        return existing
    from . import role_contract
    version = models.JobVersion(
        job_id=job.id, version=job.version or 1, status="published",
        effective_at=job.updated_at or datetime.utcnow(),
        evidence_window={"dimensions": {
            "job_name": job.name,
            "seniority": job.level or "unspecified",
            "recruitment_type": job.recruitment_type or "mixed",
            "track": job.track or "software",
            "industry": job.industry or "general",
        }},
        summary=job.summary, responsibilities=job.core_responsibilities or [],
        typical_scenarios=job.typical_scenarios or [], contract_snapshot=None,
        created_by=created_by)
    db.add(version)
    db.flush()
    rows = db.query(models.JobSkill).filter(models.JobSkill.job_id == job.id).all()
    for row in rows:
        evidence_refs = [{"evidence_id": ev.id, "raw_jd_id": ev.raw_jd_id,
                          "url": ev.source_url} for ev in db.query(models.Evidence).filter(
                              models.Evidence.job_skill_id == row.id).limit(12).all()]
        skill = db.query(models.Skill).get(row.skill_id)
        parent = db.query(models.Skill).get(skill.parent_id) if skill and skill.parent_id else None
        db.add(models.JobVersionSkill(
            job_version_id=version.id, skill_id=row.skill_id,
            capability_cluster=(parent.name if parent else (skill.name if skill else None)),
            importance=row.importance, status=row.status, weight=row.weight,
            confidence=row.confidence, level_required=row.level_required,
            factors=row.factors or {}, evidence_refs=evidence_refs))
    db.flush()
    version.contract_snapshot = role_contract.build_contract_from_version(db, job, version)
    return version


def current_capabilities(db: Session, job: models.Job) -> list[dict]:
    """Return the complete current active capability set in proposal input shape."""
    rows = (db.query(models.JobSkill, models.Skill)
            .join(models.Skill, models.Skill.id == models.JobSkill.skill_id)
            .filter(models.JobSkill.job_id == job.id,
                    models.JobSkill.status == "active").all())
    parent_ids = {skill.parent_id for _, skill in rows if skill.parent_id}
    parents = dict(db.query(models.Skill.id, models.Skill.name).filter(
        models.Skill.id.in_(parent_ids)).all()) if parent_ids else {}
    return [{
        "name": skill.name,
        "category": skill.category,
        "skill_type": skill.skill_type,
        "parent": parents.get(skill.parent_id),
        "capability_cluster": parents.get(skill.parent_id) or skill.name,
        "importance": row.importance,
        "status": "active",
        "weight": float(row.weight or 0),
        "confidence": float(row.confidence or 0),
        "level_required": row.level_required or "familiar",
        "factors": row.factors or {},
        "source_count": int(row.source_count or 0),
        "evidence": [{
            "raw_jd_id": ev.raw_jd_id,
            "source_type": ev.source_type,
            "source": ev.source_name,
            "source_url": ev.source_url,
            "snippet": ev.snippet,
            "weight": ev.weight,
        } for ev in db.query(models.Evidence).filter(
            models.Evidence.job_skill_id == row.id).order_by(
            models.Evidence.id).limit(12).all()],
    } for row, skill in rows]


def apply_review_level_overrides(current: list[dict], parsed_jds: list[dict]) \
        -> tuple[list[dict], list[dict]]:
    """Propose explicit level upgrades for existing, already-validated skills only.

    A manually supplied JD has no independent-employer identity, so it must never
    introduce a new active capability. It may, however, produce a review-only
    level upgrade for a capability whose historical evidence is already active.
    """
    rank = {"familiar": 0, "proficient": 1, "expert": 2}
    requested: dict[str, str] = {}
    for parsed in parsed_jds:
        for key in ("required_skills", "bonus_skills", "fine_skills"):
            for value in parsed.get(key, []):
                name = normalize_skill(str(value.get("name") or ""))
                level = value.get("level") or "familiar"
                if not name or level not in rank:
                    continue
                if rank[level] > rank.get(requested.get(name, "familiar"), 0):
                    requested[name] = level

    proposed, changes = [], []
    for value in current:
        capability = dict(value)
        name = normalize_skill(str(capability.get("name") or ""))
        old_level = capability.get("level_required") or "familiar"
        new_level = requested.get(name)
        if new_level and rank[new_level] > rank.get(old_level, 0):
            capability["level_required"] = new_level
            changes.append({
                "change_type": "modify", "skill_name": name,
                "importance": capability.get("importance"),
                "old_value": {"level_required": old_level},
                "new_value": {"level_required": new_level},
                "reason": ("手工提供的最新 JD 明确提高该既有能力的熟练度要求；"
                           "仅生成待人工审核提案，不计作独立雇主交叉验证"),
                "confidence": float(capability.get("confidence") or 0),
                "data_source": {
                    "source": "manual_jd_preview",
                    "jd_count": len(parsed_jds),
                    "employer_validated": False,
                    "manual_review_required": True,
                },
            })
        proposed.append(capability)
    return proposed, changes


def normalize_proposed_capabilities(snapshot: dict) -> list[dict]:
    """Normalize a full proposed snapshot; omitted current skills mean deletion."""
    raw = snapshot.get("capabilities") or []
    if not isinstance(raw, list) or not raw:
        raise ValueError("proposed_snapshot.capabilities 至少需要一项能力")
    normalized: list[dict] = []
    seen: set[str] = set()
    for value in raw:
        cap = {"name": value} if isinstance(value, str) else dict(value or {})
        name = normalize_skill(str(cap.get("name") or ""))
        if not name or name in seen or cap.get("status", "active") != "active":
            continue
        seen.add(name)
        importance = cap.get("importance", "required")
        if importance not in {"required", "bonus"}:
            raise ValueError(f"{name} 的 importance 必须是 required/bonus")
        parent = cap.get("parent") or cap.get("parent_name") or cap.get("capability_cluster")
        normalized.append({
            **cap,
            "name": name,
            "parent": parent if parent and parent != name else None,
            "capability_cluster": parent or name,
            "importance": importance,
            "status": "active",
            "weight": float(cap.get("weight", 0.5)),
            "confidence": float(cap.get("confidence", 0.0)),
            "level_required": cap.get("level_required") or "familiar",
            "factors": cap.get("factors") or {},
            "source_count": int(cap.get("source_count") or cap.get("employer_count") or 0),
            "evidence": cap.get("evidence") or [],
        })
    if not normalized:
        raise ValueError("proposed_snapshot 中没有有效 active 能力")
    return sorted(normalized, key=lambda item: item["name"])


def _snapshot_map(capabilities: list[dict]) -> dict[str, dict]:
    return {cap["name"]: {
        "importance": cap.get("importance") or "required",
        "status": "active",
        "weight": round(float(cap.get("weight") or 0), 6),
        "confidence": round(float(cap.get("confidence") or 0), 6),
        "level_required": cap.get("level_required") or "familiar",
        "factors": cap.get("factors") or {},
        "capability_cluster": cap.get("capability_cluster") or cap.get("parent") or cap["name"],
    } for cap in capabilities if cap.get("status", "active") == "active"}


def compute_snapshot_diff(before: list[dict], after: list[dict]) -> list[dict]:
    """Create a lossless change log for every governed JobVersionSkill field."""
    old_map, new_map = _snapshot_map(before), _snapshot_map(after)
    changes = []
    for name in sorted(set(old_map) | set(new_map)):
        old_value, new_value = old_map.get(name), new_map.get(name)
        if old_value == new_value:
            continue
        if old_value is None:
            change_type, reason = "add", "拟发布快照新增能力项"
        elif new_value is None:
            change_type, reason = "delete", "拟发布快照移除能力项"
        else:
            change_type, reason = "modify", "拟发布快照修改能力字段"
        current = new_value or old_value or {}
        changes.append({
            "change_type": change_type,
            "skill_name": name,
            "importance": current.get("importance"),
            "old_value": old_value,
            "new_value": new_value,
            "reason": reason,
            "confidence": float(current.get("confidence") or 0),
            "data_source": {"source": "evolution_run_snapshot"},
        })
    return changes


def version_capabilities(db: Session, version: models.JobVersion) -> list[dict]:
    """Rebuild the governed active snapshot from immutable JobVersionSkill rows."""
    rows = (db.query(models.JobVersionSkill, models.Skill)
            .join(models.Skill, models.Skill.id == models.JobVersionSkill.skill_id)
            .filter(models.JobVersionSkill.job_version_id == version.id,
                    models.JobVersionSkill.status == "active").all())
    return [{
        "name": skill.name,
        "importance": row.importance,
        "status": row.status,
        "weight": row.weight,
        "confidence": row.confidence,
        "level_required": row.level_required,
        "factors": row.factors or {},
        "capability_cluster": row.capability_cluster or skill.name,
    } for row, skill in rows]


def assert_snapshot_reconciled(*, before: list[dict], proposed: list[dict],
                               after: list[dict], changes: list[dict]) -> None:
    """Fail publication unless proposal, snapshots, and append-only log are identical."""
    if _snapshot_map(proposed) != _snapshot_map(after):
        raise RuntimeError("发布后 JobVersionSkill 快照与 proposal 不一致")
    expected = compute_snapshot_diff(before, after)
    signature = lambda rows: [{
        "change_type": row.get("change_type"),
        "skill_name": row.get("skill_name"),
        "old_value": row.get("old_value"),
        "new_value": row.get("new_value"),
    } for row in rows]
    if signature(expected) != signature(changes):
        raise RuntimeError("JobVersion 快照 diff 与 CapabilityChange 日志不一致")
