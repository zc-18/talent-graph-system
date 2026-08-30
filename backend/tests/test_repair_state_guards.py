"""R6 数据修复护栏回归测试（内存 SQLite，绝不碰云库）。

锁三件事：

1. `repair_safety.assert_shadow_apply_target` —— 一次性修复脚本唯一的 apply 总闸。
   生产库 `talent_graph_v3` 永远不是发布目标；回滚库 `talent_graph_v2`、原始
   demo 库 `talent_graph` 同样进不来；非 SQLite 目标必须**同时**满足「库名含
   shadow」+ `--allow-shadow` + `--confirm-database` 与实际连接精确相等。
   这道闸是 CLAUDE.md 危险项 3/4 的补充：数据只活在云库里，而部署是单向的，
   所以 repair 必须先在影子库上验收再切库。

2. `state_reconcile` 的状态重算 —— 它按现有证据重算 job_skill 派生字段并降级
   不合闸的 active，口径必须和 `services/confidence.py`（全系统唯一公式）+
   `confidence_batch`（生产批算）完全一致，不许出现第三套：
   * 「独立来源」= 独立**雇主实体**，不是平台。同一雇主的 JD 散到三个平台仍是 1 家；
   * candidate 行的 factors/source_count/confidence 必须被刷新（生产批算历史上
     只喂 active，candidate 长期是陈旧值），但**永远不自动升级**；
   * 这是数据修复不是岗位演化：一条 `CapabilityChange` 都不许写。

3. 备份完整性与验证的无副作用性 —— 备份漏掉了它改过的行，就等于没有回滚路径。
"""
import json
import math
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app import models
from app.services import repair_safety, role_contract, state_reconcile


AS_OF = datetime(2026, 8, 30, 0, 0, 0)


@pytest.fixture()
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


# --------------------------------------------------------------- 造图工具


def _employer(db, name, parent_id=None, status="active"):
    row = models.Employer(name=name, normalized_name=name, parent_id=parent_id,
                          status=status)
    db.add(row)
    db.flush()
    return row


def _raw_jd(db, employer, platform, index):
    row = models.RawJD(
        job_title="后端开发工程师", company=employer.name, platform=platform,
        source=platform, raw_text=f"岗位要求 {index}", dedup_hash=f"hash-{index}",
        is_duplicate=False, duplicate_of=None, employer_id=employer.id,
        collected_at=AS_OF - timedelta(days=10), source_authority=1.0)
    db.add(row)
    db.flush()
    return row


def _job(db, name="后端开发工程师", version=1):
    job = models.Job(name=name, slug=f"slug-{name}", category="人工智能",
                     status="published", version=version, track="software",
                     industry="general", level="middle")
    db.add(job)
    db.flush()
    if version is not None:
        job_version = models.JobVersion(job_id=job.id, version=version,
                                        status="published",
                                        evidence_window={"dimensions": {
                                            "job_name": name, "seniority": "middle",
                                            "recruitment_type": "unspecified",
                                            "track": "software",
                                            "industry": "general"}})
        db.add(job_version)
        db.flush()
    return job


def _capability(db, job, skill_name, raw_jds, *, status="active",
                source_count=0, confidence=0.0, factors=None, with_snapshot=True):
    """一条能力项 + 它的 JD 证据（+ 当前版本投影行）。"""
    skill = models.Skill(name=skill_name, normalized_name=skill_name,
                         category="人工智能", skill_type="hard")
    db.add(skill)
    db.flush()
    relation = models.JobSkill(job_id=job.id, skill_id=skill.id,
                               importance="required", weight=0.6,
                               level_required="familiar", status=status,
                               source_count=source_count, confidence=confidence,
                               factors=factors)
    db.add(relation)
    db.flush()
    for raw in raw_jds:
        db.add(models.Evidence(job_skill_id=relation.id, raw_jd_id=raw.id,
                               source_type="jd", source_url=raw.source_url,
                               snippet=skill_name, weight=1.0))
    db.flush()
    if with_snapshot:
        version = state_reconcile._current_version(db, job)
        if version is not None:
            db.add(models.JobVersionSkill(
                job_version_id=version.id, skill_id=skill.id,
                capability_cluster=skill_name, importance="required",
                status=status, weight=0.6, confidence=confidence,
                level_required="familiar", factors=factors, evidence_refs=[]))
            db.flush()
    return relation, skill


