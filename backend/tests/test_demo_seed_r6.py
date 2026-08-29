"""第六轮示例账号扩容脚本：dry-run 不写库、可重复执行、账号覆盖面成立。"""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models
from app.auth import verify_password
from app.db import Base
from data import seed_demo_accounts_r6 as seed


JOB_SPECS = (
    ("算法工程师", "人工智能", "software"),
    ("大数据开发工程师", "数据工程", "software"),
    ("后端开发工程师", "云计算与工程", "software"),
    ("前端开发工程师", "云计算与工程", "software"),
    ("测试开发工程师", "云计算与工程", "software"),
    ("机器学习平台工程师", "人工智能", "software"),
    ("数据分析师", "数据工程", "software"),
    ("产品运营专员", "云计算与工程", "product"),
    ("移动端开发工程师", "云计算与工程", "software"),
    ("质量保障工程师", "云计算与工程", "software"),
)

# 刻意挑落在**不同能力簇**的技能名：能力契约按簇投影，同簇技能会被折叠成一条，
# 全用同簇技能的话所有 persona 的覆盖率都会塌成同一个分数。
SKILL_SPECS = ("Python", "Spring Boot", "MySQL", "Kafka", "微服务",
               "Kubernetes", "机器学习", "RAG", "TCP/IP", "需求分析")


def _database():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    skills = []
    for name in SKILL_SPECS:
        skill = models.Skill(name=name, normalized_name=name, category="人工智能")
        db.add(skill)
        db.flush()
        skills.append(skill)
    for index, (name, category, track) in enumerate(JOB_SPECS):
        job = models.Job(name=name, slug=f"r6-job-{index}", category=category,
                         status="published", version=1, confidence=.85, level="middle",
                         track=track, industry="internet", recruitment_type="social",
                         core_responsibilities=[], typical_scenarios=[])
        db.add(job)
        db.flush()
        db.add(models.JobVersion(job_id=job.id, version=1, status="published"))
        for order, skill in enumerate(skills):
            db.add(models.JobSkill(
                job_id=job.id, skill_id=skill.id, status="active", importance="required",
                weight=.95 - order * .05, confidence=.8, source_count=3,
                factors={"support": .7, "diversity": 1.0, "freshness": .9,
                         "authority": .8, "external": .5}))
    # 现有三个账号：脚本只补昵称/头像，不负责创建
    for username, role in (("demo-user", "user"), ("demo-hr", "hr"), ("demo-admin", "admin")):
        db.add(models.AppUser(username=username, password_hash="unused",
                              role=role, status="active"))
    db.commit()
    return db


def _counts(db) -> dict:
    return {model.__tablename__: db.query(model).count() for model in (
        models.AppUser, models.Organization, models.OrganizationMember,
        models.ResumeProfile, models.MatchRun, models.FeedbackTicket,
        models.FeedbackRevision, models.FeedbackEvent,
        models.RecruitmentBatch, models.BatchCandidate)}


def test_plan_is_dry_run_and_writes_nothing():
    db = _database()
    try:
        before = _counts(db)
        planned = seed.plan(db)
        assert planned["mode"] == "dry-run" and planned["writes"] is False
        assert [item["action"] for item in planned["new_accounts"]] == ["create"] * 6
        assert _counts(db) == before
    finally:
        db.close()


def test_apply_is_idempotent_and_covers_all_roles():
    db = _database()
    try:
        first = seed.apply_seed(db)
        after_first = _counts(db)
        second = seed.apply_seed(db)
        assert _counts(db) == after_first, "重复执行不应产生新行"
        assert first["organizations"] == second["organizations"]

        # 9 个示例账号：原有 3 + 新增 5 个人用户 + 1 个 HR
        usernames = {row.username for row in db.query(models.AppUser).all()}
        assert len(usernames) == 9
        assert {"demo-user", "demo-hr", "demo-admin"} <= usernames

        # 原有账号补了昵称/头像，密码与角色没被动过
        for username in ("demo-user", "demo-hr", "demo-admin"):
            row = db.query(models.AppUser).filter(
                models.AppUser.username == username).one()
            assert row.nickname and row.avatar_url
            assert row.password_hash == "unused"

        # 新账号密码可登录、昵称头像齐全
        for persona in seed.PERSONAS:
            row = db.query(models.AppUser).filter(
                models.AppUser.username == persona["username"]).one()
            assert verify_password(persona["password"], row.password_hash)
            assert row.nickname == persona["nickname"]
            assert row.avatar_url.startswith("/avatars/a")
    finally:
        db.close()


