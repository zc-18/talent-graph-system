"""全量/局部重建的**破坏性防护**回归测试（内存 SQLite，不碰云库）。

锁的是 2026-07-29 复查暴露的一类事故：`graph_service.upsert_job` 是「先清空
JobSkill/Evidence 再按传入能力项重建」，而调用它的路径有三条口径不一致的地方——

1. 流水线对每个岗位一律传 `is_new=False`，把 6 个新兴岗位依据人社部文件标注的
   `is_new/emergence_score` 一并抹平（新兴岗位数 6 → 0）；
2. 想重建单个岗位只能全量重跑，而全量重跑会把 13 个已跑过演化的岗位的能力项
   连同 621 条变更记录对应的库表事实一起冲掉，审计日志与库表事实就此背离；
3. `seed_new_jobs.py` 不带 --no-llm 时会用 4-6 个 LLM 粗概念覆盖语料建好的能力集，
   与 `/api/discovery/discover` 那次线上事故同一机制，只差一个命令行参数。

另外锁住两级技能体系的落库机制本身：细粒度能力项必须靠 `parent` 键挂上
`Skill.parent_id`，`discovery.make_cap` 不产出这个键正是 6 个新兴岗位
「只有大概念、没有技能点」的根因。
"""
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app import models
from app.services import graph_service, ingest


@pytest.fixture()
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    s = sessionmaker(bind=engine)()
    try:
        yield s
    finally:
        s.close()


def _cap(name, status="active", parent=None, importance="required"):
    c = {"name": name, "importance": importance, "weight": 0.6,
         "level_required": "familiar", "confidence": 0.9,
         "source_count": 3, "status": status}
    if parent:
        c["parent"] = parent
    return c


def _upsert(db, title="测试岗位", caps=None, **kw):
    kw.setdefault("category", "人工智能")
    kw.setdefault("level", "middle")
    kw.setdefault("responsibilities", [])
    kw.setdefault("scenarios", [])
    kw.setdefault("summary", "s")
    kw.setdefault("with_embedding", False)
    return graph_service.upsert_job(db, job_title=title,
                                    capabilities=caps if caps is not None else [_cap("Python")],
                                    **kw)


# --------------------------------------------------------------- 新兴标记保全

def test_upsert_job_preserves_emergence_markers_when_none(db):
    """is_new/emergence_score 传 None = 保留现值（回归：全量重建抹平新兴岗位）。"""
    job = _upsert(db, is_new=True, emergence_score=0.9)
    job.emergence_type = "new"
    db.commit()

    _upsert(db, caps=[_cap("Python"), _cap("PyTorch")], is_new=None, emergence_score=None)

    db.refresh(job)
    assert job.is_new is True
    assert job.emergence_score == pytest.approx(0.9)
    assert job.emergence_type == "new"          # 本就不是 upsert_job 的参数，顺带确认


def test_upsert_job_still_sets_is_new_when_explicit(db):
    """显式传值仍然生效——护栏不能把 seed/discovery 的正常路径改坏。"""
    job = _upsert(db, is_new=True, emergence_score=0.9)
    _upsert(db, is_new=False, emergence_score=0.0)
    db.refresh(job)
    assert job.is_new is False
    assert job.emergence_score == pytest.approx(0.0)


# --------------------------------------------------------- 已演化岗位重建冲突

def test_rebuild_conflict_blocks_evolved_job(db):
    """跑过演化的岗位必须拒绝重建，并报清楚版本/变更数/能力数。"""
    job = _upsert(db, caps=[_cap("Java"), _cap("Spring")])
    job.version = 3
    db.add(models.CapabilityChange(job_id=job.id, version=2, change_type="delete",
                                   skill_name="Struts", importance="required",
                                   reason="窗口内未再出现"))
    db.commit()

    c = graph_service.rebuild_conflict(db, "测试岗位")
    assert c and c["reason"] == "job_has_evolution_history"
    assert c["version"] == 3 and c["changes"] == 1 and c["active_capabilities"] == 2