def _mysql_session(database):
    """只构造 URL，不连库：guard 只读 dialect 与 url.database。"""
    engine = create_engine(
        f"mysql+pymysql://user:pw@127.0.0.1:3306/{database}?charset=utf8mb4")
    return sessionmaker(bind=engine)()


# ------------------------------------------------- 1. apply 目标护栏


def test_production_database_is_never_an_apply_target():
    """生产库无条件拒绝——哪怕两个确认参数都给全了。"""
    session = _mysql_session("talent_graph_v3")
    try:
        with pytest.raises(RuntimeError) as err:
            repair_safety.assert_shadow_apply_target(
                session, allow_shadow=True, confirm_database="talent_graph_v3")
        assert "talent_graph_v3" in str(err.value)
    finally:
        session.close()


@pytest.mark.parametrize("database", ["talent_graph_v2", "talent_graph"])
def test_rollback_and_demo_databases_are_refused_as_non_shadow(database):
    """回滚库与原始 demo 库不含 shadow，同样进不来（写坏 v2 等于失去回滚路径）。"""
    session = _mysql_session(database)
    try:
        with pytest.raises(RuntimeError):
            repair_safety.assert_shadow_apply_target(
                session, allow_shadow=True, confirm_database=database)
    finally:
        session.close()


def test_shadow_requires_both_confirmations():
    """影子库要「双确认」：--allow-shadow 与精确匹配实际连接的 --confirm-database。"""
    session = _mysql_session("talent_graph_v4_shadow")
    try:
        with pytest.raises(RuntimeError):  # 缺 --allow-shadow
            repair_safety.assert_shadow_apply_target(
                session, allow_shadow=False,
                confirm_database="talent_graph_v4_shadow")
        with pytest.raises(RuntimeError):  # --confirm-database 对不上实际连接
            repair_safety.assert_shadow_apply_target(
                session, allow_shadow=True, confirm_database="talent_graph_v5_shadow")
        with pytest.raises(RuntimeError):  # 忘了填 --confirm-database
            repair_safety.assert_shadow_apply_target(
                session, allow_shadow=True, confirm_database=None)
        assert repair_safety.assert_shadow_apply_target(
            session, allow_shadow=True,
            confirm_database="talent_graph_v4_shadow") == "talent_graph_v4_shadow"
    finally:
        session.close()


def test_sqlite_target_is_always_allowed(db):
    """SQLite 影子（seed_local_demo / rehearse_shadow_release 那条路）无需确认。"""
    assert repair_safety.assert_shadow_apply_target(
        db, allow_shadow=False, confirm_database=None) == "sqlite"


def test_backup_is_exclusive_and_timestamped(tmp_path):
    """备份文件不许被覆盖——覆盖等于把上一次的回滚点抹掉。"""
    path = repair_safety.backup_path(tmp_path, "job_skill_state_r6")
    assert path.name.startswith("job_skill_state_r6_") and path.suffix == ".json"
    repair_safety.write_json_exclusive(path, {"rows": [1, 2]})
    assert json.loads(path.read_text(encoding="utf-8")) == {"rows": [1, 2]}
    with pytest.raises(FileExistsError):
        repair_safety.write_json_exclusive(path, {"rows": []})


# ------------------------------------------------- 2. 状态重算口径


def test_active_below_employer_gate_is_demoted(db):
    """只有 1 家独立雇主的 active 必须降为 candidate（≥2 交叉验证闸门）。"""
    only = _employer(db, "甲公司")
    jds = [_raw_jd(db, only, "tencent", 1), _raw_jd(db, only, "netease", 2)]
    job = _job(db)
    relation, _skill = _capability(db, job, "Java", jds, status="active",
                                   source_count=2, confidence=0.9)
    db.flush()

    stats = state_reconcile.reconcile_all(db, as_of=AS_OF, run_id="t-demote")

    assert relation.status == "candidate"
    assert relation.source_count == 1
    assert stats["active_demoted"] == 1


def test_same_employer_across_platforms_is_one_source(db):
    """独立来源 = 独立雇主实体，不是平台：同一家散到三个平台仍然只算 1 家。

    对照组同一岗位另一条能力项由两家不同雇主支撑，保持 active。
    """
    one = _employer(db, "甲公司")
    two = _employer(db, "乙公司")
    syndicated = [_raw_jd(db, one, "tencent", 1), _raw_jd(db, one, "netease", 2),
                  _raw_jd(db, one, "iguopin", 3)]
    independent = [syndicated[0], _raw_jd(db, two, "tencent", 4)]
    job = _job(db)
    single, _ = _capability(db, job, "Java", syndicated, status="active",
                            source_count=3, confidence=0.9)
    cross, _ = _capability(db, job, "MySQL", independent, status="active",
                           source_count=2, confidence=0.9)
    db.flush()

    state_reconcile.reconcile_all(db, as_of=AS_OF, run_id="t-employer")

    assert (single.source_count, single.status) == (1, "candidate")
    assert (cross.source_count, cross.status) == (2, "active")


