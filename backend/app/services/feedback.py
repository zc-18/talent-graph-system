"""Feedback revisions, review state transitions and append-only timeline."""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from .. import models
from ..auth import Actor, add_audit


VALID_STATUSES = {"submitted", "triaged", "approved", "rejected", "applied"}
STATUS_LABELS = {
    "submitted": "已提交",
    "triaged": "已分诊",
    "approved": "已批准",
    "rejected": "已驳回",
    "applied": "已应用",
}
TRANSITIONS = {
    "submitted": {"triage": "triaged"},
    "triaged": {"approve": "approved", "reject": "rejected"},
    "approved": {"apply": "applied"},
}
APPLIED_TARGETS = {
    "job_version": models.JobVersion,
    "evolution_run": models.EvolutionRun,
    "job_candidate_revision": models.JobCandidateRevision,
    "skill_alias": models.SkillAlias,
    "crawl_batch": models.CrawlBatch,
}


def _event(db: Session, ticket: models.FeedbackTicket, actor: Actor, event_type: str,
           *, from_status: str | None = None, to_status: str | None = None,
           revision: int | None = None, comment: str | None = None,
           applied_record_type: str | None = None,
           applied_record_id: str | None = None) -> models.FeedbackEvent:
    row = models.FeedbackEvent(
        ticket_id=ticket.id, event_type=event_type, from_status=from_status,
        to_status=to_status, revision=revision,
        comment=(comment or "").strip() or None,
        applied_record_type=applied_record_type,
        applied_record_id=applied_record_id, actor_user_id=actor.user_id)
    db.add(row)
    return row


def create_ticket(db: Session, actor: Actor, *, target_type: str, target_id: str | None,
                  category: str, content: str, evidence: list[dict]) -> models.FeedbackTicket:
    ticket = models.FeedbackTicket(
        owner_user_id=actor.user_id, organization_id=actor.organization_id,
        target_type=target_type, target_id=target_id,
        status="submitted", current_revision=1)
    db.add(ticket)
    db.flush()
    db.add(models.FeedbackRevision(
        ticket_id=ticket.id, revision=1, category=category,
        content=content, evidence=evidence, created_by=actor.user_id))
    _event(db, ticket, actor, "submitted", to_status="submitted", revision=1)
    add_audit(db, actor, "feedback.submit", "feedback_ticket", ticket.id,
              summary={"status": "submitted", "revision": 1})
    return ticket


def append_revision(db: Session, ticket: models.FeedbackTicket, actor: Actor, *,
                    target_type: str, target_id: str | None, category: str,
                    content: str, evidence: list[dict]) -> models.FeedbackRevision:
    if ticket.status != "submitted":
        raise HTTPException(409, "当前状态不可修改")
    ticket.current_revision += 1
    ticket.target_type = target_type
    ticket.target_id = target_id
    revision = models.FeedbackRevision(
        ticket_id=ticket.id, revision=ticket.current_revision,
        category=category, content=content, evidence=evidence,
        created_by=actor.user_id)
    db.add(revision)
    _event(db, ticket, actor, "revised", from_status=ticket.status,
           to_status=ticket.status, revision=ticket.current_revision)
    add_audit(db, actor, "feedback.revise", "feedback_ticket", ticket.id,
              summary={"revision": ticket.current_revision})
    return revision


def validate_applied_target(db: Session, record_type: str | None,
                            record_id: str | None) -> tuple[str, str]:
    if not record_type or not record_id:
        raise HTTPException(422, "applied 必须关联实际变更记录")
    model = APPLIED_TARGETS.get(record_type)
    if model is None:
        raise HTTPException(422, "不支持的变更记录类型")
    try:
        numeric_id = int(record_id)
    except (TypeError, ValueError):
        raise HTTPException(422, "变更记录 ID 必须是整数") from None
    if numeric_id <= 0 or db.get(model, numeric_id) is None:
        raise HTTPException(422, "关联的实际变更记录不存在")
    return record_type, str(numeric_id)


def transition(db: Session, ticket: models.FeedbackTicket, actor: Actor, *, action: str,
               comment: str | None = None, applied_record_type: str | None = None,
               applied_record_id: str | None = None) -> models.FeedbackTicket:
    old_status = ticket.status
    next_status = TRANSITIONS.get(old_status, {}).get(action)
    if not next_status:
        raise HTTPException(409, "反馈状态转换无效")
    applied_reference = validate_applied_target(
        db, applied_record_type, applied_record_id) if action == "apply" else None
    ticket.status = next_status
    if applied_reference:
        ticket.applied_record_type, ticket.applied_record_id = applied_reference
    _event(db, ticket, actor, action, from_status=old_status, to_status=next_status,
           revision=ticket.current_revision, comment=comment,
           applied_record_type=ticket.applied_record_type if applied_reference else None,
           applied_record_id=ticket.applied_record_id if applied_reference else None)
    add_audit(db, actor, f"feedback.{action}", "feedback_ticket", ticket.id,
              summary={"status": next_status, "action": action})
    return ticket


def ticket_detail(db: Session, ticket: models.FeedbackTicket) -> dict:
    revisions = db.query(models.FeedbackRevision).filter(
        models.FeedbackRevision.ticket_id == ticket.id).order_by(
        models.FeedbackRevision.revision).all()
    events = db.query(models.FeedbackEvent).filter(
        models.FeedbackEvent.ticket_id == ticket.id).order_by(
        models.FeedbackEvent.created_at, models.FeedbackEvent.id).all()
    actor_ids = {row.actor_user_id for row in events}
    usernames = dict(db.query(models.AppUser.id, models.AppUser.username).filter(
        models.AppUser.id.in_(actor_ids)).all()) if actor_ids else {}
    current = next((row for row in reversed(revisions)
                    if row.revision == ticket.current_revision), None)
    revision_items = [{
        "revision": row.revision, "category": row.category,
        "content": row.content, "evidence": row.evidence or [],
        "created_by": row.created_by, "created_at": row.created_at.isoformat(),
    } for row in revisions]
    if events:
        timeline = [{
            "id": row.id, "type": row.event_type,
            "from_status": row.from_status, "to_status": row.to_status,
            "revision": row.revision, "comment": row.comment,
            "actor_user_id": row.actor_user_id,
            "actor_username": usernames.get(row.actor_user_id),
            "applied_record_type": row.applied_record_type,
            "applied_record_id": row.applied_record_id,
            "created_at": row.created_at.isoformat(),
        } for row in events]
    else:
        timeline = [{
            "id": None, "type": "submitted" if row.revision == 1 else "revised",
            "from_status": None if row.revision == 1 else "submitted",
            "to_status": "submitted", "revision": row.revision, "comment": None,
            "actor_user_id": row.created_by, "actor_username": None,
            "applied_record_type": None, "applied_record_id": None,
            "created_at": row.created_at.isoformat(),
        } for row in revisions]
    review_comments = [item for item in timeline
                       if item["type"] in {"triage", "approve", "reject", "apply"}]
    return {
        "id": ticket.id, "status": ticket.status,
        "status_label": STATUS_LABELS.get(ticket.status, ticket.status),
        "target_type": ticket.target_type, "target_id": ticket.target_id,
        "current_revision": ticket.current_revision,
        "category": current.category if current else None,
        "content": current.content if current else None,
        "evidence": current.evidence if current else [],
        "revisions": revision_items, "review_comments": review_comments,
        "timeline": timeline,
        "applied_record_type": ticket.applied_record_type,
        "applied_record_id": ticket.applied_record_id,
        "created_at": ticket.created_at.isoformat(),
        "updated_at": ticket.updated_at.isoformat(),
    }