def test_rebuild_conflict_allows_v1_job(db):
    """v1 且无变更记录的岗位可以放心重建。"""
    _upsert(db)
    assert graph_service.rebuild_conflict(db, "测试岗位") is None


def test_rebuild_conflict_none_for_unknown_job(db):
    """库里没有的岗位当然不冲突（新建路径不能被挡）。"""
    assert graph_service.rebuild_conflict(db, "不存在的岗位") is None


def test_rebuild_conflict_blocks_manual_edited_v1_job(db):
    """人工编辑也会写变更记录，同样不该被无声抹掉——判据不只看版本号。"""
    job = _upsert(db)
    db.add(models.CapabilityChange(job_id=job.id, version=1, change_type="add",
                                   skill_name="人工加的", importance="required",
                                   reason="manual"))
    db.commit()
    assert graph_service.rebuild_conflict(db, "测试岗位") is not None


# ------------------------------------------------- 聚合编排层：白名单与跳过

def _clusters(db, mapping):
    """{岗位名: JD条数} → (clusters, parsed_cache)，喂给 _aggregate_clusters。"""
    clusters, parsed = {}, {}
    for name, n in mapping.items():
        items = []
        for i in range(n):
            row = models.RawJD(job_title=name, source="t", platform="tencent",
                               raw_text=f"{name}-{i}")
            db.add(row)
            db.flush()
            parsed[row.id] = {"required_skills": [{"name": "Python", "level": "proficient"}],
                              "bonus_skills": [], "fine_skills": [],
                              "core_responsibilities": ["r"], "typical_scenarios": ["s"],
                              "category": "人工智能", "level": "middle", "summary": name}
            items.append({"row": row, "jd": None})
        clusters[name] = items
    db.commit()
    return clusters, parsed


def test_aggregate_clusters_skips_evolved_and_keeps_its_skills(db):
    """已演化岗位的 JobSkill 行必须**逐行原样保留**（id 不变），v1 岗位正常重建。

    这条就是能挡住 repair_click_damage.py 那类事故的测试。
    """
    evolved = _upsert(db, title="已演化岗位", caps=[_cap("Java"), _cap("Spring")])
    evolved.version = 3
    db.add(models.CapabilityChange(job_id=evolved.id, version=2, change_type="add",
                                   skill_name="大语言模型", importance="required", reason="r"))
    db.commit()
    before = sorted(r[0] for r in db.query(models.JobSkill.id)
                    .filter(models.JobSkill.job_id == evolved.id).all())

    clusters, parsed = _clusters(db, {"已演化岗位": 3, "普通岗位": 3})
    results, skipped = ingest._aggregate_clusters(db, clusters, parsed)

    assert [s["job_name"] for s in skipped] == ["已演化岗位"]
    assert [r["job"] for r in results] == ["普通岗位"]
    after = sorted(r[0] for r in db.query(models.JobSkill.id)
                   .filter(models.JobSkill.job_id == evolved.id).all())
    assert after == before, "已演化岗位的能力关系被重建了"


def test_aggregate_clusters_force_rebuilds_evolved(db):
    """--force-rebuild-evolved 是显式逃生口，必须真的能重建。"""
    evolved = _upsert(db, title="已演化岗位", caps=[_cap("Java")])
    evolved.version = 3
    db.commit()
    clusters, parsed = _clusters(db, {"已演化岗位": 3})
    results, skipped = ingest._aggregate_clusters(db, clusters, parsed, force=True)
    assert skipped == [] and [r["job"] for r in results] == ["已演化岗位"]


def _historical_jd(db, title, year, company, text, *, quality=0.8):
    row = models.RawJD(
        job_title=title, company=company, raw_text=text,
        source="fixture", platform="fixture", source_url=f"https://example.test/{year}/{text}",
        publish_date=datetime(year, 6, 1), is_duplicate=False, duplicate_of=None,
        quality_score=quality, source_authority=0.8,
    )
    db.add(row)
    db.flush()
    return row


