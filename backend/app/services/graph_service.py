"""图谱持久化与查询服务。

把交叉验证后的能力项落库为 Job / Skill / JobSkill / Evidence，并提供全景图谱查询。
"""
from __future__ import annotations
import re
from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy import func
from .. import models, clients
from .taxonomy import skill_category, skill_type
from .hallucination import job_confidence

# 每个能力项最多留存的证据条数。source_count（提及该技能的 JD 数）可达上千，
# 全存下来是六位数行且对说服力没有边际收益；但只存 6 条时前端「87 来源」只能
# 展开出 6 张卡，看着像坏了。12 条够填满两列网格。
MAX_EVIDENCE_PER_SKILL = 12


def slugify(name: str) -> str:
    s = re.sub(r"[^\w一-鿿]+", "-", name.strip().lower()).strip("-")
    return s or "job"


def upsert_skill(db: Session, name: str, category: str | None = None,
                 stype: str | None = None, with_embedding: bool = False,
                 parent_name: str | None = None) -> models.Skill:
    sk = db.query(models.Skill).filter(models.Skill.normalized_name == name).first()
    if sk is None:
        sk = models.Skill(name=name, normalized_name=name,
                          category=category or skill_category(name),
                          skill_type=stype or skill_type(name))
        if with_embedding:
            sk.embedding = clients.embed(name)
        db.add(sk)
        db.flush()
    # 两级技能体系：细粒度技能挂到粗粒度父技能下（Skill.parent_id 层级树）
    if parent_name and parent_name != name and not sk.parent_id:
        parent = upsert_skill(db, parent_name, category, stype, with_embedding)
        if parent.id != sk.id:
            sk.parent_id = parent.id
            db.flush()
    return sk


def write_evidence(db: Session, job_skill_id: int, evidence: list[dict],
                   cap: int = MAX_EVIDENCE_PER_SKILL) -> int:
    """为某条岗位-能力关系写入证据行，按 raw_jd_id 去重后返回新增条数。

    抽成公共函数是因为 evolution.apply_evolution 此前**根本不写证据**：
    交叉验证聚合明明在 capability["evidence"] 里给出了带 raw_jd_id 的证据，
    演化路径直接忽略了它，于是每一条经演化新增/刷新的能力在「溯源证据」页
    都落到「该能力项暂无独立JD证据」分支——一个卖点是可溯源的系统，
    演化出来的能力反而无据可查。两条写库路径共用同一个函数，避免再次漂移。
    """
    if not evidence:
        return 0
    seen = {r[0] for r in db.query(models.Evidence.raw_jd_id).filter(
        models.Evidence.job_skill_id == job_skill_id).all() if r[0] is not None}
    n = 0
    for ev in evidence:
        if n >= cap:
            break
        rid = ev.get("raw_jd_id")
        if rid is not None and rid in seen:
            continue          # 同一条 JD 不重复举证（演化可能被跑很多次）
        if rid is not None:
            seen.add(rid)
        db.add(models.Evidence(
            job_skill_id=job_skill_id, raw_jd_id=rid,
            source_type=ev.get("source_type", "jd"),
            source_name=ev.get("source") or ev.get("source_name"),
            source_url=ev.get("source_url", ""),
            snippet=(ev.get("snippet") or "")[:500], weight=ev.get("weight", 1.0)))
        n += 1
    return n


def rebuild_conflict(db: Session, job_title: str) -> dict | None:
    """岗位已跑过演化时返回冲突说明，否则 None。**全量重建前必须问一次。**

    `upsert_job` 是「先清空 JobSkill/Evidence 再重建」，对一个已经跑过
    v1→v2→v3 演化的岗位重跑聚合，会把演化结果连同 143 条淘汰、111 条修改
    对应的库表事实一起冲掉，而 capability_change 表里的审计记录还留着——
    审计日志与库表事实背离，正是 repair_click_damage.py 记录的那类事故。

    判据取「version > 1 或存在任何 capability_change 行」而不是只看版本号：
    人工编辑（jobs.manual_edit）也会写变更记录，那同样是不该被无声抹掉的人工产出。

    刻意不放进 upsert_job 内部：它的返回值是 Job，在里面抛异常会打断
    人工编辑等合法调用方。由调用层（ingest 编排、脚本）决定跳过还是 --force。
    """
    job = db.query(models.Job).filter(models.Job.slug == slugify(job_title)).first()
    if not job:
        return None
    n_changes = db.query(models.CapabilityChange).filter(
        models.CapabilityChange.job_id == job.id).count()
    if (job.version or 1) <= 1 and n_changes == 0:
        return None
    n_caps = db.query(models.JobSkill).filter(
        models.JobSkill.job_id == job.id,
        models.JobSkill.status == "active").count()
    return {
        "reason": "job_has_evolution_history",
        "job_id": job.id, "job_name": job.name, "version": job.version,
        "changes": n_changes, "active_capabilities": n_caps,
        "message": (f"「{job.name}」已跑过演化（v{job.version}，{n_changes} 条变更记录，"
                    f"{n_caps} 项已验证能力），跳过重建以免冲掉演化结果。"
                    "确需重建请显式加 --force-rebuild-evolved。"),
    }


