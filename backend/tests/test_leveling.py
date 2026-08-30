"""岗位分级（初/中/高级）逻辑与 SQLite 落库回归测试。"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models
from app.db import Base
from app.services import leveling
from app.services.evolution import compute_changes


class FakeRow:
    def __init__(self, level, text="有效JD文本" * 5, recruitment_type=None,
                 track=None, industry=None, title="Java开发工程师", row_id=None,
                 employer_id=None):
        self.id = row_id
        self.inferred_level = level
        self.raw_text = text
        self.recruitment_type = recruitment_type
        self.track = track
        self.industry = industry
        self.job_title = title
        self.cluster_hint = None
        self.lag_days = 0
        self.source = "test"
        self.platform = "test"
        self.source_authority = 0.6
        self.company = f"测试雇主{employer_id or row_id or 0}"
        self.employer_id = employer_id


@pytest.fixture()
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    value = sessionmaker(bind=engine)()
    try:
        yield value
    finally:
        value.close()


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


def test_bucket_slices_separate_campus_social_and_track():
    rows = ([FakeRow("junior", recruitment_type="campus", track="software")] * 3
            + [FakeRow("middle", recruitment_type="social", track="software")] * 4
            + [FakeRow("middle", recruitment_type="social", track="hardware",
                       title="硬件系统测试工程师")] * 3)
    slices = leveling.bucket_slices(rows)
    assert ("junior", "campus", "software", "general") in slices
    assert ("middle", "social", "software", "general") in slices
    assert ("middle", "social", "hardware", "general") in slices


# ---------- 画像重建不变量 ----------


def _job_with_skill(session, status="active"):
    job = models.Job(name="Java开发工程师", slug="java", status="published")
    skill = models.Skill(name="Java", normalized_name="Java")
    session.add_all([job, skill])
    session.flush()
    session.add(models.JobSkill(job_id=job.id, skill_id=skill.id, status=status))
    session.commit()
    return job, skill


def _two_level_rows():
    rows = []
    for idx, level in enumerate(("junior",) * 3 + ("middle",) * 3, start=1):
        rows.append(FakeRow(level, row_id=idx, employer_id=idx,
                            track="software", industry="general"))
    return rows


def _java_parse(_text):
    return {"required_skills": [{"name": "Java", "level": "proficient"}],
            "bonus_skills": [], "fine_skills": []}


def _prime_java_cache(monkeypatch):
    parsed = _java_parse("")
    raw_hash = leveling.cleaning.exact_hash(FakeRow("junior").raw_text)
    monkeypatch.setattr(leveling, "_load_cache", lambda _path: {raw_hash: parsed})


def test_rebuild_removes_stale_slice_and_reuses_active_skill_id(session, monkeypatch):
    _prime_java_cache(monkeypatch)
    job, skill = _job_with_skill(session)
    stale = models.JobLevelSkill(
        job_id=job.id, level="junior", recruitment_type="unspecified",
        track="unspecified", industry="general", skill_id=skill.id)
    session.add(stale)
    session.commit()

    profiles = leveling.build_level_profiles(
        session, job, rows=_two_level_rows(), parse_fn=_java_parse,
        cache_path="unused-test-cache.json")

    assert len(profiles) == 2
    stored = session.query(models.JobLevelSkill).filter_by(job_id=job.id).all()
    assert len(stored) == 2
    assert not any(row.track == "unspecified" for row in stored)
    assert {row.track for row in stored} == {"software"}
    assert {row.skill_id for row in stored} == {skill.id}
    assert all(session.query(models.JobSkill).filter_by(
        job_id=row.job_id, skill_id=row.skill_id, status="active").count() == 1
               for row in stored)


def test_rebuild_clears_old_profile_when_job_no_longer_has_two_buckets(session):
    job, skill = _job_with_skill(session)
    session.add(models.JobLevelSkill(
        job_id=job.id, level="junior", recruitment_type="unspecified",
        track="unspecified", industry="general", skill_id=skill.id))
    session.commit()

    profiles = leveling.build_level_profiles(
        session, job, rows=[FakeRow("junior", row_id=i, employer_id=i)
                            for i in range(1, 4)], parse_fn=_java_parse,
        cache_path="unused-test-cache.json")

    assert profiles == {}
    assert session.query(models.JobLevelSkill).filter_by(job_id=job.id).count() == 0


def test_empty_active_set_cannot_write_generated_capabilities(session, monkeypatch):
    _prime_java_cache(monkeypatch)
    job, skill = _job_with_skill(session, status="candidate")
    session.add(models.JobLevelSkill(
        job_id=job.id, level="junior", recruitment_type="unspecified",
        track="unspecified", industry="general", skill_id=skill.id))
    session.commit()

    profiles = leveling.build_level_profiles(
        session, job, rows=_two_level_rows(), parse_fn=_java_parse,
        cache_path="unused-test-cache.json")

    assert profiles == {}
    assert session.query(models.JobLevelSkill).filter_by(job_id=job.id).count() == 0


def test_service_does_not_commit_callers_transaction(session, monkeypatch):
    _prime_java_cache(monkeypatch)
    job, _skill = _job_with_skill(session)
    job.summary = "调用方尚未提交"

    profiles = leveling.build_level_profiles(
        session, job, rows=_two_level_rows(), parse_fn=_java_parse,
        cache_path="unused-test-cache.json")

    assert profiles
    assert session.query(models.JobLevelSkill).filter_by(job_id=job.id).count() == 2
    session.rollback()
    session.expire_all()
    assert session.get(models.Job, job.id).summary is None
    assert session.query(models.JobLevelSkill).filter_by(job_id=job.id).count() == 0


def test_invalid_target_deletion_is_rollbackable(session):
    job, skill = _job_with_skill(session)
    session.add(models.JobLevelSkill(
        job_id=job.id, level="junior", recruitment_type="unspecified",
        track="unspecified", industry="general", skill_id=skill.id))
    session.commit()

    assert leveling.build_level_profiles(
        session, job, rows=[FakeRow("junior", row_id=i) for i in range(3)],
        parse_fn=_java_parse, cache_path="unused-test-cache.json") == {}
    assert session.query(models.JobLevelSkill).filter_by(job_id=job.id).count() == 0
    session.rollback()
    assert session.query(models.JobLevelSkill).filter_by(job_id=job.id).count() == 1


def test_parse_failure_leaves_old_profile_untouched(session, monkeypatch):
    monkeypatch.setattr(leveling, "_load_cache", lambda _path: {})
    job, skill = _job_with_skill(session)
    session.add(models.JobLevelSkill(
        job_id=job.id, level="junior", recruitment_type="unspecified",
        track="unspecified", industry="general", skill_id=skill.id))
    session.commit()

    def fail_parse(_text):
        raise RuntimeError("parse failed")

    with pytest.raises(RuntimeError, match="parse failed"):
        leveling.build_level_profiles(
            session, job, rows=_two_level_rows(), parse_fn=fail_parse,
            cache_path="unused-test-cache.json")
    assert session.query(models.JobLevelSkill).filter_by(job_id=job.id).count() == 1


def test_cache_write_failure_leaves_old_profile_untouched(session, monkeypatch):
    monkeypatch.setattr(leveling, "_load_cache", lambda _path: {})
    monkeypatch.setattr(
        leveling, "_stage_cache",
        lambda _path, _cache: (_ for _ in ()).throw(OSError("cache failed")))
    job, skill = _job_with_skill(session)
    session.add(models.JobLevelSkill(
        job_id=job.id, level="junior", recruitment_type="unspecified",
        track="unspecified", industry="general", skill_id=skill.id))
    session.commit()

    with pytest.raises(OSError, match="cache failed"):
        leveling.build_level_profiles(
            session, job, rows=_two_level_rows(), parse_fn=_java_parse,
            cache_path="unused-test-cache.json")
    assert session.query(models.JobLevelSkill).filter_by(job_id=job.id).count() == 1


def test_unicode_normalization_reuses_confirmed_skill_id(session, monkeypatch):
    job = models.Job(name="Java开发工程师", slug="unicode-skill", status="published")
    skill = models.Skill(name="Ｊａｖａ", normalized_name="java-wide")
    session.add_all([job, skill])
    session.flush()
    session.add(models.JobSkill(job_id=job.id, skill_id=skill.id, status="active"))
    session.commit()
    _prime_java_cache(monkeypatch)

    profiles = leveling.build_level_profiles(
        session, job, rows=_two_level_rows(), parse_fn=_java_parse,
        cache_path="unused-test-cache.json")

    assert profiles
    assert {row.skill_id for row in session.query(models.JobLevelSkill).filter_by(
        job_id=job.id)} == {skill.id}


def test_normalized_active_skill_collision_fails_before_replacement(session, monkeypatch):
    _prime_java_cache(monkeypatch)
    job, first = _job_with_skill(session)
    second = models.Skill(name="Ｊａｖａ", normalized_name="java-wide")
    session.add(second)
    session.flush()
    session.add_all([
        models.JobSkill(job_id=job.id, skill_id=second.id, status="active"),
        models.JobLevelSkill(
            job_id=job.id, level="junior", recruitment_type="unspecified",
            track="unspecified", industry="general", skill_id=first.id),
    ])
    session.commit()

    with pytest.raises(ValueError, match="同名的 active 技能"):
        leveling.build_level_profiles(
            session, job, rows=_two_level_rows(), parse_fn=_java_parse,
            cache_path="unused-test-cache.json")
    stored = session.query(models.JobLevelSkill).filter_by(job_id=job.id).all()
    assert len(stored) == 1 and stored[0].skill_id == first.id


def test_cross_level_gate_counts_levels_not_slices(session, monkeypatch):
    job, skill = _job_with_skill(session)
    session.add(models.JobLevelSkill(
        job_id=job.id, level="senior", recruitment_type="unspecified",
        track="unspecified", industry="general", skill_id=skill.id))
    session.commit()
    rows = []
    for idx in range(3):
        rows.append(FakeRow(
            "junior", text="初级 Java JD", recruitment_type="campus",
            track="software", row_id=idx + 1, employer_id=idx + 1))
        rows.append(FakeRow(
            "junior", text="初级 Java JD", recruitment_type="social",
            track="software", row_id=idx + 11, employer_id=idx + 11))
        rows.append(FakeRow(
            "middle", text="中级 Python JD", recruitment_type="social",
            track="software", row_id=idx + 21, employer_id=idx + 21))
    cache = {
        leveling.cleaning.exact_hash("初级 Java JD"): _java_parse(""),
        leveling.cleaning.exact_hash("中级 Python JD"): {
            "required_skills": [{"name": "Python", "level": "proficient"}],
            "bonus_skills": [], "fine_skills": [],
        },
    }
    monkeypatch.setattr(leveling, "_load_cache", lambda _path: cache)

    def aggregate(items, source_meta=None):
        name = items[0]["required_skills"][0]["name"]
        return {"capabilities": [{
            "name": name, "status": "active", "granularity": "coarse",
            "importance": "required", "weight": 0.8,
            "level_required": "proficient", "confidence": 0.9,
            "factors": {}, "source_count": 3,
        }]}

    monkeypatch.setattr(leveling.hallucination, "aggregate_capabilities", aggregate)
    assert leveling.build_level_profiles(
        session, job, rows=rows, parse_fn=_java_parse,
        cache_path="unused-test-cache.json") == {}
    assert session.query(models.JobLevelSkill).filter_by(job_id=job.id).count() == 0


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