def _parsed(required=(), bonus=(), fine=()):
    def coarse(name):
        return {"name": name, "raw": name, "level": "proficient"}

    def leaf(name):
        return {"name": name, "raw": name, "parent": "Java",
                "importance": "required", "level": "proficient"}

    return {
        "required_skills": [coarse(name) for name in required],
        "bonus_skills": [coarse(name) for name in bonus],
        "fine_skills": [leaf(name) for name in fine],
    }


def test_refresh_evolved_evidence_is_append_only_idempotent_and_exact(db):
    """证据刷新只能给精确命中的既有关系追加 Evidence，版本/审计/岗位字段原样保留。"""
    job = _upsert(db, title="已演化岗位", caps=[_cap("Java"), _cap("Spring")])
    job.version = 3
    job.confidence = 0.731
    job.evidence_count = 77
    job.source_summary = {"locked": True, "era_counts": {"2026": 9}}
    user = models.AppUser(username="refresh-guard", password_hash="x", role="admin")
    db.add(user)
    db.flush()
    java = db.query(models.Skill).filter(models.Skill.name == "Java").one()
    version = models.JobVersion(job_id=job.id, version=3, status="published",
                                summary="frozen")
    db.add(version)
    db.flush()
    db.add(models.JobVersionSkill(job_version_id=version.id, skill_id=java.id,
                                  importance="required", status="active",
                                  weight=0.8, confidence=0.7,
                                  level_required="proficient"))
    db.add(models.CapabilityChange(job_id=job.id, version=3, change_type="add",
                                   skill_name="Spring", importance="required", reason="reviewed"))
    db.add(models.EvolutionRun(job_id=job.id, created_by=user.id, from_version=2,
                               proposed_version=3, status="published"))
    java_jd = _historical_jd(db, job.name, 2024, "甲公司", "java")
    unknown_jd = _historical_jd(db, job.name, 2025, "乙公司", "rust")
    db.commit()

    js_columns = [column.name for column in models.JobSkill.__table__.columns]
    before_js = [tuple(getattr(row, name) for name in js_columns) for row in
                 db.query(models.JobSkill).filter_by(job_id=job.id).order_by(models.JobSkill.id)]
    before_counts = {
        "changes": db.query(models.CapabilityChange).filter_by(job_id=job.id).count(),
        "versions": db.query(models.JobVersion).filter_by(job_id=job.id).count(),
        "version_skills": db.query(models.JobVersionSkill).count(),
        "runs": db.query(models.EvolutionRun).filter_by(job_id=job.id).count(),
    }
    locked_job = (job.confidence, job.version, job.source_summary, job.evidence_count)
    items = [{"row": java_jd, "jd": None}, {"row": unknown_jd, "jd": None}]
    parsed = {java_jd.id: _parsed(required=("Java",)),
              unknown_jd.id: _parsed(required=("Rust",))}

    result = ingest.refresh_job_evidence(db, job, items, parsed)
    db.commit()
    assert result["added_evidence"] == 1
    evidence_names = {skill.name for skill in db.query(models.Skill).join(
        models.JobSkill, models.JobSkill.skill_id == models.Skill.id).join(
        models.Evidence, models.Evidence.job_skill_id == models.JobSkill.id).filter(
        models.JobSkill.job_id == job.id).all()}
    assert evidence_names == {"Java"}
    assert db.query(models.Skill).filter(models.Skill.name == "Rust").count() == 0
    after_js = [tuple(getattr(row, name) for name in js_columns) for row in
                db.query(models.JobSkill).filter_by(job_id=job.id).order_by(models.JobSkill.id)]
    assert after_js == before_js
    assert {
        "changes": db.query(models.CapabilityChange).filter_by(job_id=job.id).count(),
        "versions": db.query(models.JobVersion).filter_by(job_id=job.id).count(),
        "version_skills": db.query(models.JobVersionSkill).count(),
        "runs": db.query(models.EvolutionRun).filter_by(job_id=job.id).count(),
    } == before_counts
    db.refresh(job)
    assert (job.confidence, job.version, job.source_summary, job.evidence_count) == locked_job

    assert ingest.refresh_job_evidence(db, job, items, parsed)["added_evidence"] == 0
    db.commit()
    assert db.query(models.Evidence).count() == 1


