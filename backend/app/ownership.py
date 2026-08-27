"""Owner and organization scope checks shared by private business routes."""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from .auth import Actor


def require_owner(row, actor: Actor, *, owner_field: str = "owner_user_id"):
    if actor.role == "admin":
        return row
    if getattr(row, owner_field, None) != actor.user_id:
        raise HTTPException(404, "记录不存在")
    return row


def require_org(row, actor: Actor, *, org_field: str = "organization_id"):
    if actor.role == "admin":
        return row
    if actor.organization_id is None or getattr(row, org_field, None) != actor.organization_id:
        raise HTTPException(404, "记录不存在")
    return row


def owned_query(query, model, actor: Actor):
    if actor.role == "admin":
        return query
    if actor.role == "hr":
        return query.filter(model.organization_id == actor.organization_id)
    return query.filter(model.owner_user_id == actor.user_id)
