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