def test_refresh_evolved_evidence_reuses_coarse_and_fine_normalization(db):
    """粗/细两层分别沿用 extraction 的规范化，不把别名当新技能。"""
    job = _upsert(db, title="已演化岗位", caps=[
        _cap("Spring"), _cap("JVM调优", parent="Java")])
    job.version = 2
    coarse_jd = _historical_jd(db, job.name, 2024, "甲公司", "spring-boot")
    fine_jd = _historical_jd(db, job.name, 2025, "乙公司", "jvm")
    db.commit()

    parsed = {
        coarse_jd.id: _parsed(required=("spring boot",)),
        fine_jd.id: _parsed(fine=("jvm调优",)),
    }
    result = ingest.refresh_job_evidence(
        db, job, [{"row": coarse_jd}, {"row": fine_jd}], parsed)
    db.commit()

    assert result["added_evidence"] == 2
    links = {(skill.name, evidence.raw_jd_id) for skill, evidence in db.query(
        models.Skill, models.Evidence).join(
        models.JobSkill, models.JobSkill.skill_id == models.Skill.id).join(
        models.Evidence, models.Evidence.job_skill_id == models.JobSkill.id).filter(
        models.JobSkill.job_id == job.id).all()}
    assert links == {("Spring", coarse_jd.id), ("JVM调优", fine_jd.id)}


def test_refresh_evolved_evidence_preserves_year_diversity_and_cap(db):
    """证据槽紧张时先补未覆盖年份；已有大量 2026 证据也要留下 2024/2025。"""
    job = _upsert(db, title="已演化岗位", caps=[_cap("Java")])
    job.version = 2
    js = db.query(models.JobSkill).filter_by(job_id=job.id).one()
    old = [_historical_jd(db, job.name, 2026, f"旧公司{i}", f"old-{i}")
           for i in range(10)]
    graph_service.write_evidence(db, js.id, [
        {"raw_jd_id": row.id, "source_type": "jd", "snippet": "Java"} for row in old])
    historical = [
        _historical_jd(db, job.name, 2024, "历史甲", "history-2024", quality=0.6),
        _historical_jd(db, job.name, 2025, "历史乙", "history-2025", quality=0.9),
        _historical_jd(db, job.name, 2026, "当前丙", "current-2026", quality=1.0),
    ]
    db.commit()

    result = ingest.refresh_job_evidence(
        db, job, [{"row": row} for row in historical],
        {row.id: _parsed(required=("Java",)) for row in historical})
    db.commit()

    assert result["added_evidence"] == 2
    evidences = db.query(models.Evidence).filter_by(job_skill_id=js.id).all()
    assert len(evidences) == graph_service.MAX_EVIDENCE_PER_SKILL
    linked = {row.id: row for row in db.query(models.RawJD).filter(
        models.RawJD.id.in_([ev.raw_jd_id for ev in evidences])).all()}
    years = {linked[ev.raw_jd_id].publish_date.year for ev in evidences}
    assert {2024, 2025}.issubset(years)
    assert historical[2].id not in {ev.raw_jd_id for ev in evidences}