def test_personas_are_differentiated():
    db = _database()
    try:
        report = seed.apply_seed(db)
        scores, jobs_seen = [], set()
        for persona in seed.PERSONAS:
            user = db.query(models.AppUser).filter(
                models.AppUser.username == persona["username"]).one()
            runs = db.query(models.MatchRun).filter(
                models.MatchRun.owner_user_id == user.id).all()
            assert len(runs) == persona["runs"]
            jobs_seen.update(run.job_id for run in runs)
            profile = db.query(models.ResumeProfile).filter(
                models.ResumeProfile.owner_user_id == user.id).one()
            assert profile.skills and profile.authorized
            assert profile.retention_expires_at is not None
            scores.append(max((run.result_snapshot or {}).get("overall_score", 0)
                              for run in runs))
        # 覆盖率 0.92 → 0.2，分数必须单调不增且拉得开
        assert scores == sorted(scores, reverse=True), scores
        assert scores[0] - scores[-1] > 20, scores
        assert len(jobs_seen) >= 5, "各 persona 应落在不同岗位上"

        statuses = {row.status for row in db.query(models.FeedbackTicket).all()}
        assert statuses == {"submitted", "triaged", "approved", "rejected"}
        assert report["total_accounts"] == 9
    finally:
        db.close()


def test_secondary_hr_lands_in_a_separate_tenant():
    db = _database()
    try:
        seed.apply_seed(db)
        hr = db.query(models.AppUser).filter(
            models.AppUser.username == seed.SECONDARY_HR["username"]).one()
        assert hr.role == "hr"
        organization = db.query(models.Organization).filter(
            models.Organization.name == seed.SECONDARY_ORGANIZATION).one()
        member = db.query(models.OrganizationMember).filter(
            models.OrganizationMember.user_id == hr.id).one()
        assert member.organization_id == organization.id and member.status == "active"

        batch = db.query(models.RecruitmentBatch).one()
        assert batch.organization_id == organization.id
        assert batch.idempotency_key == seed.SECONDARY_BATCH_KEY
        assert batch.succeeded_count == batch.total_count == 3
        candidates = db.query(models.BatchCandidate).filter(
            models.BatchCandidate.batch_id == batch.id).all()
        assert len(candidates) == 3
        assert [row.rank for row in candidates] == [1, 2, 3]
        # 候选人分数随覆盖率递减，Top-K 排序有东西可排
        assert (candidates[0].overall_score > candidates[1].overall_score
                > candidates[2].overall_score)
        # 候选简历挂在组织名下，不属于任何个人用户（跨租户隔离的事实前提）
        for candidate in candidates:
            profile = db.query(models.ResumeProfile).get(candidate.resume_profile_id)
            assert profile.organization_id == organization.id
            assert profile.owner_user_id is None
    finally:
        db.close()


def test_ownership_scoping_hides_the_other_tenant():
    """跨租户命中的是 app/ownership.py 的 404（记录不存在），不是 403。"""
    from fastapi import HTTPException

    from app.auth import actor_for_user
    from app.ownership import require_org

    db = _database()
    try:
        seed.apply_seed(db)
        primary_org = models.Organization(name=seed.PRIMARY_ORGANIZATION, status="active")
        db.add(primary_org)
        db.flush()
        demo_hr = db.query(models.AppUser).filter(
            models.AppUser.username == "demo-hr").one()
        db.add(models.OrganizationMember(organization_id=primary_org.id, user_id=demo_hr.id,
                                         role="hr", status="active"))
        db.commit()

        actor = actor_for_user(db, demo_hr)
        batch = db.query(models.RecruitmentBatch).one()
        assert actor.organization_id == primary_org.id != batch.organization_id
        try:
            require_org(batch, actor)
        except HTTPException as exc:
            assert exc.status_code == 404
        else:
            raise AssertionError("跨租户访问必须被拒绝")
    finally:
        db.close()