def upsert_job(db: Session, *, job_title: str, category: str, level: str,
               responsibilities: list, scenarios: list, capabilities: list[dict],
               is_new: bool | None = False, summary: str = "",
               source_summary: dict | None = None,
               emergence_score: float | None = 0.0, with_embedding: bool = True) -> models.Job:
    """根据聚合能力项创建/更新岗位及其能力关系。

    is_new / emergence_score 传 None 表示「保留库中现值」。这是给全量语料重建用的：
    流水线对每个岗位一律传 is_new=False，而 6 个新兴岗位的 is_new/emergence_score
    是 seed_new_jobs.py 依据人社部文件单独标注的策展信息——重建一次就被抹平一次，
    新兴岗位数从 6 掉到 0。让「不知道」和「确定为 False」在签名上可区分。
    """
    slug = slugify(job_title)
    job = db.query(models.Job).filter(models.Job.slug == slug).first()
    if not job:
        job = models.Job(name=job_title, slug=slug)
        db.add(job)
        db.flush()
    job.category = category
    job.level = level
    if is_new is not None:
        job.is_new = is_new
    job.summary = summary
    job.core_responsibilities = responsibilities
    job.typical_scenarios = scenarios
    if emergence_score is not None:
        job.emergence_score = emergence_score
    job.source_summary = source_summary or {}
    active_caps = [c for c in capabilities if c.get("status") == "active"]
    job.confidence = job_confidence(capabilities)
    job.evidence_count = sum(c.get("source_count", 0) for c in active_caps)
    if with_embedding:
        job.embedding = clients.embed(f"{job_title} {summary} " + " ".join(c["name"] for c in active_caps[:15]))

    # 清空旧能力关系后重建（先删证据子表，避免外键约束）
    old_js_ids = [r[0] for r in db.query(models.JobSkill.id)
                  .filter(models.JobSkill.job_id == job.id).all()]
    if old_js_ids:
        db.query(models.Evidence).filter(
            models.Evidence.job_skill_id.in_(old_js_ids)).delete(synchronize_session=False)
        db.query(models.JobSkill).filter(
            models.JobSkill.id.in_(old_js_ids)).delete(synchronize_session=False)
    db.flush()
    seen_skill_ids: set[int] = set()
    # active 全落；candidate **只落细粒度技能点**。
    #
    # 落 candidate 是因为赛题要求颗粒度到技能点，前端要能展开查看未通过交叉验证的
    # 技能点，演化判据④「降级为候选能力项，保留观察」也依赖它们真实存在于库中。
    # 但只限细粒度：全库既有 candidate 行 100% 是细粒度（Java 岗 169 条、AI产品经理
    # 86 条全部有 parent），「候选 = 单来源细粒度技能点」是文档与前端一致的口径。
    # 粗粒度落选项（某岗位 105 条 JD 里只有 1 条提到 Flask）属于「低置信过滤」，
    # 落库会一次多出四百来行、前端一处都不渲染（它掉进 coarse/fine 两套分组的缝里），
    # 单个岗位详情响应从 100 行涨到 1050 行，纯属负担。
    for c in capabilities:
        status = c.get("status", "active")
        if status == "candidate" and c.get("granularity") != "fine":
            continue
        if status not in ("active", "candidate"):
            continue          # deprecated 等历史状态由演化路径维护，重建不复制
        sk = upsert_skill(db, c["name"], c.get("category"), c.get("skill_type"),
                          with_embedding and status == "active",
                          parent_name=c.get("parent"))
        if sk.id in seen_skill_ids:
            continue  # 粗/细或大小写变体解析到同一技能 → 保留先插入的高置信项（防 uq_job_skill 冲突）
        seen_skill_ids.add(sk.id)
        js = models.JobSkill(
            job_id=job.id, skill_id=sk.id, importance=c["importance"],
            weight=c.get("weight", 0.5), level_required=c.get("level_required", "familiar"),
            confidence=c.get("confidence", 0.0), factors=c.get("factors"),
            source_count=c.get("source_count", 0),
            status=status,
            first_seen=datetime.utcnow(), last_seen=datetime.utcnow())
        db.add(js)
        db.flush()
        write_evidence(db, js.id, c.get("evidence", []))
    db.commit()
    return job


