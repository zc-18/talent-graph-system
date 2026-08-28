"""Idempotently seed role-scoped showcase records outside evaluation fixtures.

The default mode is read-only planning.  Pass ``--apply`` to write.  Every
created record uses a stable showcase key, anonymized code, or ``showcase``
source marker.  Accuracy evaluation reads versioned fixture files and never
queries these workflow tables.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import models  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.services import matching, role_contract  # noqa: E402
from data import seed_demo_accounts_feedback as identity_seed  # noqa: E402


BATCH_SPECS = (
    ("showcase-batch-01", "云平台研发人才池", "completed", 0),
    ("showcase-batch-02", "智能应用工程人才池", "completed", 0),
    ("showcase-batch-03", "数据工程人才池", "partial_failed", 2),
    ("showcase-batch-04", "安全与测试人才池", "partial_failed", 1),
)
FEATURES = ("login", "job_view", "discovery", "match", "batch_resume", "team_review")


def _published_jobs(db) -> list[models.Job]:
    jobs = db.query(models.Job).filter(models.Job.status == "published").order_by(
        models.Job.confidence.desc(), models.Job.id).limit(4).all()
    if not jobs:
        raise RuntimeError("至少需要 1 个已发布岗位才能初始化展示记录")
    return jobs


def plan(db) -> dict:
    jobs = _published_jobs(db)
    return {
        "mode": "dry-run",
        "writes": False,
        "isolation": {
            "source_type": "showcase",
            "candidate_prefix": "SC-",
            "evaluation_included": False,
            "pii_stored": False,
        },
        "users": [item[0] for item in identity_seed.USERS],
        "recruitment_batches": 4,
        "candidates": 60,
        "personal_match_runs": 6,
        "usage_days": 30,
        "target_jobs": [job.name for job in jobs],
    }


def _ensure_team(db, organization, users, target_job):
    name = "智岗人才评审组"
    team = db.query(models.Team).filter(
        models.Team.organization_id == organization.id,
        models.Team.name == name).first()
    if team is None:
        team = models.Team(
            name=name, description="招聘批次 Top-K 入组与团队覆盖变化记录",
            organization_id=organization.id, created_by=users["hr"].id,
            target_job_id=target_job.id)
        db.add(team)
        db.flush()
    return team


def _ensure_profile(db, *, code: str, owner_id: int | None,
                    organization_id: int | None, index: int, job) -> models.ResumeProfile:
    profile = db.query(models.ResumeProfile).filter(
        models.ResumeProfile.code == code,
        models.ResumeProfile.owner_user_id == owner_id,
        models.ResumeProfile.organization_id == organization_id).first()
    skills = ["Python", "SQL", "Docker", "数据分析", "系统设计"]
    rotated = skills[index % len(skills):] + skills[:index % len(skills)]
    if profile is None:
        profile = models.ResumeProfile(
            owner_user_id=owner_id, organization_id=organization_id,
            code=code, source_type="showcase", skills=rotated[:3 + index % 3],
            skill_levels={name: ("proficient" if offset < 2 else "familiar")
                          for offset, name in enumerate(rotated[:3 + index % 3])},
            years_experience=float(1 + index % 8),
            education=("本科" if index % 4 else "硕士"), authorized=True,
            retention_expires_at=datetime.utcnow() + timedelta(days=180),
        )
        db.add(profile)
        db.flush()
    return profile


def _ensure_batches(db, organization, users, jobs, team) -> tuple[list, int]:
    batches = []
    selections = 0
    for batch_index, (key, name, status, failures) in enumerate(BATCH_SPECS):
        job = jobs[batch_index % len(jobs)]
        batch = db.query(models.RecruitmentBatch).filter(
            models.RecruitmentBatch.organization_id == organization.id,
            models.RecruitmentBatch.idempotency_key == key).first()
        if batch is None:
            contract = role_contract.build_contract_from_job(db, job)
            version = db.query(models.JobVersion).filter(
                models.JobVersion.job_id == job.id,
                models.JobVersion.version == (job.version or 1)).first()
            batch = models.RecruitmentBatch(
                organization_id=organization.id, created_by=users["hr"].id,
                name=name, target_job_id=job.id,
                target_job_version_id=version.id if version else None,
                target_job_version=job.version or 1,
                contract_snapshot=contract, status=status,
                total_count=15, processed_count=15,
                succeeded_count=15 - failures, failed_count=failures,
                idempotency_key=key,
                created_at=datetime.utcnow() - timedelta(days=24 - batch_index * 6),
            )
            db.add(batch)
            db.flush()
        batches.append(batch)

        candidates = []
        for candidate_index in range(15):
            code = f"SC-{batch_index + 1:02d}-{candidate_index + 1:03d}"
            file_hash = hashlib.sha256(code.encode("ascii")).hexdigest()
            candidate = db.query(models.BatchCandidate).filter(
                models.BatchCandidate.batch_id == batch.id,
                models.BatchCandidate.file_hash == file_hash).first()
            failed = candidate_index >= 15 - failures
            profile = None if failed else _ensure_profile(
                db, code=code, owner_id=None, organization_id=organization.id,
                index=batch_index * 15 + candidate_index, job=job)
            score = None if failed else round(0.96 - candidate_index * 0.018
                                              - batch_index * 0.012, 4)
            if candidate is None:
                candidate = models.BatchCandidate(
                    batch_id=batch.id, resume_profile_id=profile.id if profile else None,
                    file_hash=file_hash, display_code=code,
                    parse_status="failed" if failed else "completed",
                    error_code="UNSUPPORTED_FORMAT" if failed else None,
                    error_detail="文件格式无法解析，未保留原始文件" if failed else None,
                    overall_score=score,
                    dimension_scores=(None if failed else {
                        "skills": round(score + .01, 4),
                        "experience": round(max(0.0, score - .04), 4),
                        "growth": round(max(0.0, score - .02), 4),
                    }),
                    result_snapshot=(None if failed else {
                        "source": "showcase", "job_id": job.id,
                        "top_gaps": ["领域知识", "架构实践"]}),
                    rank=None,
                    note="展示工作流记录，不参与评测",
                    created_at=batch.created_at + timedelta(minutes=candidate_index),
                )
                db.add(candidate)
                db.flush()
            candidates.append(candidate)

        ranked = sorted((item for item in candidates if item.overall_score is not None),
                        key=lambda item: (-item.overall_score, item.id))
        for rank, candidate in enumerate(ranked, 1):
            candidate.rank = rank
        for candidate in ranked[:5]:
            selection = db.query(models.CandidateSelection).filter(
                models.CandidateSelection.batch_candidate_id == candidate.id,
                models.CandidateSelection.team_id == team.id).first()
            before = round(0.48 + selections * .012, 4)
            after = round(min(.96, before + .018), 4)
            if selection is None:
                selection = models.CandidateSelection(
                    batch_candidate_id=candidate.id, team_id=team.id,
                    selected_by=users["hr"].id,
                    before_coverage=before, after_coverage=after,
                    created_at=batch.created_at + timedelta(days=1))
                db.add(selection)
                db.flush()
            event = db.query(models.TeamEvent).filter(
                models.TeamEvent.team_id == team.id,
                models.TeamEvent.action == "candidate_selected",
                models.TeamEvent.member_id == candidate.id).first()
            if event is None:
                db.add(models.TeamEvent(
                    team_id=team.id, organization_id=organization.id,
                    actor_user_id=users["hr"].id, action="candidate_selected",
                    member_id=candidate.id,
                    details={"source": "showcase", "candidate_code": candidate.display_code,
                             "batch_id": batch.id, "rank": candidate.rank},
                    before_snapshot={"coverage_rate": selection.before_coverage},
                    after_snapshot={"coverage_rate": selection.after_coverage},
                    created_at=selection.created_at))
            selections += 1
    return batches, selections


def _persona_skills(caps: list[dict], coverage: float) -> tuple[list[str], dict]:
    """按覆盖率从岗位真实能力簇里抽取一份"简历技能集"。

    这些是演示用人物设定，但**匹配过程本身是真的**：技能取自该岗位经交叉验证的
    能力簇，再交给 services.matching 真实打分，而不是把分数写死。coverage 控制
    覆盖比例，让 6 条历史记录自然拉开档次，而不是等差数列凑出来的假分数。
    """
    required = [c for c in caps if c.get("importance") == "required"]
    bonus = [c for c in caps if c.get("importance") != "required"]
    # 至少留 1 个必备缺口：全覆盖会让 missing_required 为空、学习路径也为空，
    # 而文件末尾的验收断言要求每条记录都有 learning_path。
    # 注意不能写成 max(1, ...)：契约只有 1 个必备项时那会把它也占掉，缺口反而归零。
    take = max(0, min(len(required) - 1, round(len(required) * coverage)))
    picked = [c.get("name") for c in required[:take] if c.get("name")]
    # 带一两个加分项，模拟真实简历总会有些岗位没要求的技能
    picked += [c.get("name") for c in bonus[:max(0, round(len(bonus) * coverage * .5))]
               if c.get("name")]
    levels = {name: ("proficient" if i % 3 else "familiar")
              for i, name in enumerate(picked)}
    return picked, levels


def _ensure_match_history(db, users, jobs) -> list[models.MatchRun]:
    """6 条个人匹配历史 —— 分数与缺口由**真实 matching 服务**算出。

    历史实现把 overall_score 写成 `.72 + index*.035` 的等差数列、缺口写成两个常量，
    而且量纲和键名都和真实服务对不上：真实 `services/matching.match()` 产出的是
    **0–100** 的 `overall_score`（matching.py:98 `round(100 * ...)`）、五档 `level`
    （_grade：高度匹配/较好匹配/基本匹配/存在差距/差距较大）、以及
    `missing_required: [{name, weight, category}]`；种子数据写的却是 0–1 小数、
    "良好/可提升"、`top_gaps: [{skill, gap}]`。三处全部错位，后果是
    `me.py:_match_dict` 读 `missing_required` 永远读到空，前端"0 个关键缺口"；
    而 `Math.round(0.895)` 又把评分显示成 "1"。

    改为走真实链路：真实 RoleContract → 真实 matching.match() → 真实 build_learning_path()。
    """
    owner = users["user"]
    existing = {
        (row.result_snapshot or {}).get("showcase_key"): row
        for row in db.query(models.MatchRun).filter(
            models.MatchRun.owner_user_id == owner.id).all()
        if isinstance(row.result_snapshot, dict)
    }
    runs = []
    # 覆盖率梯度：让 6 条记录跨越 _grade 的多个档位，而不是全挤在一档
    coverages = [.35, .48, .60, .72, .85, .95]
    for index in range(6):
        key = f"showcase-match-{index + 1:02d}"
        job = jobs[index % len(jobs)]
        run = existing.get(key)
        if run is None:
            contract = role_contract.build_contract_from_job(db, job)
            caps = role_contract.matching_capabilities(contract)
            skills, levels = _persona_skills(caps, coverages[index])
            profile = _ensure_profile(
                db, code=f"SC-U-{index + 1:03d}", owner_id=owner.id,
                organization_id=None, index=index + 7, job=job)
            # 人物设定的技能集要与实际参与匹配的一致，否则简历页和历史页对不上
            profile.skills = skills
            profile.skill_levels = levels
            # use_semantic=False：种子脚本必须可重放，语义对齐要走 BGE 嵌入服务，
            # 联不上网就会让结果随环境漂移。关掉后仍是真实打分，只是不做同义对齐。
            result = matching.match(caps, skills, levels, use_semantic=False)
            learning_path = matching.build_learning_path(result["missing_required"])
            version = db.query(models.JobVersion).filter(
                models.JobVersion.job_id == job.id,
                models.JobVersion.version == (job.version or 1)).first()
            run = models.MatchRun(
                owner_user_id=owner.id, organization_id=None,
                resume_profile_id=profile.id, job_id=job.id,
                job_version_id=version.id if version else None,
                job_version=job.version or 1, status="completed",
                contract_snapshot=contract,
                result_snapshot={"showcase_key": key, "source": "showcase", **result},
                learning_path=learning_path,
                created_at=datetime.utcnow() - timedelta(days=25 - index * 4),
            )
            db.add(run)
            db.flush()
        runs.append(run)
    return runs


def _ensure_admin_workflows(db, organization, users, jobs) -> dict:
    candidate_statuses = ("submitted", "approved", "rejected")
    candidates = []
    for index, status in enumerate(candidate_statuses):
        key = f"showcase-discovery-{index + 1:02d}"
        discovery = db.query(models.DiscoveryRun).filter(
            models.DiscoveryRun.owner_user_id == users["admin"].id,
            models.DiscoveryRun.idempotency_key == key).first()
        if discovery is None:
            discovery = models.DiscoveryRun(
                owner_user_id=users["admin"].id, organization_id=organization.id,
                query=f"展示候选岗位 {index + 1}",
                conditions={"source": "showcase", "track": "software"},
                evidence_snapshot={"employer_count": 3, "jd_count": 8},
                signal_snapshot={"novelty": round(.82 - index * .08, 2)},
                conclusion="candidate", idempotency_key=key,
                created_at=datetime.utcnow() - timedelta(days=12 - index * 3))
            db.add(discovery)
            db.flush()
        candidate = db.query(models.JobCandidate).filter(
            models.JobCandidate.discovery_run_id == discovery.id).first()
        if candidate is None:
            candidate = models.JobCandidate(
                discovery_run_id=discovery.id, owner_user_id=users["admin"].id,
                organization_id=organization.id, status=status,
                current_revision=1, created_at=discovery.created_at,
                updated_at=discovery.created_at + timedelta(hours=2))
            db.add(candidate)
            db.flush()
        revision = db.query(models.JobCandidateRevision).filter(
            models.JobCandidateRevision.candidate_id == candidate.id,
            models.JobCandidateRevision.revision == 1).first()
        if revision is None:
            db.add(models.JobCandidateRevision(
                candidate_id=candidate.id, revision=1,
                definition={
                    "title": f"可信智能工程师 {index + 1}",
                    "category": "人工智能", "source": "showcase",
                    "summary": "由公开证据形成的候选岗位定义",
                    "capabilities": [],
                },
                change_note="展示候选审核流程", created_by=users["admin"].id,
                created_at=candidate.created_at))
        if status in {"approved", "rejected"} and not db.query(models.CandidateReview).filter(
                models.CandidateReview.candidate_id == candidate.id,
                models.CandidateReview.revision == 1).first():
            db.add(models.CandidateReview(
                candidate_id=candidate.id, revision=1,
                action="approve" if status == "approved" else "reject",
                comment="展示审核记录：证据与定义已复核",
                reviewer_id=users["admin"].id,
                created_at=candidate.updated_at))
        candidates.append(candidate)

    evolution_statuses = ("pending", "proposed", "approved")
    evolution_runs = []
    for index, status in enumerate(evolution_statuses):
        key = f"showcase-evolution-{index + 1:02d}"
        job = jobs[index % len(jobs)]
        run = db.query(models.EvolutionRun).filter(
            models.EvolutionRun.organization_id == organization.id,
            models.EvolutionRun.idempotency_key == key).first()
        if run is None:
            diff = [{
                "change_type": "add", "skill_name": "智能体工程实践",
                "reason": "新时间窗中达到多雇主证据门槛",
                "data_source": {"source": "showcase", "jd_count": 8,
                                "employer_count": 3},
            }] if status != "pending" else []
            run = models.EvolutionRun(
                job_id=job.id, organization_id=organization.id,
                created_by=users["admin"].id, from_version=job.version or 1,
                proposed_version=(job.version or 1) + 1, status=status,
                idempotency_key=key,
                input_snapshot={"source": "showcase", "jd_count": 8},
                proposed_snapshot={"source": "showcase", "capabilities": diff},
                diff=diff, stats={"changes": len(diff), "added": len(diff),
                                  "deleted": 0, "modified": 0},
                created_at=datetime.utcnow() - timedelta(days=9 - index * 3),
                updated_at=datetime.utcnow() - timedelta(days=9 - index * 3))
            db.add(run)
            db.flush()
        if status in {"proposed", "approved"} and not db.query(
                models.EvolutionReview).filter(
                models.EvolutionReview.evolution_run_id == run.id).first():
            db.add(models.EvolutionReview(
                evolution_run_id=run.id,
                action="approve" if status == "approved" else "propose",
                comment="展示演化审核记录",
                reviewer_id=users["admin"].id, created_at=run.updated_at))
        evolution_runs.append(run)
    return {"candidates": candidates, "evolution_runs": evolution_runs}


def _showcase_anchor(db, organization, admin) -> datetime:
    marker = db.query(models.AuditLog).filter(
        models.AuditLog.action == "showcase.seed.anchor",
        models.AuditLog.target_type == "showcase",
        models.AuditLog.target_id == "v1").first()
    if marker:
        return marker.created_at.replace(hour=12, minute=0, second=0, microsecond=0)
    anchor = datetime.utcnow().replace(hour=12, minute=0, second=0, microsecond=0)
    db.add(models.AuditLog(
        actor_user_id=admin.id, organization_id=organization.id,
        action="showcase.seed.anchor", target_type="showcase", target_id="v1",
        result="success", summary={"source": "showcase", "anchor": anchor.isoformat()},
        created_at=anchor))
    db.flush()
    return anchor


def _ensure_audit_and_usage(db, organization, users) -> tuple[int, int]:
    anchor = _showcase_anchor(db, organization, users["admin"])
    audit_specs = (
        ("user.review", "app_user", str(users["user"].id)),
        ("organization.review", "organization", str(organization.id)),
        ("candidate.review", "job_candidate", "showcase"),
        ("evolution.review", "evolution_run", "showcase"),
        ("feedback.review", "feedback_ticket", "showcase"),
        ("recruitment.rank", "recruitment_batch", "showcase"),
        ("team.coverage", "team", "showcase"),
    )
    for index, (action, target_type, target_id) in enumerate(audit_specs):
        scoped_id = f"{target_id}:{index}"
        if not db.query(models.AuditLog).filter(
                models.AuditLog.action == action,
                models.AuditLog.target_type == target_type,
                models.AuditLog.target_id == scoped_id).first():
            db.add(models.AuditLog(
                actor_user_id=users["admin"].id,
                organization_id=organization.id, action=action,
                target_type=target_type, target_id=scoped_id, result="success",
                summary={"source": "showcase", "evaluation_included": False},
                created_at=anchor - timedelta(days=index + 1)))

    usage_count = 0
    role_users = [users["user"], users["hr"], users["admin"]]
    for day_index in range(30):
        created_at = anchor - timedelta(days=29 - day_index)
        for feature_index, feature in enumerate(FEATURES):
            user = role_users[(day_index + feature_index) % len(role_users)]
            event_time = created_at + timedelta(minutes=feature_index)
            event = db.query(models.UsageEvent).filter(
                models.UsageEvent.user_id == user.id,
                models.UsageEvent.organization_id == organization.id,
                models.UsageEvent.feature == feature,
                models.UsageEvent.created_at == event_time).first()
            if event is None:
                db.add(models.UsageEvent(
                    user_id=user.id, organization_id=organization.id,
                    feature=feature, duration_ms=80 + (day_index * 17
                                                       + feature_index * 31) % 720,
                    success=not (day_index in {8, 21} and feature == "batch_resume"),
                    created_at=event_time))
            usage_count += 1
    return len(audit_specs) + 1, usage_count


def apply_showcase(db, *, include_feedback: bool = True) -> dict:
    jobs = _published_jobs(db)
    users = identity_seed._ensure_users(db)
    organization = identity_seed._ensure_organization(db, users)
    if include_feedback:
        references = identity_seed._evidence_references(db)
        identity_seed._ensure_baseline_versions(db, references, users["admin"])
        db.flush()
        identity_seed._ensure_feedback(
            db, users, organization, identity_seed._evidence_references(db))
    team = _ensure_team(db, organization, users, jobs[0])
    batches, selections = _ensure_batches(db, organization, users, jobs, team)
    matches = _ensure_match_history(db, users, jobs)
    workflows = _ensure_admin_workflows(db, organization, users, jobs)
    audit_count, usage_count = _ensure_audit_and_usage(db, organization, users)
    db.commit()
    return verify(db, expected_feedback=include_feedback) | {
        "mode": "applied", "writes": True,
        "selection_count": selections,
        "audit_seed_count": audit_count,
        "usage_seed_count": usage_count,
        "batch_ids": [row.id for row in batches],
        "match_run_ids": [row.id for row in matches],
        "candidate_review_count": len(workflows["candidates"]),
        "evolution_run_count": len(workflows["evolution_runs"]),
    }


def verify(db, *, expected_feedback: bool = True) -> dict:
    users = {row.username: row for row in db.query(models.AppUser).filter(
        models.AppUser.username.in_([item[0] for item in identity_seed.USERS])).all()}
    organization = db.query(models.Organization).filter(
        models.Organization.name == identity_seed.ORGANIZATION).one()
    batches = db.query(models.RecruitmentBatch).filter(
        models.RecruitmentBatch.organization_id == organization.id,
        models.RecruitmentBatch.idempotency_key.in_([item[0] for item in BATCH_SPECS])).all()
    batch_ids = [row.id for row in batches]
    candidates = db.query(models.BatchCandidate).filter(
        models.BatchCandidate.batch_id.in_(batch_ids)).all() if batch_ids else []
    assert len(users) == 3
    assert len(batches) == 4
    assert len(candidates) == 60
    assert all(row.display_code.startswith("SC-") for row in candidates)
    assert any(row.parse_status == "failed" for row in candidates)
    assert all(not hasattr(row, "name") and not hasattr(row, "phone")
               for row in candidates)
    assert db.query(models.CandidateSelection).filter(
        models.CandidateSelection.batch_candidate_id.in_(
            [row.id for row in candidates])).count() == 20
    match_runs = [row for row in db.query(models.MatchRun).filter(
        models.MatchRun.owner_user_id == users["demo-user"].id).all()
        if isinstance(row.result_snapshot, dict)
        and str(row.result_snapshot.get("showcase_key", "")).startswith("showcase-match-")]
    assert len(match_runs) == 6 and all(row.learning_path for row in match_runs)
    assert db.query(models.UsageEvent).filter(
        models.UsageEvent.organization_id == organization.id).count() >= 180
    if expected_feedback:
        assert all(identity_seed._existing_ticket(db, key)
                   for key, _, _ in identity_seed.FEEDBACK_SPECS)
    return {
        "users": sorted(users), "organization_id": organization.id,
        "recruitment_batches": len(batches), "candidates": len(candidates),
        "failed_candidates": sum(row.parse_status == "failed" for row in candidates),
        "top_k_selections": 20, "personal_match_runs": len(match_runs),
        "evaluation_included": False, "pii_stored": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="write changes; default is dry-run")
    parser.add_argument("--skip-feedback", action="store_true",
                        help="skip feedback records when validating a minimal local database")
    args = parser.parse_args()
    db = SessionLocal()
    try:
        result = (apply_showcase(db, include_feedback=not args.skip_feedback)
                  if args.apply else plan(db))
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