def test_refresh_evolved_evidence_dry_run_predicts_without_insert(db):
    """dry-run 使用同一选择器估算新增数，但 Evidence 保持零写入。"""
    job = _upsert(db, title="已演化岗位", caps=[_cap("Java")])
    job.version = 2
    row = _historical_jd(db, job.name, 2024, "甲公司", "java-dry")
    db.commit()

    result = ingest.refresh_job_evidence(
        db, job, [{"row": row}], {row.id: _parsed(required=("java",))}, dry_run=True)
    db.commit()

    assert result["dry_run"] is True
    assert result["estimated_new_evidence"] == 1
    assert result["added_evidence"] == 0
    assert db.query(models.Evidence).count() == 0


def test_aggregate_refreshes_evidence_but_still_skips_relation_rebuild(db):
    """opt-in 刷证据不改变 rebuild_conflict 的结论，JobSkill id/字段仍冻结。"""
    job = _upsert(db, title="已演化岗位", caps=[_cap("Java"), _cap("Spring")])
    job.version = 3
    db.add(models.CapabilityChange(job_id=job.id, version=2, change_type="add",
                                   skill_name="Spring", importance="required", reason="r"))
    db.commit()
    before = [(row.id, row.skill_id, row.importance, row.weight, row.confidence,
               row.source_count, row.status) for row in db.query(models.JobSkill).filter_by(
               job_id=job.id).order_by(models.JobSkill.id)]
    clusters, parsed = _clusters(db, {"已演化岗位": 1})
    row = clusters["已演化岗位"][0]["row"]
    row.publish_date = datetime(2024, 1, 1)
    parsed[row.id] = _parsed(required=("Java",))
    db.commit()

    results, skipped = ingest._aggregate_clusters(
        db, clusters, parsed, refresh_evolved_evidence=True)
    db.commit()

    assert results == []
    assert skipped[0]["reason"] == "existing_job_evidence_only"
    assert skipped[0]["changes"] == 1          # rebuild_conflict 的结论仍照实报出
    assert skipped[0]["evidence_refresh"]["added_evidence"] == 1
    after = [(item.id, item.skill_id, item.importance, item.weight, item.confidence,
              item.source_count, item.status) for item in db.query(models.JobSkill).filter_by(
              job_id=job.id).order_by(models.JobSkill.id)]
    assert after == before


def test_aggregate_evidence_only_also_protects_unevolved_existing_job(db):
    """证据-only 是全岗位安全语义：v1、无变更记录的岗位同样不重建，只追加证据。

    发布口径从「只保护 15 个演化岗」推广到「32 个既有岗位全部只追加证据」的原因是：
    未演化岗位走全量重聚合会重算置信度/新兴标记/分级画像，A/B 显示全库置信度反而
    从 0.5502 掉到 0.5451。这里锁住推广后的边界。
    """
    job = _upsert(db, title="未演化岗位", caps=[_cap("Java"), _cap("Spring")])
    db.commit()
    assert graph_service.rebuild_conflict(db, "未演化岗位") is None   # 前提：本可重建
    before = [(row.id, row.skill_id, row.importance, row.weight, row.confidence,
               row.source_count, row.status) for row in db.query(models.JobSkill).filter_by(
               job_id=job.id).order_by(models.JobSkill.id)]
    locked = (job.version, job.confidence, job.evidence_count)
    clusters, parsed = _clusters(db, {"未演化岗位": 1})
    row = clusters["未演化岗位"][0]["row"]
    row.publish_date = datetime(2024, 1, 1)
    parsed[row.id] = _parsed(required=("Java",))
    db.commit()

    results, skipped = ingest._aggregate_clusters(
        db, clusters, parsed, refresh_evidence_only=True)
    db.commit()

    assert results == []
    assert skipped[0]["reason"] == "existing_job_evidence_only"
    assert skipped[0]["changes"] == 0
    assert skipped[0]["evidence_refresh"]["added_evidence"] == 1
    after = [(item.id, item.skill_id, item.importance, item.weight, item.confidence,
              item.source_count, item.status) for item in db.query(models.JobSkill).filter_by(
              job_id=job.id).order_by(models.JobSkill.id)]
    assert after == before
    db.refresh(job)
    assert (job.version, job.confidence, job.evidence_count) == locked


