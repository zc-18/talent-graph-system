"""人才侧图层：语料台账、供需缺口、团队能力盘点（老师意见⑧）。

与岗位侧（需求侧）的关系：**只读不写**。这里所有计算都基于已有的
job_skill / skill 与新增的 talent_profile，不回写任何岗位能力项、
不参与置信度计算 —— 置信度衡量的是"岗位是不是真的要求这项能力"，
简历能证明的是"有人会"，两回事，混在一起会把需求侧的结论污染掉。
"""
from __future__ import annotations
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import models
from .taxonomy import resolve_skill


def _active_caps(db: Session, job_id: int) -> list[dict]:
    """岗位的在用能力项（含技能名/权重/重要度）。"""
    js = db.query(models.JobSkill).filter(
        models.JobSkill.job_id == job_id,
        models.JobSkill.status == "active").all()
    if not js:
        return []
    sk = {r.id: r for r in db.query(models.Skill.id, models.Skill.name,
                                    models.Skill.normalized_name, models.Skill.category)
          .filter(models.Skill.id.in_({j.skill_id for j in js})).all()}
    caps = []
    for j in js:
        s = sk.get(j.skill_id)
        if not s:
            continue
        caps.append({"skill_id": j.skill_id,
                     "name": s.normalized_name or s.name,
                     "category": s.category,
                     "importance": j.importance, "weight": j.weight or 0.0,
                     "level_required": j.level_required, "confidence": j.confidence or 0.0})
    return caps


def talent_skill_index(db: Session, profiles: list[models.TalentProfile] | None = None
                       ) -> dict[str, set[int]]:
    """技能名 → 具备该技能的人才 id 集合。技能名经 resolve_skill 对齐到图谱写法。"""
    profiles = profiles if profiles is not None else db.query(models.TalentProfile).all()
    idx: dict[str, set[int]] = {}
    for p in profiles:
        for s in (p.skills or []):
            idx.setdefault(resolve_skill(s), set()).add(p.id)
    return idx


def supply_demand(db: Session, job_id: int) -> dict:
    """岗位需求 vs 人才供给：逐能力项算供给覆盖。

    供给率 = 具备该能力的人才数 / 语料总人数。**n 很小（30 份）**，
    所以这里只做定性对照，不做统计推断，前端与文档都会标注样本量。
    """
    job = db.query(models.Job).get(job_id)
    if not job:
        return {}
    caps = _active_caps(db, job_id)
    profiles = db.query(models.TalentProfile).all()
    total = len(profiles)
    idx = talent_skill_index(db, profiles)
    # 该岗位的对口人才（目标岗位就是它的）单独统计一档
    aligned = [p for p in profiles if p.matched_job_id == job_id]
    aligned_idx = talent_skill_index(db, aligned)

    items = []
    for c in caps:
        holders = idx.get(c["name"], set())
        a_holders = aligned_idx.get(c["name"], set())
        items.append({
            "skill": c["name"], "category": c["category"],
            "importance": c["importance"], "weight": round(c["weight"], 4),
            "confidence": round(c["confidence"], 4),
            "supply_count": len(holders),
            "supply_rate": round(len(holders) / total, 4) if total else 0.0,
            "aligned_supply_count": len(a_holders),
            "aligned_supply_rate": round(len(a_holders) / len(aligned), 4) if aligned else 0.0,
            "gap": round(c["weight"] * (1 - (len(holders) / total if total else 0)), 4),
        })
    items.sort(key=lambda x: (-x["gap"], -x["weight"]))
    required = [i for i in items if i["importance"] == "required"]
    covered = [i for i in required if i["supply_count"] > 0]
    return {
        "job": {"id": job.id, "name": job.name, "category": job.category},
        "corpus_size": total, "aligned_talents": len(aligned),
        "required_total": len(required), "required_covered": len(covered),
        "coverage_rate": round(len(covered) / len(required), 4) if required else 0.0,
        "items": items,
        "note": f"供给率分母为语料总人数 {total}，样本量小，仅作定性对照",
    }


