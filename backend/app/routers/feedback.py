"""Owner/org scoped feedback tickets; public knowledge is never mutated here."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models
from ..auth import Actor, current_actor
from ..db import get_db
from ..ownership import owned_query, require_org, require_owner
from ..schemas import FeedbackCreateRequest
from ..services import feedback as feedback_service

router = APIRouter(prefix="/api/feedback", tags=["feedback"])


def _scope(row: models.FeedbackTicket, actor: Actor):
    return require_org(row, actor) if actor.role == "hr" else require_owner(row, actor)


def _detail(db: Session, ticket: models.FeedbackTicket) -> dict:
    return feedback_service.ticket_detail(db, ticket)


@router.post("", status_code=201)
def create_feedback(payload: FeedbackCreateRequest, actor: Actor = Depends(current_actor),
                    db: Session = Depends(get_db)):
    ticket = feedback_service.create_ticket(
        db, actor, target_type=payload.target_type, target_id=payload.target_id,
        category=payload.category, content=payload.content, evidence=payload.evidence)
    db.commit()
    return _detail(db, ticket)


@router.get("")
def list_feedback(status: str | None = None, page: int = 1, size: int = 20,
                  actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    page, size = max(1, page), min(100, max(1, size))
    if status and status not in feedback_service.VALID_STATUSES:
        raise HTTPException(422, "status 不在反馈状态机内")
    q = owned_query(db.query(models.FeedbackTicket), models.FeedbackTicket, actor)
    if status:
        q = q.filter(models.FeedbackTicket.status == status)
    total = q.count()
    rows = q.order_by(models.FeedbackTicket.updated_at.desc(),
                      models.FeedbackTicket.id.desc()).offset(
        (page - 1) * size).limit(size).all()
    return {"items": [_detail(db, row) for row in rows],
            "total": total, "page": page, "size": size}


@router.get("/{ticket_id}")
def get_feedback(ticket_id: int, actor: Actor = Depends(current_actor),
                 db: Session = Depends(get_db)):
    ticket = db.query(models.FeedbackTicket).get(ticket_id)
    if not ticket:
        raise HTTPException(404, "反馈不存在")
    _scope(ticket, actor)
    return _detail(db, ticket)


@router.patch("/{ticket_id}")
def revise_feedback(ticket_id: int, payload: FeedbackCreateRequest,
                    actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    ticket = db.query(models.FeedbackTicket).get(ticket_id)
    if not ticket:
        raise HTTPException(404, "反馈不存在")
    _scope(ticket, actor)
    feedback_service.append_revision(
        db, ticket, actor, target_type=payload.target_type,
        target_id=payload.target_id, category=payload.category,
        content=payload.content, evidence=payload.evidence)
    db.commit()
    return _detail(db, ticket)