def test_employer_parent_folds_two_children_into_one_source(db):
    """挂了同一母公司的两家子实体折叠成 1 家——虚增的 diversity 会被收回去。"""
    parent = _employer(db, "中国联通总部")
    child_a = _employer(db, "联通山东省分公司", parent_id=parent.id)
    child_b = _employer(db, "联通安徽省分公司", parent_id=parent.id)
    jds = [_raw_jd(db, child_a, "iguopin", 1), _raw_jd(db, child_b, "iguopin", 2)]
    job = _job(db)
    relation, _ = _capability(db, job, "Java", jds, status="active",
                              source_count=2, confidence=0.9)
    db.flush()

    state_reconcile.reconcile_all(db, as_of=AS_OF, run_id="t-fold")

    assert (relation.source_count, relation.status) == (1, "candidate")


def test_candidate_metrics_are_refreshed_but_never_promoted(db):
    """candidate 的派生字段必须重算（生产批算历史上只喂 active，它一直是陈旧值），
    但达到 ≥2 雇主也**不会**被自动升级——升级是有人负责的动作，不是重算的副作用。"""
    one = _employer(db, "甲公司")
    two = _employer(db, "乙公司")
    jds = [_raw_jd(db, one, "tencent", 1), _raw_jd(db, two, "netease", 2)]
    job = _job(db)
    stale = {"support": 0.0, "diversity": 0.0, "freshness": 0.0,
             "authority": 0.0, "external": 0.0}
    relation, _ = _capability(db, job, "Java", jds, status="candidate",
                              source_count=0, confidence=0.0, factors=stale)
    db.flush()

    stats = state_reconcile.reconcile_all(db, as_of=AS_OF, run_id="t-candidate")

    assert relation.status == "candidate"          # 没有自动升级
    assert relation.source_count == 2              # 指标被刷新了
    assert relation.confidence > 0.0
    assert relation.factors != stale
    assert stats["candidates_refreshed"] == 1
    assert stats["auto_promoted"] == 0


def test_reconcile_writes_no_capability_change_and_one_audit_row(db):
    """数据修复不是岗位演化：一条 CapabilityChange 都不许写，只留一条修复清单。"""
    only = _employer(db, "甲公司")
    job = _job(db)
    _capability(db, job, "Java", [_raw_jd(db, only, "tencent", 1)],
                status="active", source_count=2, confidence=0.9)
    db.flush()

    stats = state_reconcile.reconcile_all(db, as_of=AS_OF, run_id="t-audit")

    assert db.query(models.CapabilityChange).count() == 0
    audits = db.query(models.AuditLog).filter(
        models.AuditLog.action == state_reconcile.AUDIT_ACTION).all()
    assert len(audits) == 1 and audits[0].target_id == "t-audit"
    assert audits[0].summary["policy"] == {
        "min_employers": role_contract.MIN_EMPLOYERS,
        "candidate_auto_promotion": False,
        "version_strategy": "revise_current_version_projection_in_place",
        "capability_change_created": False,
    }
    assert stats["audit_created"] is True


def test_version_projection_follows_the_demotion(db):
    """当前版本投影必须跟着降级走，否则图谱说 candidate、契约还按 active 投影。"""
    only = _employer(db, "甲公司")
    job = _job(db)
    relation, skill = _capability(db, job, "Java",
                                  [_raw_jd(db, only, "tencent", 1)],
                                  status="active", source_count=2, confidence=0.9)
    db.flush()

    state_reconcile.reconcile_all(db, as_of=AS_OF, run_id="t-version")

    snapshot = db.query(models.JobVersionSkill).filter(
        models.JobVersionSkill.skill_id == skill.id).one()
    assert snapshot.status == "candidate" == relation.status
    assert snapshot.confidence == relation.confidence