def skill_meta(db: Session, skill_ids) -> dict:
    """批量取技能元信息，返回 {skill_id: Row(id, name, category, skill_type, parent_id)}。

    只选树/详情需要的 5 个列：Skill.embedding 是 512 维 JSON，整行 ORM 取回来时
    几百个技能就是好几 MB 的传输 + JSON 解析，纯属浪费。
    """
    ids = {i for i in skill_ids if i is not None}
    if not ids:
        return {}
    SK = models.Skill
    rows = db.query(SK.id, SK.name, SK.category, SK.skill_type, SK.parent_id) \
             .filter(SK.id.in_(ids)).all()
    return {r.id: r for r in rows}


def job_to_dict(db: Session, job: models.Job, include_candidates: bool = False) -> dict:
    js = db.query(models.JobSkill).filter(models.JobSkill.job_id == job.id).all()
    skills = []
    # 技能元信息 1 条 SQL 批量取；父技能名再补 1 条（父技能不一定出现在本岗位的技能里）。
    # 历史实现对每个有 parent_id 的技能单独 query 一次 Skill（N+1），
    # 技能多的岗位就是几百次公网往返。
    skill_rows = skill_meta(db, [j.skill_id for j in js])
    parent_rows = skill_meta(db, {r.parent_id for r in skill_rows.values()
                                  if r.parent_id and r.parent_id not in skill_rows})
    for j in js:
        sk = skill_rows.get(j.skill_id)
        if not sk:
            continue
        parent = skill_rows.get(sk.parent_id) or parent_rows.get(sk.parent_id)
        skills.append({
            "id": j.id, "skill_id": sk.id, "name": sk.name, "category": sk.category,
            "skill_type": sk.skill_type, "importance": j.importance, "weight": j.weight,
            "level_required": j.level_required, "confidence": j.confidence,
            "factors": j.factors, "source_count": j.source_count, "status": j.status,
            "parent_id": sk.parent_id, "parent_name": parent.name if parent else None,
            "granularity": "fine" if sk.parent_id else "coarse",
        })
    required = [s for s in skills if s["importance"] == "required"]
    bonus = [s for s in skills if s["importance"] == "bonus"]
    return {
        "id": job.id, "name": job.name, "slug": job.slug, "category": job.category,
        "level": job.level, "is_new": job.is_new, "status": job.status,
        "summary": job.summary, "core_responsibilities": job.core_responsibilities or [],
        "typical_scenarios": job.typical_scenarios or [],
        "required_skills": sorted(required, key=lambda x: x["weight"], reverse=True),
        "bonus_skills": sorted(bonus, key=lambda x: x["weight"], reverse=True),
        "confidence": job.confidence, "evidence_count": job.evidence_count,
        "emergence_score": job.emergence_score, "version": job.version,
        "emergence_type": job.emergence_type,
        "first_seen_date": job.first_seen_date.isoformat() if job.first_seen_date else None,
        "source_summary": job.source_summary or {},
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "updated_at": job.updated_at.isoformat() if job.updated_at else None,
    }


#: 全景图技能节点默认上限（力导向图可读性 + 载荷上限的安全阀）。
#: 当前语料粗粒度技能仅 147/573 个，默认值不会触发截断；
#: 触发时按 degree（关联岗位数）降序保留 top-N，并在 stats 中如实标注 truncated/skills_total。
DEFAULT_MAX_SKILLS = 400


