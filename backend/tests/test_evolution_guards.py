"""演化落库与发现落库的**破坏性防护**回归测试（内存 SQLite，不碰云库）。

这两个用例锁的是 2026-07-28 线上误点暴露的真实事故：

1. `apply_evolution` 曾用「本轮聚合出的 active 集合」判淘汰，与 `compute_changes`
   的淘汰判据脱钩。交互式演化只贴几条 JD 时，旧能力仅靠 history 先验拿到 1 个来源，
   过不了「≥2 来源」闸门就被静默降级——变更日志 0 条 delete，库里 313 行 deprecated。
2. `/api/discovery/discover` 对已存在岗位调用 `upsert_job`，而后者会先清空
   JobSkill/Evidence 再重建，等于用十来项 LLM 现生成的能力抹掉 301 项交叉验证结果。
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app import models
from app.services import evolution


@pytest.fixture()
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    s = sessionmaker(bind=engine)()
    try:
        yield s
    finally:
        s.close()


def _job_with_skills(db, names):
    from app.services.graph_service import slugify
    job = models.Job(name="测试岗位", slug=slugify("测试岗位"),
                     category="人工智能", version=1)
    db.add(job)
    db.flush()
    for n in names:
        sk = models.Skill(name=n)
        db.add(sk)
        db.flush()
        db.add(models.JobSkill(job_id=job.id, skill_id=sk.id, importance="required",
                               status="active", source_count=5, confidence=0.9))
    db.commit()
    return job


def _cap(name, status="active"):
    return {"name": name, "importance": "required", "weight": 0.6,
            "level_required": "familiar", "confidence": 0.9,
            "source_count": 3, "status": status}


def _active_names(db, job):
    return {db.query(models.Skill.name).filter(models.Skill.id == js.skill_id).scalar()
            for js in db.query(models.JobSkill).filter(
                models.JobSkill.job_id == job.id,
                models.JobSkill.status == "active").all()}


def test_apply_evolution_does_not_deprecate_without_delete_record(db):
    """变更日志没判淘汰 → 一行都不许降级（回归：曾静默降级 313 行）。"""
    job = _job_with_skills(db, ["Java", "Spring", "Kafka"])
    # 本轮只聚合出 Java（Spring/Kafka 因样本太薄没过闸），但 compute_changes 没判淘汰
    evolution.apply_evolution(db, job, [_cap("Java")], changes=[])
    assert _active_names(db, job) == {"Java", "Spring", "Kafka"}


def test_apply_evolution_deprecates_exactly_the_logged_deletes(db):
    """日志判了谁淘汰，就只淘汰谁——库表与审计轨迹按构造一致。"""
    job = _job_with_skills(db, ["Java", "Spring", "Kafka"])
    changes = [{"change_type": "delete", "skill_name": "Spring",
                "importance": "required", "reason": "窗口内未再出现"}]
    evolution.apply_evolution(db, job, [_cap("Java")], changes=changes)
    assert _active_names(db, job) == {"Java", "Kafka"}
    dep = db.query(models.JobSkill).filter(models.JobSkill.status == "deprecated").all()
    assert len(dep) == 1


def test_apply_evolution_still_adds_new_capabilities(db):
    """加固不能把「新增」一起挡掉。"""
    job = _job_with_skills(db, ["Java"])
    evolution.apply_evolution(db, job, [_cap("Java"), _cap("大语言模型")], changes=[])
    assert _active_names(db, job) == {"Java", "大语言模型"}
    assert job.version == 2


def test_discovery_refuses_to_overwrite_existing_job(db):
    """已在图谱中的岗位不得被「发现」覆盖，能力关系必须一行不少。"""
    from app.routers.discovery import _save_if_absent

    job = _job_with_skills(db, ["提示工程", "LangChain", "Python编程"])
    definition = {
        "job_title": "测试岗位", "category": "人工智能", "level": "senior",
        "core_responsibilities": ["x"], "typical_scenarios": ["y"],
        "capabilities": [_cap("只有一项")], "summary": "s",
        "source_summary": {}, "emergence_score": 0.9,
    }
    saved, conflict = _save_if_absent(db, definition)

    assert saved is None
    assert conflict and conflict["reason"] == "already_exists"
    assert conflict["active_capabilities"] == 3
    # 关键：原能力关系未被清空，is_new 未被翻成 True
    assert _active_names(db, job) == {"提示工程", "LangChain", "Python编程"}
    assert not job.is_new