def test_reconcile_leaves_verify_clean(db):
    """reconcile 之后 verify_all 必须是空列表——空列表才是 commit 许可。"""
    one, two = _employer(db, "甲公司"), _employer(db, "乙公司")
    job = _job(db)
    _capability(db, job, "Java",
                [_raw_jd(db, one, "tencent", 1), _raw_jd(db, two, "netease", 2)],
                status="active", source_count=0, confidence=0.0)
    _capability(db, job, "MySQL", [_raw_jd(db, one, "tencent", 3)],
                status="active", source_count=9, confidence=0.9)
    db.flush()

    state_reconcile.reconcile_all(db, as_of=AS_OF, run_id="t-verify")
    assert state_reconcile.verify_all(db, as_of=AS_OF) == []


def test_verify_all_reports_real_factor_drift(db):
    """容差不能松到把真实漂移一起放过：第 2 位小数的差异必须报出来。"""
    one, two = _employer(db, "甲公司"), _employer(db, "乙公司")
    job = _job(db)
    relation, _ = _capability(
        db, job, "Java",
        [_raw_jd(db, one, "tencent", 1), _raw_jd(db, two, "netease", 2)],
        status="active", source_count=0, confidence=0.0)
    db.flush()
    state_reconcile.reconcile_all(db, as_of=AS_OF, run_id="t-drift")
    assert state_reconcile.verify_all(db, as_of=AS_OF) == []

    relation.factors = {**relation.factors,
                        "support": relation.factors["support"] + 0.01}
    db.flush()
    errors = state_reconcile.verify_all(db, as_of=AS_OF)
    assert any(str(relation.id) in message for message in errors)


def test_verify_all_tolerates_json_float_roundtrip_noise(db):
    """MySQL JSON 列不能按位往返 double：写 0.09523809523809523 读回可能差 1 ulp。

    `factors_from_jd` 的 support/diversity/freshness/authority 都**没有**四舍五入
    （同模块的 `compute` 取 4 位、`_freshness` 取 6 位），于是 2/21 这种分数以全精度
    落进 JSON 列。精确 `!=` 会把这 1 ulp 当成派生字段陈旧，让 commit 前验证必然失败，
    也让 plan 的 metrics_stale 被噪声放大。SQLite 用 json.dumps 能精确往返，
    所以这里直接注入 1 ulp 扰动来复现 MySQL 的失真。
    """
    assert state_reconcile.factors_equal(
        {"support": 0.09523809523809523}, {"support": 0.09523809523809525})
    assert not state_reconcile.factors_equal(
        {"support": 0.0952}, {"support": 0.0953})       # 真实漂移仍然算漂移
    assert not state_reconcile.factors_equal(
        {"support": 0.5}, {"support": 0.5, "diversity": 0.0})   # 键集合必须一致

    one, two = _employer(db, "甲公司"), _employer(db, "乙公司")
    job = _job(db)
    relation, _ = _capability(
        db, job, "Java",
        [_raw_jd(db, one, "tencent", 1), _raw_jd(db, two, "netease", 2)],
        status="active", source_count=0, confidence=0.0)
    db.flush()
    state_reconcile.reconcile_all(db, as_of=AS_OF, run_id="t-ulp")
    assert state_reconcile.verify_all(db, as_of=AS_OF) == []

    relation.factors = {
        key: math.nextafter(value, math.inf) if isinstance(value, float) else value
        for key, value in relation.factors.items()}
    db.flush()
    assert state_reconcile.verify_all(db, as_of=AS_OF) == []


# ------------------------------------------------- 3. 备份完整性与验证副作用


def test_backup_covers_versions_of_jobs_with_null_version(db):
    """`job.version IS NULL` 时 reconcile 仍然按 v1 改版本投影，备份必须一起兜住。

    回归：备份用 `JobVersion.version == Job.version` 裸相等，SQL 三值逻辑把
    NULL 行整个排除，于是备份文件里恰好缺了它实际改掉的那些行——等于没有回滚路径。
    """
    only = _employer(db, "甲公司")
    job = _job(db, version=1)
    relation, skill = _capability(db, job, "Java",
                                  [_raw_jd(db, only, "tencent", 1)],
                                  status="active", source_count=2, confidence=0.9)
    job.version = None
    db.flush()

    backup = state_reconcile.backup_projection(db)
    version = state_reconcile._current_version(db, job)
    assert version is not None
    assert version.id in {row["id"] for row in backup["current_versions"]}
    assert skill.id in {row["skill_id"] for row in backup["current_version_skills"]}

    state_reconcile.reconcile_all(db, as_of=AS_OF, run_id="t-nullver")
    snapshot = db.query(models.JobVersionSkill).filter(
        models.JobVersionSkill.skill_id == skill.id).one()
    assert snapshot.status == "candidate" == relation.status