def test_aggregate_evidence_only_never_creates_missing_job(db):
    """库里没有的簇在证据-only 模式下只报告，绝不静默建岗（否则等于绕开发布评审）。"""
    clusters, parsed = _clusters(db, {"库中不存在的岗位": 1})
    row = clusters["库中不存在的岗位"][0]["row"]
    parsed[row.id] = _parsed(required=("Java",))
    db.commit()

    results, skipped = ingest._aggregate_clusters(
        db, clusters, parsed, refresh_evidence_only=True)
    db.commit()

    assert results == []
    assert skipped[0]["reason"] == "missing_existing_job"
    assert db.query(models.Job).count() == 0
    assert db.query(models.Evidence).count() == 0


def test_rows_dry_run_reports_evolved_refresh_without_graph_write(db, monkeypatch):
    """from-db 编排的 dry-run 解析并估算演化岗位，但不插 Evidence。"""
    job = _upsert(db, title="已演化岗位", caps=[_cap("Java")])
    job.version = 2
    db.commit()
    row = _historical_jd(db, job.name, 2024, "甲公司", "java-build-dry")
    row.cluster_hint = None
    db.commit()
    monkeypatch.setattr(ingest, "canonical_job_names", lambda: frozenset({job.name}))
    monkeypatch.setattr(ingest, "title_key", lambda *args, **kwargs: job.name)

    result = ingest.build_graph_from_rows(
        db, [row], parse_fn=lambda _: _parsed(required=("Java",)), max_workers=1,
        dry_run=True, refresh_evolved_evidence=True)

    assert result["dry_run"] is True
    assert result["evolved_jobs_to_refresh"] == 1
    assert result["estimated_new_evidence"] == 1
    assert result["skipped_evolved"][0]["evidence_refresh"]["added_evidence"] == 0
    assert db.query(models.Evidence).count() == 0
    db.refresh(job)
    assert job.version == 2


def test_only_jobs_allowlist_builds_only_listed(db):
    """白名单模式只碰列出的岗位，其余簇连聚合都不跑。"""
    clusters, parsed = _clusters(db, {"甲岗位": 3, "乙岗位": 3, "丙岗位": 3})
    results, _ = ingest._aggregate_clusters(db, clusters, parsed, only_jobs={"乙岗位"})
    assert [r["job"] for r in results] == ["乙岗位"]
    assert {r[0] for r in db.query(models.Job.name).all()} == {"乙岗位"}


def test_aggregate_clusters_records_era_counts(db):
    """按时间切片的 JD 数落进 source_summary，供详情页说明「历史语料里检索到 0 条」。"""
    clusters, parsed = _clusters(db, {"甲岗位": 3})
    ingest._aggregate_clusters(db, clusters, parsed)
    job = db.query(models.Job).filter(models.Job.name == "甲岗位").first()
    assert job.source_summary["era_counts"] == {"2018": 0, "2024": 0, "2026": 3}


# ------------------------------------------------------ 两级技能体系落库机制

def test_upsert_job_sets_parent_for_fine_capability(db):
    """细粒度能力项靠 parent 键挂上 Skill.parent_id，granularity 由它派生。

    锁的是第 5 点的根因：discovery.make_cap 不产出 parent 键，
    所以新岗位发现路径建出来的能力项在结构上永远是粗粒度。
    """
    job = _upsert(db, caps=[_cap("大语言模型"),
                            _cap("vLLM推理部署", parent="大语言模型")])
    d = graph_service.job_to_dict(db, job)
    fine = [s for s in d["required_skills"] if s["name"] == "vLLM推理部署"][0]
    assert fine["granularity"] == "fine"
    assert fine["parent_name"] == "大语言模型"
    coarse = [s for s in d["required_skills"] if s["name"] == "大语言模型"][0]
    assert coarse["granularity"] == "coarse"


