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


# ------------------------------------------------- 演化路径的溯源/颗粒度补全
# 演化此前只更新 JobSkill：不写证据、不传 parent、不重算 evidence_count。
# 后果是经演化新增的能力在「溯源证据」页一律落到「暂无独立JD证据」分支，
# 细粒度技能点全部退化成粗粒度——一个卖点是可溯源的系统，演化出来的能力无据可查。

def _cap_with_evidence(name, jd_ids, **kw):
    c = _cap(name)
    c.update(kw)
    c["evidence"] = [{"raw_jd_id": i, "source_type": "jd", "source": "tencent",
                      "source_url": f"https://x/{i}", "snippet": name} for i in jd_ids]
    return c


def test_apply_evolution_writes_evidence_for_new_capabilities(db):
    """新增能力必须带着证据落库，而不是只写一行 JobSkill。"""
    job = _job_with_skills(db, ["Java"])
    evolution.apply_evolution(db, job, [_cap_with_evidence("大语言模型", [1, 2, 3])], changes=[])

    js = db.query(models.JobSkill).join(
        models.Skill, models.Skill.id == models.JobSkill.skill_id).filter(
        models.Skill.name == "大语言模型").first()
    evs = db.query(models.Evidence).filter(models.Evidence.job_skill_id == js.id).all()
    assert len(evs) == 3
    assert {e.raw_jd_id for e in evs} == {1, 2, 3}
    assert all(e.source_url for e in evs), "证据必须带可点的原始 JD 链接"


def test_apply_evolution_does_not_duplicate_evidence_on_rerun(db):
    """同一批 JD 反复跑演化不许堆叠证据（演化批次会被重跑）。"""
    job = _job_with_skills(db, ["Java"])
    cap = _cap_with_evidence("大语言模型", [1, 2])
    evolution.apply_evolution(db, job, [cap], changes=[])
    evolution.apply_evolution(db, job, [cap], changes=[])

    js = db.query(models.JobSkill).join(
        models.Skill, models.Skill.id == models.JobSkill.skill_id).filter(
        models.Skill.name == "大语言模型").first()
    assert db.query(models.Evidence).filter(
        models.Evidence.job_skill_id == js.id).count() == 2


def test_apply_evolution_sets_parent_for_fine_skill(db):
    """演化新增的细粒度技能点要真的挂到父技能下，否则退化成粗粒度。"""
    job = _job_with_skills(db, ["Java"])
    evolution.apply_evolution(db, job, [
        _cap("大语言模型"),
        dict(_cap("vLLM推理部署"), parent="大语言模型"),
    ], changes=[])

    fine = db.query(models.Skill).filter(models.Skill.name == "vLLM推理部署").first()
    parent = db.query(models.Skill).filter(models.Skill.name == "大语言模型").first()
    assert fine.parent_id == parent.id


def test_apply_evolution_recomputes_evidence_count(db):
    """岗位卡片的「JD 支撑」要跟着演化后的能力集走，不能一直是建图时那个旧值。"""
    job = _job_with_skills(db, ["Java"])
    job.evidence_count = 999
    db.commit()
    evolution.apply_evolution(db, job, [_cap("Java"), _cap("大语言模型")], changes=[])
    assert job.evidence_count == 6      # 两条 active × source_count 3


# ---------------------------------------------------------------------------
# 2026-07-30 事故（与上面两例方向相反）：日志说降级、库里没降级
# ---------------------------------------------------------------------------
# 07-29 的修复让 apply_evolution 只按日志淘汰，堵住了"库里被静默改、日志空白"。
# 但 compute_changes 还会无条件产出「支持减弱→候选」的 modify，而 apply_evolution
# 根本不消费它，于是变成反过来的背离：日志记了 40 条降级，job_skill 20 行原封未动。
# 线上演化页因此首屏全是「确认能力项→候选能力项」，演化记录总数 621→661 与交付
# 文档对不上，而图谱其实没变。两个方向都要锁住。

def test_thin_window_emits_no_candidate_demotion():
    """窗口薄（交互式演化不传 window 信息）→ 一条「支持减弱」都不许记。

    回归 2026-07-30：线上误点两次，Java 各刷出 20 条 modify。根因是调用方把每条旧
    能力当 history 先验注入，它们必然只有 1 个来源、必然过不了 ≥2 闸门、必然落进
    这个分支——窗口薄本来就说明不了任何事。
    """
    old = [{"name": "Python", "importance": "required", "weight": 0.8, "confidence": 0.6,
            "source_count": 17}]
    # 旧能力靠 history 先验回到 new_caps，但只有 1 个来源、没过闸 → status 非 active
    new = [{"name": "Python", "importance": "required", "weight": 0.8, "confidence": 0.29,
            "source_count": 1, "status": "candidate"}]
    assert evolution.compute_changes(old, new) == []


def test_thick_window_still_reports_candidate_demotion():
    """窗口够厚（≥20 条 JD 且给了窗口技能集）→ 支持减弱要照记，不能一并堵死。"""
    old = [{"name": "Python", "importance": "required", "weight": 0.8, "confidence": 0.6,
            "source_count": 17}]
    new = [{"name": "Python", "importance": "required", "weight": 0.8, "confidence": 0.29,
            "source_count": 1, "status": "candidate"}]
    changes = evolution.compute_changes(old, new, window_skill_names={"Python"},
                                        window_jd_count=25)
    assert [c["change_type"] for c in changes] == ["modify"]
    assert changes[0]["new_value"]["status"] == "candidate"


def test_apply_evolution_applies_logged_candidate_demotion(db):
    """日志判了「降级为候选」，库里就要真降级——否则又是一次日志与事实背离。"""
    job = _job_with_skills(db, ["Java", "Python"])
    changes = [{"change_type": "modify", "skill_name": "Python", "importance": "required",
                "old_value": {"confidence": 0.6},
                "new_value": {"confidence": 0.29, "status": "candidate"},
                "reason": "支持减弱"}]
    evolution.apply_evolution(db, job, [_cap("Java")], changes=changes)

    assert _active_names(db, job) == {"Java"}
    py = db.query(models.JobSkill).join(
        models.Skill, models.Skill.id == models.JobSkill.skill_id).filter(
        models.Skill.name == "Python").first()
    assert py.status == "candidate"
    assert round(py.confidence, 4) == 0.29
    # 降级不是淘汰：一行都不该变成 deprecated
    assert db.query(models.JobSkill).filter(
        models.JobSkill.status == "deprecated").count() == 0