def test_verify_all_does_not_rewrite_evidence_window(db):
    """verify 只发现问题，不许自己变成写者。

    `build_contract_from_version` 会把重算的 evidence_window 写回 ORM 对象；
    verify_all 借它比对，就顺手把值改了。调用方若因为别的原因 commit，
    就等于悄悄发布了一份它只想读的窗口。
    """
    only = _employer(db, "甲公司")
    job = _job(db)
    _capability(db, job, "Java", [_raw_jd(db, only, "tencent", 1)],
                status="active", source_count=2, confidence=0.9)
    db.flush()
    version = state_reconcile._current_version(db, job)
    version.evidence_window = {"dimensions": {"job_name": job.name,
                                              "seniority": "middle",
                                              "recruitment_type": "unspecified",
                                              "track": "software",
                                              "industry": "general"},
                               "jd_count": 999, "employer_count": 999}
    db.flush()
    before = json.dumps(version.evidence_window, sort_keys=True, ensure_ascii=False)

    errors = state_reconcile.verify_all(db, as_of=AS_OF)

    assert errors, "窗口被人为改成 999 应当被报为不一致"
    assert json.dumps(version.evidence_window, sort_keys=True,
                      ensure_ascii=False) == before


def test_plan_all_stages_nothing(db):
    """dry-run 必须真的零写入：plan_all 之后 session 不许有脏对象。"""
    one, two = _employer(db, "甲公司"), _employer(db, "乙公司")
    job = _job(db)
    _capability(db, job, "Java",
                [_raw_jd(db, one, "tencent", 1), _raw_jd(db, two, "netease", 2)],
                status="active", source_count=0, confidence=0.0)
    db.commit()

    stats = state_reconcile.plan_all(db, as_of=AS_OF)

    assert stats["jobs"] == 1 and stats["relations"] == 1
    assert stats["metrics_stale"] == 1          # source_count/confidence 都是陈旧值
    assert not db.dirty and not db.new and not db.deleted


def test_verify_all_refuses_two_level_employer_folding(db):
    """雇主折叠超过一层时两套口径会分叉，必须报错而不是静默放行。

    `confidence_batch._employer_key` 只跳一层 parent（决定 source_count 与 ≥2 闸门），
    `role_contract.build_contract_from_version` 走到根：祖孙三层时闸门算 2 家、
    契约算 1 家，关系留在 active 而契约把这项能力悄悄丢掉。
    """
    root = _employer(db, "集团总部")
    middle = _employer(db, "中间层子公司", parent_id=root.id)
    leaf = _employer(db, "孙公司", parent_id=middle.id)
    other = _employer(db, "另一家孙公司", parent_id=middle.id)
    job = _job(db)
    _capability(db, job, "Java",
                [_raw_jd(db, leaf, "tencent", 1), _raw_jd(db, other, "netease", 2)],
                status="active", source_count=2, confidence=0.9)
    db.flush()

    assert state_reconcile.employer_chain_violations(db)
    state_reconcile.reconcile_all(db, as_of=AS_OF, run_id="t-chain")
    errors = state_reconcile.verify_all(db, as_of=AS_OF)
    assert any("母公司" in message for message in errors)
    assert state_reconcile.plan_all(db, as_of=AS_OF)["employer_chain_violations"] == 2


def test_plan_all_counts_version_skills_without_a_relation(db):
    """JobSkill 已经没了、JobVersionSkill 还在的行永远刷不到，却仍被投影进契约。

    这类行只统计不擅自处置——怎么处理是策略决定，不是重算的副作用。
    """
    only = _employer(db, "甲公司")
    job = _job(db)
    relation, _skill = _capability(db, job, "Java",
                                   [_raw_jd(db, only, "tencent", 1)],
                                   status="active", source_count=2, confidence=0.9)
    orphan_skill = models.Skill(name="Kafka", normalized_name="Kafka")
    db.add(orphan_skill)
    db.flush()
    version = state_reconcile._current_version(db, job)
    db.add(models.JobVersionSkill(
        job_version_id=version.id, skill_id=orphan_skill.id,
        capability_cluster="Kafka", importance="required", status="active",
        weight=0.6, confidence=0.9, level_required="familiar",
        factors={}, evidence_refs=[]))
    db.commit()

    assert state_reconcile.plan_all(
        db, as_of=AS_OF)["version_skill_without_relation"] == 1