def team_gap(db: Session, team_id: int, job_id: int) -> dict:
    """团队对目标岗位的能力盘点：已覆盖 / 谁能补 / 还缺谁。

    这正是《改进说明_第二版》里承诺过、当时没做的那个计算。
    """
    team = db.query(models.Team).get(team_id)
    job = db.query(models.Job).get(job_id)
    if not team or not job:
        return {}
    members = db.query(models.TeamMember).filter(
        models.TeamMember.team_id == team_id).all()
    tp_by_id = {}
    if members:
        for p in db.query(models.TalentProfile).filter(
                models.TalentProfile.id.in_([m.talent_id for m in members])).all():
            tp_by_id[p.id] = p

    member_skills: dict[int, set[str]] = {}
    for m in members:
        p = tp_by_id.get(m.talent_id)
        member_skills[m.id] = {resolve_skill(s) for s in (p.skills or [])} if p else set()

    caps = _active_caps(db, job_id)
    required = [c for c in caps if c["importance"] == "required"]
    bonus = [c for c in caps if c["importance"] == "bonus"]

    covered, missing = [], []
    for c in required:
        who = [m for m in members if c["name"] in member_skills.get(m.id, set())]
        rec = {"skill": c["name"], "category": c["category"],
               "weight": round(c["weight"], 4), "confidence": round(c["confidence"], 4),
               "holders": [{"member_id": m.id, "display_name": m.display_name,
                            "talent_code": tp_by_id.get(m.talent_id).code
                            if tp_by_id.get(m.talent_id) else None} for m in who]}
        (covered if who else missing).append(rec)

    bonus_covered = sum(1 for c in bonus
                        if any(c["name"] in member_skills.get(m.id, set()) for m in members))

    # 每个成员的贡献：他覆盖了多少必备能力、其中多少是只有他会的（不可替代性）
    contributions = []
    for m in members:
        mine = {c["skill"] for c in covered
                if any(h["member_id"] == m.id for h in c["holders"])}
        unique = {c["skill"] for c in covered
                  if len(c["holders"]) == 1 and c["holders"][0]["member_id"] == m.id}
        p = tp_by_id.get(m.talent_id)
        contributions.append({
            "member_id": m.id, "display_name": m.display_name,
            "role_label": m.role_label,
            "talent_code": p.code if p else None,
            "talent_id": m.talent_id,
            "skill_count": len(member_skills.get(m.id, set())),
            "covers_required": len(mine),
            "uniquely_covers": len(unique),
            "unique_skills": sorted(unique)[:12],
        })
    contributions.sort(key=lambda x: (-x["uniquely_covers"], -x["covers_required"]))

    req_w = sum(c["weight"] for c in required) or 1.0
    covered_w = sum(c["weight"] for c in covered)
    return {
        "team": {"id": team.id, "name": team.name, "size": len(members)},
        "job": {"id": job.id, "name": job.name, "category": job.category},
        "required_total": len(required), "required_covered": len(covered),
        "coverage_rate": round(len(covered) / len(required), 4) if required else 0.0,
        "weighted_coverage": round(covered_w / req_w, 4),
        "bonus_total": len(bonus), "bonus_covered": bonus_covered,
        "covered": sorted(covered, key=lambda x: -x["weight"]),
        "missing": sorted(missing, key=lambda x: -x["weight"]),
        "contributions": contributions,
    }


def corpus_overview(db: Session) -> dict:
    """语料台账 + 人才画像总览（前端"语料从哪来"那一栏直读）。"""
    batches = db.query(models.ResumeBatch).order_by(models.ResumeBatch.id).all()
    profiles = db.query(models.TalentProfile).all()
    by_source: dict[str, int] = {}
    by_lang: dict[str, int] = {}
    by_cluster: dict[str, int] = {}
    for p in profiles:
        by_source[p.source_type or "-"] = by_source.get(p.source_type or "-", 0) + 1
        by_lang[p.language or "-"] = by_lang.get(p.language or "-", 0) + 1
        if p.target_cluster:
            by_cluster[p.target_cluster] = by_cluster.get(p.target_cluster, 0) + 1
    aliases = db.query(models.SkillAlias).all()
    # 入库数按实际画像行数现算，不用 ResumeBatch.kept：kept 是采集/清洗环节写下的
    # 数字，导入时的跨批次近重复剔除发生在它之后（res-c-sample_ruiwen 就少了 1 份），
    # 直接展示 kept 会让台账各行加起来对不上总数 30。
    profiled = dict(db.query(models.TalentProfile.batch_id,
                             func.count(models.TalentProfile.id))
                    .group_by(models.TalentProfile.batch_id).all())
    return {
        "total_profiles": len(profiles),
        "total_skills_extracted": sum(p.skill_count or 0 for p in profiles),
        "by_source": by_source, "by_language": by_lang,
        "by_cluster": dict(sorted(by_cluster.items(), key=lambda kv: -kv[1])),
        "holdout": sum(1 for p in profiles if p.holdout),
        "alias_accepted": sum(1 for a in aliases if a.status == "accepted"),
        "alias_rejected": sum(1 for a in aliases if a.status == "rejected"),
        "batches": [{
            "batch_key": b.batch_key, "source_type": b.source_type,
            "source_name": b.source_name, "source_url": b.source_url,
            "license": b.license, "tier": b.tier, "authority": b.authority,
            "method": b.method, "robots_ok": b.robots_ok,
            "collected": b.collected, "kept": b.kept,
            "profiles": int(profiled.get(b.id, 0)), "raw_dir": b.raw_dir,
            "notes": b.notes,
        } for b in batches],
        "privacy_notice": "简历原文与姓名/联系方式不入库：talent_profile 表结构上就没有正文与身份字段，"
                          "落盘归档的正文也已脱敏，只保留技能要素与可回溯出处",
    }