def test_upsert_job_persists_candidate_capabilities(db):
    """细粒度 candidate 落库（赛题要求技能点颗粒度可展开），但不计入 evidence_count。"""
    job = _upsert(db, caps=[_cap("大语言模型"),
                            dict(_cap("单来源碎片", status="candidate",
                                      parent="大语言模型"), granularity="fine",
                                 source_count=1)])
    rows = db.query(models.JobSkill).filter(models.JobSkill.job_id == job.id).all()
    assert {r.status for r in rows} == {"active", "candidate"}
    assert job.evidence_count == 3          # 只算 active 那条的 source_count
    d = graph_service.job_to_dict(db, job)
    assert any(s["status"] == "candidate" for s in d["required_skills"])


def test_upsert_job_drops_coarse_candidates(db):
    """粗粒度落选项属「低置信过滤」，不落库。

    全库既有 candidate 行 100% 是细粒度，「候选 = 单来源细粒度技能点」是文档与
    前端一致的口径。粗粒度 candidate 落库后掉进前端 coarse/fine 两套分组的缝里、
    一处都不渲染，实测让单个岗位详情从 100 行涨到 1050 行。
    """
    job = _upsert(db, caps=[_cap("Python"),
                            dict(_cap("Flask", status="candidate"), source_count=1)])
    rows = db.query(models.JobSkill).filter(models.JobSkill.job_id == job.id).all()
    assert len(rows) == 1 and rows[0].status == "active"


def test_upsert_job_ignores_deprecated_capabilities(db):
    """deprecated 是演化路径维护的历史状态，重建不复制。"""
    job = _upsert(db, caps=[_cap("Python"), _cap("Struts", status="deprecated")])
    names = {r[0] for r in db.query(models.Skill.name)
             .join(models.JobSkill, models.JobSkill.skill_id == models.Skill.id)
             .filter(models.JobSkill.job_id == job.id).all()}
    assert names == {"Python"}


def test_write_evidence_dedupes_by_raw_jd(db):
    """同一条 JD 不重复举证——演化可能被跑很多次。"""
    job = _upsert(db, caps=[_cap("Python")])
    js = db.query(models.JobSkill).filter(models.JobSkill.job_id == job.id).first()
    ev = [{"raw_jd_id": 1, "source_type": "jd", "snippet": "a"},
          {"raw_jd_id": 2, "source_type": "jd", "snippet": "b"}]
    assert graph_service.write_evidence(db, js.id, ev) == 2
    db.commit()
    assert graph_service.write_evidence(db, js.id, ev) == 0      # 再跑一次不堆叠
    db.commit()
    assert db.query(models.Evidence).filter(
        models.Evidence.job_skill_id == js.id).count() == 2


def test_write_evidence_respects_cap(db):
    """证据条数有上限：source_count 可达上千，全存下来没有边际收益。"""
    job = _upsert(db, caps=[_cap("Python")])
    js = db.query(models.JobSkill).filter(models.JobSkill.job_id == job.id).first()
    ev = [{"raw_jd_id": i, "source_type": "jd", "snippet": str(i)} for i in range(50)]
    n = graph_service.write_evidence(db, js.id, ev)
    assert n == graph_service.MAX_EVIDENCE_PER_SKILL


# ------------------------------------------------- 重建不得丢失证据 URL / 归簇闸门

