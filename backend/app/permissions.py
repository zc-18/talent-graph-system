"""Fixed role-template authorization dependencies."""
from __future__ import annotations

from fastapi import Depends, HTTPException

from .auth import Actor, current_actor


def require_roles(*roles: str):
    allowed = set(roles)

    def dependency(actor: Actor = Depends(current_actor)) -> Actor:
        if actor.role not in allowed:
            raise HTTPException(403, "当前角色无权执行此操作")
        return actor

    return dependency


require_user = require_roles("user")
require_hr = require_roles("hr")
require_admin = require_roles("admin")
require_private_actor = require_roles("user", "hr", "admin")
