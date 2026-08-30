"""第六轮：把示例账号从 3 个扩到 9 个，并给每个账号铺上可演示的业务数据。

沿用 ``data/seed_demo_accounts_feedback.py`` 的三条约定：**默认 dry-run**（必须显式
``--apply`` 才写库）、**幂等**（已存在就修复而不是重复创建）、**结尾 ``_verify()`` 断言**。

原有 3 个账号（demo-user / demo-hr / demo-admin）的用户名、密码、角色、组织关系
一律不动，本脚本只给它们补上昵称和预置头像（两列都是本轮新增的 NULL 列）。

新增 6 个账号，覆盖面刻意错开：

* 5 个个人用户，分属算法 / 数据 / 后端 / 前端 / 应届五个方向，匹配覆盖率从 0.9 降到
  0.25，因此匹配总分、能力缺口、学习路径、反馈工单状态各不相同；每人都有脱敏简历画像
  （ResumeProfile）和 1~3 条匹配历史（MatchRun，按天回填 created_at）。
* 1 个 HR，隶属**第二个组织**「岚汐云智科技」，并在该组织下建一个招聘批次和 3 名候选人
  —— 这样 demo-hr（智岗演示组织）去访问这些行时会命中 app/ownership.py 的
  「跨租户返回 404 而不是 403」逻辑，隔离性可以当场演示。

匹配分不是编出来的：脚本走 role_contract.build_contract_from_job → matching.match
真实算，简历技能取自岗位能力契约的前若干个必备簇，覆盖率决定取多少。

用法（backend/ 目录下）：
    uv run python -X utf8 data/seed_demo_accounts_r6.py            # dry-run，打印计划
    uv run python -X utf8 data/seed_demo_accounts_r6.py --apply    # 写库
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import json
import math
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import models  # noqa: E402
from app.auth import actor_for_user, hash_password, verify_password  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.services import feedback, matching, role_contract  # noqa: E402


PRIMARY_ORGANIZATION = "智岗演示组织"          # 原有 HR 的组织，不动
SECONDARY_ORGANIZATION = "岚汐云智科技"        # 新增 HR 的组织，用于跨租户隔离演示
SECONDARY_BATCH_KEY = "r6-seed-lanxi-cloud"    # RecruitmentBatch.idempotency_key（唯一约束）

# 原有账号：只补昵称/头像，其余保持原样
EXISTING_PROFILES = (
    ("demo-user", "求职小林", "/avatars/a01.webp"),
    ("demo-hr", "演示组织 HR", "/avatars/a02.webp"),
    ("demo-admin", "平台管理员", "/avatars/a03.webp"),
)

# 新增个人用户：coverage 决定简历覆盖岗位必备能力的比例，直接决定匹配分高低
PERSONAS = (
    {
        "username": "demo-user-ai", "password": "DemoAi123!", "nickname": "算法工程师·林知遥",
        "avatar_url": "/avatars/a04.webp",
        "keywords": ("算法", "人工智能", "机器学习", "大模型", "深度学习", "AI"),
        "coverage": 0.92, "years": 4.5, "education": "硕士", "level": "proficient",
        "runs": 3, "feedback_status": "approved", "feedback_title": "能力项证据补充",
    },
    {
        "username": "demo-user-data", "password": "DemoData123!", "nickname": "数据工程师·沈叙白",
        "keywords": ("数据", "大数据", "数仓", "ETL", "分析"),
        "avatar_url": "/avatars/a05.webp",
        "coverage": 0.7, "years": 3.0, "education": "本科", "level": "proficient",
        "runs": 2, "feedback_status": "triaged", "feedback_title": "技能权重复核",
    },
    {
        "username": "demo-user-backend", "password": "DemoBack123!", "nickname": "后端工程师·周砚清",
        "keywords": ("后端", "服务端", "Java", "云", "架构", "开发"),
        "avatar_url": "/avatars/a06.webp",
        "coverage": 0.55, "years": 2.5, "education": "本科", "level": "familiar",
        "runs": 3, "feedback_status": "submitted", "feedback_title": "岗位能力项表述建议",
    },
    {
        "username": "demo-user-frontend", "password": "DemoFront123!", "nickname": "前端工程师·许南星",
        "keywords": ("前端", "客户端", "Web", "移动", "视觉"),
        "avatar_url": "/avatars/a07.webp",
        "coverage": 0.35, "years": 1.5, "education": "本科", "level": "familiar",
        "runs": 2, "feedback_status": "rejected", "feedback_title": "能力项适用性异议",
    },
    {
        "username": "demo-user-fresh", "password": "DemoFresh123!", "nickname": "应届生·柯一鸣",
        "keywords": ("产品", "运营", "测试", "质量", "支持"),
        "avatar_url": "/avatars/a08.webp",
        "coverage": 0.2, "years": 0.0, "education": "本科", "level": "familiar",
        "runs": 1, "feedback_status": None, "feedback_title": None,
    },
)

SECONDARY_HR = {
    "username": "demo-hr-cloud", "password": "DemoHrCloud123!",
    "nickname": "岚汐云智·招聘负责人", "avatar_url": "/avatars/a09.webp",
}

SECONDARY_CANDIDATES = (("LX-001", 0.8), ("LX-002", 0.55), ("LX-003", 0.3))

FEEDBACK_MARKER = "[R6演示反馈:{key}]"
RESUME_CODE = "R6-{username}"


# ----------------------------- 选岗位 / 造简历 -----------------------------

def _published_jobs(db) -> list[models.Job]:
    return (db.query(models.Job).filter(models.Job.status == "published")
            .order_by(models.Job.id).all())


def _assign_jobs(jobs: list[models.Job]) -> dict[str, list[models.Job]]:
    """给每个 persona 挑主岗位 + 若干对比岗位，尽量避开彼此重复。"""
    if not jobs:
        raise RuntimeError("库里没有 published 岗位，请先跑 data/run_pipeline.py")
    taken: set[int] = set()
    assignment: dict[str, list[models.Job]] = {}
    for persona in PERSONAS:
        haystack = [(job, f"{job.name} {job.category or ''} {job.track or ''}") for job in jobs]
        matched = [job for job, text in haystack
                   if any(word.lower() in text.lower() for word in persona["keywords"])]
        pool = [job for job in matched if job.id not in taken] or \
               [job for job in jobs if job.id not in taken] or jobs
        picked = pool[:persona["runs"]]
        while len(picked) < persona["runs"]:
            picked.append(jobs[len(picked) % len(jobs)])
        taken.update(job.id for job in picked)
        assignment[persona["username"]] = picked
    return assignment


def _contract(db, job: models.Job) -> dict:
    return role_contract.build_contract_from_job(db, job)


def _resume_skills(contract: dict, coverage: float) -> tuple[list[str], dict[str, str]]:
    clusters = [c for c in contract.get("clusters", []) if c.get("importance") == "required"] \
        or contract.get("clusters", [])
    ordered = sorted(clusters, key=lambda c: (-(c.get("weight") or 0), c.get("name") or ""))
    keep = max(1, math.ceil(len(ordered) * coverage)) if ordered else 0
    names = [c["name"] for c in ordered[:keep]]
    return names, {name: "proficient" for name in names}


# ----------------------------- 账号 / 组织 -----------------------------

def _has_seed_marker(db, user: models.AppUser) -> bool:
    """Distinguish a previously seeded account from a first-run username collision.

    Password hashes are intentionally not seed ownership markers because users may change their
    passwords after the first production run.  R6-owned business rows provide stable markers.
    """
    persona_names = {persona["username"] for persona in PERSONAS}
    if user.username in persona_names:
        code = RESUME_CODE.format(username=user.username)
        return db.query(models.ResumeProfile).filter(
            models.ResumeProfile.owner_user_id == user.id,
            models.ResumeProfile.code == code).first() is not None
    if user.username == SECONDARY_HR["username"]:
        return db.query(models.OrganizationMember).join(
            models.Organization,
            models.Organization.id == models.OrganizationMember.organization_id).filter(
            models.OrganizationMember.user_id == user.id,
            models.Organization.name == SECONDARY_ORGANIZATION).first() is not None
    return False


def _ensure_user(db, username: str, password: str, role: str, *,
                 nickname: str | None = None, avatar_url: str | None = None) -> models.AppUser:
    user = db.query(models.AppUser).filter(models.AppUser.username == username).first()
    created = user is None
    if created:
        user = models.AppUser(username=username, password_hash=hash_password(password),
                              role=role, status="active")
        db.add(user)
        db.flush()
    else:
        # First-run username collisions must prove the configured demo credentials.  Once this
        # script's durable business marker exists, however, password/profile changes belong to
        # the user and a rerun must not reset or reject them.
        seeded_before = _has_seed_marker(db, user)
        identity_matches = user.role == role and user.status == "active"
        initial_credentials_match = verify_password(password, user.password_hash)
        if not identity_matches or (not seeded_before and not initial_credentials_match):
            raise RuntimeError(f"{username} 已存在但认证属性与 R6 示例账号不一致；拒绝覆盖")
    # 新建账号落默认资料；已存在账号只修业务演示数据，不重置密码、角色、状态或用户
    # 自己改过的昵称/头像。这样脚本重复执行不会夺走账号控制权或覆盖个性化资料。
    if user.nickname is None and nickname:
        user.nickname = nickname
    if user.avatar_url is None and avatar_url:
        user.avatar_url = avatar_url
    return user


def _decorate_existing(db) -> list[str]:
    """给原有 3 个账号补昵称/头像；不存在就跳过（本脚本不负责创建它们）。"""
    touched = []
    for username, nickname, avatar_url in EXISTING_PROFILES:
        user = db.query(models.AppUser).filter(models.AppUser.username == username).first()
        if user is None:
            continue
        user.nickname = user.nickname or nickname
        user.avatar_url = user.avatar_url or avatar_url
        touched.append(username)
    return touched


def _ensure_organization(db, name: str, creator: models.AppUser,
                         member: models.AppUser) -> models.Organization:
    organization = db.query(models.Organization).filter(
        models.Organization.name == name).first()
    if organization is None:
        organization = models.Organization(name=name, status="active", created_by=creator.id)
        db.add(organization)
        db.flush()
    else:
        organization.status = "active"
    row = db.query(models.OrganizationMember).filter(
        models.OrganizationMember.organization_id == organization.id,
        models.OrganizationMember.user_id == member.id).first()
    if row is None:
        db.add(models.OrganizationMember(organization_id=organization.id, user_id=member.id,
                                         role="hr", status="active"))
    else:
        row.role, row.status = "hr", "active"
    return organization


# ----------------------------- 个人业务数据 -----------------------------

def _ensure_resume_profile(db, user: models.AppUser, skills: list[str],
                           levels: dict[str, str], persona: dict) -> models.ResumeProfile:
    code = RESUME_CODE.format(username=user.username)
    row = db.query(models.ResumeProfile).filter(
        models.ResumeProfile.owner_user_id == user.id,
        models.ResumeProfile.code == code).first()
    if row is None:
        row = models.ResumeProfile(owner_user_id=user.id, organization_id=None, code=code,
                                   source_type="upload")
        db.add(row)
    row.skills = skills
    row.skill_levels = levels
    row.years_experience = persona["years"]
    row.education = persona["education"]
    row.authorized = True
    row.retention_expires_at = datetime.utcnow() + timedelta(days=180)
    db.flush()
    return row


def _ensure_match_run(db, user: models.AppUser, profile: models.ResumeProfile,
                      job: models.Job, contract: dict, skills: list[str],
                      levels: dict[str, str], *, days_ago: int) -> models.MatchRun:
    """(owner_user_id, job_id) 作为自然键：同一人对同一岗位只保留一条演示记录。"""
    row = db.query(models.MatchRun).filter(
        models.MatchRun.owner_user_id == user.id,
        models.MatchRun.job_id == job.id).order_by(models.MatchRun.id).first()
    caps = role_contract.matching_capabilities(contract)
    result = matching.match(caps, skills, levels, use_semantic=False)
    learning_path = matching.build_learning_path(result["missing_required"], {})
    version_row = db.query(models.JobVersion).filter(
        models.JobVersion.job_id == job.id,
        models.JobVersion.version == (job.version or 1)).first()
    if row is None:
        row = models.MatchRun(owner_user_id=user.id, organization_id=None, job_id=job.id)
        db.add(row)
    row.resume_profile_id = profile.id
    row.job_version_id = version_row.id if version_row else None
    row.job_version = job.version or 1
    row.status = "completed"
    row.contract_snapshot = contract
    row.result_snapshot = {**result, "suggestions": {}}
    row.learning_path = learning_path
    row.created_at = datetime.utcnow() - timedelta(days=days_ago)
    db.flush()
    return row


def _existing_ticket(db, key: str) -> models.FeedbackTicket | None:
    revision = db.query(models.FeedbackRevision).filter(
        models.FeedbackRevision.content.like(FEEDBACK_MARKER.format(key=key) + "%")).first()
    return db.get(models.FeedbackTicket, revision.ticket_id) if revision else None


def _ensure_feedback(db, user: models.AppUser, admin: models.AppUser, job: models.Job,
                     contract: dict, persona: dict) -> models.FeedbackTicket | None:
    target_status = persona["feedback_status"]
    if not target_status:
        return None
    key = persona["username"]
    existing = _existing_ticket(db, key)
    if existing:
        return existing
    clusters = contract.get("clusters", [])
    focus = clusters[0]["name"] if clusters else "核心能力"
    owner = actor_for_user(db, user)
    reviewer = actor_for_user(db, admin)
    marker = FEEDBACK_MARKER.format(key=key)
    ticket = feedback.create_ticket(
        db, owner, target_type="job", target_id=str(job.id), category="evidence_review",
        content=f"{marker} {persona['feedback_title']}：{job.name} / {focus}",
        evidence=[{"job_id": job.id, "job_name": job.name, "capability": focus,
                   "job_version": job.version or 1}])
    if target_status in {"triaged", "approved", "rejected"}:
        feedback.transition(db, ticket, reviewer, action="triage", comment="演示分诊：引用可核对")
    if target_status == "approved":
        feedback.transition(db, ticket, reviewer, action="approve", comment="演示批准：建议成立")
    if target_status == "rejected":
        feedback.transition(db, ticket, reviewer, action="reject", comment="演示驳回：与当前版本不符")
    db.flush()
    return ticket


# ----------------------------- 第二组织的 HR 侧数据 -----------------------------

def _ensure_secondary_batch(db, hr: models.AppUser, organization: models.Organization,
                            job: models.Job, contract: dict) -> models.RecruitmentBatch:
    batch = db.query(models.RecruitmentBatch).filter(
        models.RecruitmentBatch.organization_id == organization.id,
        models.RecruitmentBatch.idempotency_key == SECONDARY_BATCH_KEY).first()
    if batch is None:
        batch = models.RecruitmentBatch(
            organization_id=organization.id, created_by=hr.id,
            name="岚汐云智 2026 秋招批次", target_job_id=job.id,
            target_job_version=job.version or 1, idempotency_key=SECONDARY_BATCH_KEY)
        db.add(batch)
        db.flush()
    batch.target_job_id = job.id
    batch.target_job_version = job.version or 1
    batch.contract_snapshot = contract
    batch.status = "completed"

    caps = role_contract.matching_capabilities(contract)
    for index, (code, coverage) in enumerate(SECONDARY_CANDIDATES):
        skills, levels = _resume_skills(contract, coverage)
        result = matching.match(caps, skills, levels, use_semantic=False)
        profile = db.query(models.ResumeProfile).filter(
            models.ResumeProfile.organization_id == organization.id,
            models.ResumeProfile.code == code).first()
        if profile is None:
            profile = models.ResumeProfile(organization_id=organization.id, owner_user_id=None,
                                           code=code, source_type="batch")
            db.add(profile)
        profile.skills = skills
        profile.skill_levels = levels
        profile.years_experience = float(3 - index)
        profile.education = "本科"
        profile.authorized = True
        profile.retention_expires_at = datetime.utcnow() + timedelta(days=90)
        db.flush()
        candidate = db.query(models.BatchCandidate).filter(
            models.BatchCandidate.batch_id == batch.id,
            models.BatchCandidate.file_hash == f"{SECONDARY_BATCH_KEY}-{code}").first()
        if candidate is None:
            candidate = models.BatchCandidate(batch_id=batch.id,
                                              file_hash=f"{SECONDARY_BATCH_KEY}-{code}")
            db.add(candidate)
        candidate.resume_profile_id = profile.id
        candidate.display_code = code
        candidate.parse_status = "succeeded"
        candidate.overall_score = result["overall_score"]
        candidate.dimension_scores = result.get("summary", {})
        candidate.result_snapshot = result
        candidate.rank = index + 1
        db.flush()
    batch.total_count = len(SECONDARY_CANDIDATES)
    batch.processed_count = len(SECONDARY_CANDIDATES)
    batch.succeeded_count = len(SECONDARY_CANDIDATES)
    batch.failed_count = 0
    db.flush()
    return batch


# ----------------------------- 计划 / 校验 -----------------------------

def _plan(db, assignment: dict[str, list[models.Job]]) -> dict:
    names = [persona["username"] for persona in PERSONAS] + [SECONDARY_HR["username"]]
    existing = {row.username for row in db.query(models.AppUser).filter(
        models.AppUser.username.in_(names)).all()}
    return {
        "mode": "dry-run",
        "writes": False,
        "existing_accounts_decorated": [item[0] for item in EXISTING_PROFILES],
        "new_accounts": [
            {"username": name, "action": "verify/update" if name in existing else "create"}
            for name in names],
        "organizations": [PRIMARY_ORGANIZATION, SECONDARY_ORGANIZATION],
        "assignment": {username: [job.name for job in jobs]
                       for username, jobs in assignment.items()},
        "secondary_batch": {"key": SECONDARY_BATCH_KEY,
                            "candidates": [code for code, _ in SECONDARY_CANDIDATES]},
    }


def _verify(db) -> dict:
    report: dict = {"accounts": {}, "organizations": {}, "isolation": {}}
    for username, _, _ in EXISTING_PROFILES:
        user = db.query(models.AppUser).filter(models.AppUser.username == username).first()
        if user is None:
            continue
        assert user.nickname and user.avatar_url, f"{username} 昵称/头像未落库"
        report["accounts"][username] = {"nickname": user.nickname, "avatar": user.avatar_url}

    scores: dict[str, float] = {}
    for persona in PERSONAS:
        user = db.query(models.AppUser).filter(
            models.AppUser.username == persona["username"]).one()
        assert user.role == "user" and user.status == "active"
        # Password ownership passes to the account after initial seeding.  Re-verifying the
        # built-in plaintext here would make a legitimate password change break every rerun.
        assert user.password_hash
        # 默认值可被账号本人后续修改；重复跑 seed 不应把自定义资料改回去。
        assert user.nickname and user.avatar_url
        profile = db.query(models.ResumeProfile).filter(
            models.ResumeProfile.owner_user_id == user.id,
            models.ResumeProfile.code == RESUME_CODE.format(username=user.username)).one()
        assert profile.skills, f"{user.username} 简历画像为空"
        runs = db.query(models.MatchRun).filter(
            models.MatchRun.owner_user_id == user.id).all()
        assert len(runs) >= persona["runs"], f"{user.username} 匹配历史不足"
        best = max((run.result_snapshot or {}).get("overall_score", 0) for run in runs)
        scores[user.username] = best
        ticket = _existing_ticket(db, persona["username"])
        if persona["feedback_status"]:
            assert ticket and ticket.status == persona["feedback_status"]
        report["accounts"][user.username] = {
            "nickname": user.nickname, "avatar": user.avatar_url,
            "match_runs": len(runs), "best_score": best,
            "resume_skills": len(profile.skills or []),
            "feedback_status": ticket.status if ticket else None,
        }
    # 覆盖率是刻意拉开的，分数不应该全挤在一起
    assert len(set(round(value, 1) for value in scores.values())) >= 3, \
        f"个人用户匹配分区分度不足: {scores}"

    hr = db.query(models.AppUser).filter(
        models.AppUser.username == SECONDARY_HR["username"]).one()
    assert hr.role == "hr" and hr.status == "active"
    assert hr.password_hash
    assert hr.nickname and hr.avatar_url
    primary = db.query(models.Organization).filter(
        models.Organization.name == PRIMARY_ORGANIZATION).first()
    secondary = db.query(models.Organization).filter(
        models.Organization.name == SECONDARY_ORGANIZATION,
        models.Organization.status == "active").one()
    assert primary is None or primary.id != secondary.id, "新 HR 必须落在另一个组织"
    assert db.query(models.OrganizationMember).filter(
        models.OrganizationMember.organization_id == secondary.id,
        models.OrganizationMember.user_id == hr.id,
        models.OrganizationMember.status == "active").count() == 1
    batch = db.query(models.RecruitmentBatch).filter(
        models.RecruitmentBatch.organization_id == secondary.id,
        models.RecruitmentBatch.idempotency_key == SECONDARY_BATCH_KEY).one()
    candidates = db.query(models.BatchCandidate).filter(
        models.BatchCandidate.batch_id == batch.id).all()
    assert len(candidates) == len(SECONDARY_CANDIDATES)
    report["accounts"][hr.username] = {"nickname": hr.nickname, "avatar": hr.avatar_url,
                                       "organization": secondary.name,
                                       "batch_candidates": len(candidates)}
    report["organizations"] = {PRIMARY_ORGANIZATION: primary.id if primary else None,
                               SECONDARY_ORGANIZATION: secondary.id}
    # 跨租户隔离的事实前提：这批行的 organization_id 一律不等于原组织
    if primary is not None:
        assert batch.organization_id != primary.id
        report["isolation"] = {"foreign_batch_id": batch.id,
                               "not_visible_to_organization_id": primary.id}
    report["total_accounts"] = db.query(models.AppUser).filter(
        models.AppUser.username.like("demo-%")).count()
    return report


def plan(db) -> dict:
    """dry-run 计划：只读，任何情况下都不写库。"""
    return _plan(db, _assign_jobs(_published_jobs(db)))


def apply_seed(db) -> dict:
    """写库主流程（幂等）。调用方负责提供 session；返回 ``_verify()`` 报告。"""
    jobs = _published_jobs(db)
    assignment = _assign_jobs(jobs)
    admin = db.query(models.AppUser).filter(models.AppUser.username == "demo-admin").first()
    if admin is None:
        raise RuntimeError("缺少 demo-admin，请先跑 data/seed_demo_accounts_feedback.py --apply")
    _decorate_existing(db)

    for persona in PERSONAS:
        user = _ensure_user(db, persona["username"], persona["password"], "user",
                            nickname=persona["nickname"], avatar_url=persona["avatar_url"])
        picked = assignment[persona["username"]]
        primary_contract = _contract(db, picked[0])
        skills, levels = _resume_skills(primary_contract, persona["coverage"])
        profile = _ensure_resume_profile(db, user, skills, levels, persona)
        for index, job in enumerate(picked):
            contract = primary_contract if index == 0 else _contract(db, job)
            _ensure_match_run(db, user, profile, job, contract, skills, levels,
                              days_ago=index * 6 + 1)
        _ensure_feedback(db, user, admin, picked[0], primary_contract, persona)

    hr = _ensure_user(db, SECONDARY_HR["username"], SECONDARY_HR["password"], "hr",
                      nickname=SECONDARY_HR["nickname"],
                      avatar_url=SECONDARY_HR["avatar_url"])
    secondary = _ensure_organization(db, SECONDARY_ORGANIZATION, admin, hr)
    target = assignment[PERSONAS[0]["username"]][0]
    _ensure_secondary_batch(db, hr, secondary, target, _contract(db, target))

    db.commit()
    return _verify(db)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="write changes; default is dry-run")
    args = parser.parse_args()
    db = SessionLocal()
    try:
        if not args.apply:
            print(json.dumps(plan(db), ensure_ascii=False, indent=2))
            return
        print(json.dumps({"mode": "applied", "writes": True, "verification": apply_seed(db)},
                         ensure_ascii=False, indent=2, default=str))
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
