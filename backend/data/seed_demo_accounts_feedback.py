"""Idempotently seed verified demo identities and evidence-linked feedback.

Dry-run is the default. Pass ``--apply`` to write to the configured database.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import models  # noqa: E402
from app.auth import actor_for_user, hash_password, verify_password  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.services import feedback, role_contract  # noqa: E402
from app.services.taxonomy import capability_cluster  # noqa: E402
from app.services.confidence_batch import run_confidence_recalculation  # noqa: E402


USERS = (
    ("demo-user", "DemoUser123!", "user"),
    ("demo-hr", "DemoHr123!", "hr"),
    ("demo-admin", "DemoAdmin123!", "admin"),
)
ORGANIZATION = "智岗演示组织"
FEEDBACK_SPECS = (
    ("demo-feedback-01", "submitted", "能力证据补充"),
    ("demo-feedback-02", "triaged", "岗位版本说明修订"),
    ("demo-feedback-03", "approved", "技能权重核验"),
    ("demo-feedback-04", "rejected", "不适用能力项复核"),
    ("demo-feedback-05", "applied", "证据链闭环应用"),
)
BEIJING = timezone(timedelta(hours=8), name="Asia/Shanghai")


def _evidence_references(db) -> list[dict]:
    rows = (db.query(models.Job, models.JobSkill, models.Skill,
                     models.Evidence, models.RawJD)
            .join(models.JobSkill, models.JobSkill.job_id == models.Job.id)
            .join(models.Skill, models.Skill.id == models.JobSkill.skill_id)
            .join(models.Evidence, models.Evidence.job_skill_id == models.JobSkill.id)
            .join(models.RawJD, models.RawJD.id == models.Evidence.raw_jd_id)
            .filter(models.Job.status == "published",
                    models.JobSkill.status == "active",
                    models.Evidence.source_type == "jd",
                    models.RawJD.is_duplicate == False,  # noqa: E712
                    models.RawJD.duplicate_of.is_(None),
                    models.RawJD.raw_text.isnot(None))
            .order_by(models.Job.id, models.JobSkill.id, models.Evidence.id).limit(500).all())
    versions: dict[int, models.JobVersion] = {}
    for version in db.query(models.JobVersion).filter(
            models.JobVersion.status == "published").order_by(
            models.JobVersion.job_id, models.JobVersion.version.desc()).all():
        versions.setdefault(version.job_id, version)
    candidates = []
    seen_evidence: set[int] = set()
    for job, relation, skill, evidence, raw_jd in rows:
        if evidence.id in seen_evidence or not (raw_jd.raw_text or "").strip():
            continue
        seen_evidence.add(evidence.id)
        version = versions.get(job.id)
        candidates.append({
            "job_id": job.id, "job_name": job.name,
            "job_version_id": version.id if version else None,
            "job_version": version.version if version else (job.version or 1),
            "job_skill_id": relation.id, "skill_id": skill.id, "skill_name": skill.name,
            "evidence_id": evidence.id, "raw_jd_id": raw_jd.id,
            "source_url": evidence.source_url or raw_jd.source_url,
            "snippet": (evidence.snippet or raw_jd.raw_text or "")[:300],
        })

    references: list[dict] = []
    selected_evidence: set[int] = set()

    def select_distinct(*fields: str) -> None:
        selected_values = {
            field: {reference[field] for reference in references}
            for field in fields
        }
        for reference in candidates:
            if len(references) >= len(FEEDBACK_SPECS):
                return
            if (reference["evidence_id"] in selected_evidence
                    or any(reference[field] in selected_values[field] for field in fields)):
                continue
            references.append(reference)
            selected_evidence.add(reference["evidence_id"])
            for field in fields:
                selected_values[field].add(reference[field])

    select_distinct("job_id", "skill_id")
    select_distinct("job_id")
    select_distinct("skill_id")
    select_distinct("job_skill_id")
    for reference in candidates:
        if len(references) >= len(FEEDBACK_SPECS):
            break
        if reference["evidence_id"] in selected_evidence:
            continue
        references.append(reference)
        selected_evidence.add(reference["evidence_id"])
    if len(references) < len(FEEDBACK_SPECS):
        raise RuntimeError(
            "至少需要 5 条关联 published Job/active JobSkill/Evidence/非重复 RawJD 的证据链")
    return references


def _plan(db, references: list[dict]) -> dict:
    existing_users = {row.username: row for row in db.query(models.AppUser).filter(
        models.AppUser.username.in_([item[0] for item in USERS])).all()}
    feedback_existing = {}
    for key, _, _ in FEEDBACK_SPECS:
        revision = db.query(models.FeedbackRevision).filter(
            models.FeedbackRevision.content.like(f"[演示反馈:{key}]%")).first()
        feedback_existing[key] = revision is not None
    return {
        "mode": "dry-run",
        "users": [{"username": username, "role": role,
                   "action": "verify/update" if username in existing_users else "create"}
                  for username, _, role in USERS],
        "organization": ORGANIZATION,
        "feedback": [{"key": key, "status": status,
                      "action": "keep" if feedback_existing[key] else "create",
                      "version_action": ("use_existing" if references[index]["job_version_id"]
                                         else "create_current_baseline"),
                      "reference": references[index]}
                     for index, (key, status, _) in enumerate(FEEDBACK_SPECS)],
        "writes": False,
    }


def _ensure_users(db) -> dict[str, models.AppUser]:
    users = {}
    for username, password, role in USERS:
        user = db.query(models.AppUser).filter(models.AppUser.username == username).first()
        if user is None:
            user = models.AppUser(username=username, password_hash=hash_password(password),
                                  role=role, status="active")
            db.add(user)
            db.flush()
        else:
            user.role = role
            user.status = "active"
            if not verify_password(password, user.password_hash):
                user.password_hash = hash_password(password)
        users[role] = user
    return users


def _ensure_organization(db, users: dict[str, models.AppUser]) -> models.Organization:
    organization = db.query(models.Organization).filter(
        models.Organization.name == ORGANIZATION).first()
    if organization is None:
        organization = models.Organization(
            name=ORGANIZATION, status="active", created_by=users["admin"].id)
        db.add(organization)
        db.flush()
    else:
        organization.status = "active"
    member = db.query(models.OrganizationMember).filter(
        models.OrganizationMember.organization_id == organization.id,
        models.OrganizationMember.user_id == users["hr"].id).first()
    if member is None:
        member = models.OrganizationMember(
            organization_id=organization.id, user_id=users["hr"].id,
            role="hr", status="active")
        db.add(member)
    else:
        member.role = "hr"
        member.status = "active"
    return organization


def _ensure_baseline_versions(db, references: list[dict], admin: models.AppUser) -> None:
    for job_id in dict.fromkeys(reference["job_id"] for reference in references):
        job = db.get(models.Job, job_id)
        if not job:
            raise RuntimeError(f"岗位 {job_id} 不存在")
        existing = db.query(models.JobVersion).filter(
            models.JobVersion.job_id == job.id,
            models.JobVersion.version == (job.version or 1)).first()
        if existing:
            continue
        relations = db.query(models.JobSkill).filter(
            models.JobSkill.job_id == job.id,
            models.JobSkill.status == "active").all()
        relation_ids = [row.id for row in relations]
        evidence_rows = (db.query(models.Evidence).filter(
            models.Evidence.job_skill_id.in_(relation_ids)).all()) if relation_ids else []
        evidence_by_relation: dict[int, list[models.Evidence]] = {}
        for evidence in evidence_rows:
            evidence_by_relation.setdefault(evidence.job_skill_id, []).append(evidence)
        raw_ids = {row.raw_jd_id for row in evidence_rows if row.raw_jd_id}
        dates = [row[0] for row in db.query(models.RawJD.publish_date).filter(
            models.RawJD.id.in_(raw_ids), models.RawJD.publish_date.isnot(None)).all()] \
            if raw_ids else []
        version = models.JobVersion(
            job_id=job.id, version=job.version or 1, status="published",
            effective_at=job.updated_at or datetime.utcnow(),
            evidence_window={
                "start": min(dates).date().isoformat() if dates else None,
                "end": max(dates).date().isoformat() if dates else None,
                "dimensions": {
                    "job_name": job.name, "seniority": job.level,
                    "recruitment_type": job.recruitment_type,
                    "track": job.track, "industry": job.industry,
                },
            },
            summary=job.summary, responsibilities=job.core_responsibilities or [],
            typical_scenarios=job.typical_scenarios or [], created_by=admin.id)
        db.add(version)
        db.flush()
        for relation in relations:
            skill = db.get(models.Skill, relation.skill_id)
            evidence_refs = [{
                "evidence_id": evidence.id, "raw_jd_id": evidence.raw_jd_id,
                "url": evidence.source_url,
            } for evidence in evidence_by_relation.get(relation.id, []) if evidence.raw_jd_id]
            db.add(models.JobVersionSkill(
                job_version_id=version.id, skill_id=relation.skill_id,
                capability_cluster=capability_cluster(skill.name) if skill else None,
                importance=relation.importance, status=relation.status,
                weight=relation.weight, confidence=relation.confidence,
                level_required=relation.level_required, factors=relation.factors or {},
                evidence_refs=evidence_refs))
        db.flush()
        version.contract_snapshot = role_contract.build_contract_from_version(db, job, version)


def _existing_ticket(db, key: str) -> models.FeedbackTicket | None:
    revision = db.query(models.FeedbackRevision).filter(
        models.FeedbackRevision.content.like(f"[演示反馈:{key}]%")).first()
    return db.get(models.FeedbackTicket, revision.ticket_id) if revision else None


def _ensure_feedback(db, users: dict[str, models.AppUser], organization,
                     references: list[dict]) -> list[models.FeedbackTicket]:
    owner = actor_for_user(db, users["user"])
    reviewer = actor_for_user(db, users["admin"])
    tickets = []
    for index, (key, target_status, title) in enumerate(FEEDBACK_SPECS):
        existing = _existing_ticket(db, key)
        if existing:
            tickets.append(existing)
            continue
        reference = references[index]
        ticket = feedback.create_ticket(
            db, owner, target_type="job_version",
            target_id=str(reference["job_version_id"]), category="evidence_review",
            content=f"[演示反馈:{key}] {title}：{reference['job_name']} / {reference['skill_name']}",
            evidence=[reference])
        ticket.organization_id = organization.id
        if target_status == "applied":
            feedback.append_revision(
                db, ticket, owner, target_type="job_version",
                target_id=str(reference["job_version_id"]), category="evidence_review",
                content=(f"[演示反馈:{key}] 修订后：已核对岗位版本、技能、证据与原始 JD 链路"),
                evidence=[reference])
        if target_status in {"triaged", "approved", "rejected", "applied"}:
            feedback.transition(db, ticket, reviewer, action="triage", comment="演示分诊：证据链完整")
        if target_status in {"approved", "applied"}:
            feedback.transition(db, ticket, reviewer, action="approve", comment="演示批准：引用记录有效")
        if target_status == "rejected":
            feedback.transition(db, ticket, reviewer, action="reject", comment="演示驳回：建议与当前版本不符")
        if target_status == "applied":
            feedback.transition(
                db, ticket, reviewer, action="apply", comment="演示应用：关联实际岗位版本记录",
                applied_record_type="job_version",
                applied_record_id=str(reference["job_version_id"]))
        tickets.append(ticket)
    return tickets


def _past_schedule_points() -> list[datetime]:
    now = datetime.now(timezone.utc).astimezone(BEIJING)
    latest = now.replace(hour=2, minute=30, second=0, microsecond=0)
    if latest > now:
        latest -= timedelta(days=1)
    return [
        (latest - timedelta(days=1)).astimezone(timezone.utc).replace(tzinfo=None),
        latest.astimezone(timezone.utc).replace(tzinfo=None),
    ]


def _verify(db, *, require_confidence_history: bool = True) -> dict:
    users = {row.username: row for row in db.query(models.AppUser).filter(
        models.AppUser.username.in_([item[0] for item in USERS])).all()}
    for username, password, role in USERS:
        user = users.get(username)
        assert user and user.role == role and user.status == "active"
        assert verify_password(password, user.password_hash)
    organization = db.query(models.Organization).filter(
        models.Organization.name == ORGANIZATION,
        models.Organization.status == "active").one()
    hr = users["demo-hr"]
    assert db.query(models.OrganizationMember).filter(
        models.OrganizationMember.organization_id == organization.id,
        models.OrganizationMember.user_id == hr.id,
        models.OrganizationMember.status == "active").count() == 1
    statuses = {}
    for key, expected, _ in FEEDBACK_SPECS:
        ticket = _existing_ticket(db, key)
        assert ticket and ticket.status == expected
        statuses[key] = ticket.status
    applied = _existing_ticket(db, "demo-feedback-05")
    event_types = [row[0] for row in db.query(models.FeedbackEvent.event_type).filter(
        models.FeedbackEvent.ticket_id == applied.id).order_by(models.FeedbackEvent.id).all()]
    assert event_types == ["submitted", "revised", "triage", "approve", "apply"]
    snapshot_count = (db.query(models.JobConfidenceSnapshot.as_of).distinct().count()
                      if require_confidence_history else 0)
    if require_confidence_history:
        assert snapshot_count >= 2
    return {"users": sorted(users), "organization_id": organization.id,
            "feedback_statuses": statuses, "applied_timeline": event_types,
            "confidence_snapshot_times": snapshot_count}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="write changes; default is dry-run")
    parser.add_argument("--skip-confidence-history", action="store_true")
    args = parser.parse_args()
    db = SessionLocal()
    try:
        references = _evidence_references(db)
        if not args.apply:
            print(json.dumps(_plan(db, references), ensure_ascii=False, indent=2))
            return
        users = _ensure_users(db)
        organization = _ensure_organization(db, users)
        _ensure_baseline_versions(db, references, users["admin"])
        db.flush()
        references = _evidence_references(db)
        _ensure_feedback(db, users, organization, references)
        db.commit()
        if not args.skip_confidence_history:
            for as_of in _past_schedule_points():
                run_confidence_recalculation(db, as_of=as_of, trigger="seed")
        print(json.dumps({"mode": "applied", "writes": True,
                         "verification": _verify(
                             db, require_confidence_history=not args.skip_confidence_history)},
                         ensure_ascii=False, indent=2))
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