def panoramic_graph(db: Session, category: str | None = None, level: str | None = None,
                    min_confidence: float = 0.0, granularity: str = "coarse",
                    max_skills: int = DEFAULT_MAX_SKILLS) -> dict:
    """构建全景图谱：岗位节点 + 技能点节点 + 关系边。

    granularity="coarse"（默认）只展示粗粒度技能节点（parent_id 为空），
    细粒度技能在岗位详情页按父类分组展示，避免全景图节点爆炸。

    性能：整图只用 2 条 SQL（岗位 1 条 + job_skill⋈skill 批量 join 1 条），
    组装在内存里完成。历史实现对每条 JobSkill 单独 query 一次 Skill（N+1），
    在云 MySQL 上是几千次公网往返，单请求要几十秒。

    max_skills：技能节点上限，超出时按 degree 降序截断，
    并置 stats.truncated=True + stats.skills_total（不静默丢数据）。
    """
    q = db.query(models.Job).filter(models.Job.status == "published")
    if category and category != "全部":
        q = q.filter(models.Job.category == category)
    if level and level != "全部":
        q = q.filter(models.Job.level == level)
    jobs = q.all()

    nodes: list[dict] = [
        {"id": f"job-{job.id}", "name": job.name, "type": "job",
         "category": job.category, "level": job.level, "is_new": bool(job.is_new),
         "confidence": job.confidence, "value": 30}
        for job in jobs
    ]
    if not jobs:
        return {"nodes": nodes, "edges": [],
                "stats": {"jobs": 0, "skills": 0, "relations": 0,
                          "skills_total": 0, "truncated": False, "max_skills": max_skills}}

    # 一次性把「岗位-技能」关系连同技能属性 join 出来，粗/细粒度过滤下推到 SQL，
    # 避免捞出细粒度技能后在 Python 里丢掉（本库 4567 技能中仅 573 个是粗粒度）。
    JS, SK = models.JobSkill, models.Skill
    rows_q = (
        db.query(JS.job_id, JS.importance, JS.weight, JS.confidence,
                 SK.id, SK.name, SK.category, SK.skill_type)
        .join(SK, SK.id == JS.skill_id)
        .filter(JS.job_id.in_([j.id for j in jobs]),
                JS.status == "active",
                JS.confidence >= min_confidence)
    )
    if granularity == "coarse":
        rows_q = rows_q.filter(SK.parent_id.is_(None))
    # 按 (job_id, skill_id) 排序：复现原「逐岗位查询」的行序——MySQL 对 job_id 等值条件
    # 会走 uq_job_skill(job_id, skill_id) 索引，天然按 skill_id 升序返回，因此节点/边的
    # 输出顺序与优化前逐字节一致。（本查询实际由 skill.parent_id 侧驱动 + filesort，
    # 但结果集只有几百行，排序开销可忽略，无需为此加索引。）
    rows = rows_q.order_by(JS.job_id, JS.skill_id).all()

    by_job: dict[int, list] = {}
    for r in rows:
        by_job.setdefault(r[0], []).append(r)

    edges: list[dict] = []
    skill_seen: dict[int, dict] = {}
    for job in jobs:  # 外层顺序沿用 jobs 查询顺序，保证与原实现一致
        for _, importance, weight, confidence, sk_id, sk_name, sk_cat, sk_type in by_job.get(job.id, ()):
            if sk_id not in skill_seen:
                skill_seen[sk_id] = {"id": f"skill-{sk_id}", "name": sk_name, "type": "skill",
                                     "category": sk_cat, "skill_type": sk_type,
                                     "value": 10, "degree": 0}
            skill_seen[sk_id]["degree"] += 1
            edges.append({"source": f"job-{job.id}", "target": f"skill-{sk_id}",
                          "importance": importance, "weight": round(weight, 3),
                          "confidence": round(confidence, 3)})

    skills_total = len(skill_seen)
    truncated = max_skills is not None and max_skills > 0 and skills_total > max_skills
    if truncated:
        # 保留热度（关联岗位数）最高的 top-N 技能节点，并同步剔除悬空边
        keep = {s["id"] for s in sorted(skill_seen.values(),
                                        key=lambda s: s["degree"], reverse=True)[:max_skills]}
        skill_seen = {k: v for k, v in skill_seen.items() if v["id"] in keep}
        edges = [e for e in edges if e["target"] in keep]

    for s in skill_seen.values():
        s["value"] = 8 + min(40, s["degree"] * 4)
        nodes.append(s)
    return {"nodes": nodes, "edges": edges,
            "stats": {"jobs": len(jobs), "skills": len(skill_seen), "relations": len(edges),
                      "skills_total": skills_total, "truncated": truncated,
                      "max_skills": max_skills}}


def stats_overview(db: Session) -> dict:
    total_jobs = db.query(func.count(models.Job.id)).scalar() or 0
    new_jobs = db.query(func.count(models.Job.id)).filter(models.Job.is_new == True).scalar() or 0  # noqa: E712
    total_skills = db.query(func.count(models.Skill.id)).scalar() or 0
    total_jds = db.query(func.count(models.RawJD.id)).scalar() or 0
    dup_jds = db.query(func.count(models.RawJD.id)).filter(models.RawJD.is_duplicate == True).scalar() or 0  # noqa: E712
    by_cat = dict(db.query(models.Job.category, func.count(models.Job.id)).group_by(models.Job.category).all())
    avg_conf = db.query(func.avg(models.Job.confidence)).scalar() or 0
    return {
        "total_jobs": total_jobs, "new_jobs": new_jobs, "total_skills": total_skills,
        "total_jds": total_jds, "duplicate_jds": dup_jds,
        "categories": by_cat, "avg_confidence": round(float(avg_conf), 4),
    }