def test_write_evidence_falls_back_to_raw_jd_url(db):
    """证据没带 URL 时，必须回落到该条 JD 自己的 source_url。

    锁的是第七轮实测到的一次静默数据丢失：`upsert_job` 每次全量重建都会先删掉
    旧证据再重写，而聚合产出的证据 dict 里没有 source_url。于是 9128 条「100% 带
    溯源链接」的证据重建一次就掉到 3795 条（37%），剩下 6387 条的 raw_jd 明明有
    URL。上一轮之所以看着是 100%，靠的是重建后有人记得补跑
    backfill_employers_evidence.py —— 一个必须靠人记住的步骤等于迟早会丢。
    时间线卡片的「URL 覆盖率」直接读这个字段，掉了就是溯源链断了。
    """
    job = _upsert(db, caps=[_cap("Python")])
    js = db.query(models.JobSkill).filter(models.JobSkill.job_id == job.id).first()
    db.add(models.RawJD(id=1, job_title="a", raw_text="x",
                        source_url="https://example.com/jd/1"))
    db.add(models.RawJD(id=2, job_title="b", raw_text="y", source_url=""))
    db.add(models.RawJD(id=3, job_title="c", raw_text="z", source_url="not-a-url"))
    db.flush()
    graph_service.write_evidence(db, js.id, [
        {"raw_jd_id": 1, "source_type": "jd", "snippet": "a"},
        {"raw_jd_id": 2, "source_type": "jd", "snippet": "b"},
        {"raw_jd_id": 3, "source_type": "jd", "snippet": "c"},
    ])
    db.commit()
    urls = {e.raw_jd_id: e.source_url for e in db.query(models.Evidence).filter(
        models.Evidence.job_skill_id == js.id).all()}
    assert urls[1] == "https://example.com/jd/1"   # 回落成功
    assert not urls[2]                             # JD 自己就没有，不编造
    assert not urls[3]                             # 非 http(s) 不当作可核验链接


def test_write_evidence_keeps_explicit_url_over_raw_jd(db):
    """证据自带 URL 时以它为准，回落只在缺省时生效。"""
    job = _upsert(db, caps=[_cap("Python")])
    js = db.query(models.JobSkill).filter(models.JobSkill.job_id == job.id).first()
    db.add(models.RawJD(id=1, job_title="a", raw_text="x",
                        source_url="https://example.com/raw"))
    db.flush()
    graph_service.write_evidence(db, js.id, [
        {"raw_jd_id": 1, "source_type": "jd", "snippet": "a",
         "source_url": "https://example.com/explicit"},
    ])
    db.commit()
    ev = db.query(models.Evidence).filter(models.Evidence.job_skill_id == js.id).first()
    assert ev.source_url == "https://example.com/explicit"


def test_title_key_rejects_non_engineering_titles_from_cluster_hint():
    """检索词命中 JD 正文就把非研发岗挂进技术岗簇——必须挡住。

    实测原文：小鹏「标准芯片-采购资深经理（电子元器件）」、蔚来「资深产品经理
    （智能座舱方向）」、联通广东「项目经理岗」都因为正文提了一句「车联网」而被
    打上 cluster_hint='车联网'，进而给车联网系统工程师供能力证据。全库 1494 个
    (JD,岗位) 对里有 214 个（14.3%）是这样挂上去的。
    """
    for title in ["标准芯片-采购资深经理（电子元器件）", "资深产品经理（智能座舱方向）",
                  "项目经理岗", "机器人销售专员"]:
        assert ingest.title_key(title, "车联网") != "车联网系统工程师", title


def test_title_key_still_trusts_cluster_hint_for_engineering_titles():
    """闸门只挡非研发岗，不得要求标题正向命中该簇领域词。

    第七轮踩过一次：把 import_raw 那份「标题必须自带簇领域词」的正向白名单搬到
    归簇这一层，结果大模型推理优化的 2026 语料从 49 条砍到 8 条、工业互联网从
    36 条砍到 7 条，还凭空多出 9 个只有 1 条 JD 的空岗位。正向白名单是对**新采集
    行**验证过的口径，对全量语料重新归簇会大面积误杀。
    """
    assert ingest.title_key("智能座舱软件工程师", "车联网") == "车联网系统工程师"
    assert ingest.title_key("推理引擎研发工程师", "大模型推理优化") == "大模型推理优化工程师"
    assert ingest.title_key("工业软件开发工程师", "工业互联网") == "工业互联网工程师"
