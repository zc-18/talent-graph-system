"""Administrator review, public publishing, audit and usage endpoints."""
from __future__ import annotations

from datetime import datetime, timedelta
import math

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func

from .. import models
from ..auth import Actor, add_audit
from ..auth import ROLE_PERMISSIONS
from ..db import get_db
from ..guards import require_write
from ..permissions import require_admin
from ..schemas import (CandidateReviewRequest, EvolutionProposeRequest,
                       EvolutionReviewRequest, EvolutionRunRequest,
                       FeedbackReviewRequest)
from ..services import (discovery as discovery_service, evolution as evolution_service,
                        feedback as feedback_service, graph_service, hallucination,
                        role_contract)
from ..services.taxonomy import normalize_skill, skill_category, skill_type

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _page(page: int, size: int) -> tuple[int, int]:
    return max(1, page), min(100, max(1, size))


def _publish_candidate(db: Session, candidate: models.JobCandidate, actor: Actor) -> models.Job:
    revision = db.query(models.JobCandidateRevision).filter(
        models.JobCandidateRevision.candidate_id == candidate.id,
        models.JobCandidateRevision.revision == candidate.current_revision).first()
    if not revision:
        raise HTTPException(409, "候选缺少当前 revision")
    definition = revision.definition or {}
    publishability = discovery_service.candidate_publishability(db, candidate, definition)
    caps = publishability.pop("validated_capabilities", [])
    authority_evidence = publishability.pop("validated_authority_evidence", [])
    if not publishability["publishable"]:
        raise HTTPException(409, {"code": "CANDIDATE_NOT_PUBLISHABLE",
                                  "message": "候选未通过公共发布质量门",
                                  **publishability})
    title = (definition.get("job_title") or definition.get("name") or "").strip()
    if not title:
        raise HTTPException(422, "候选定义缺少 job_title")
    slug = graph_service.slugify(title)
    if db.query(models.Job).filter(models.Job.slug == slug).first():
        raise HTTPException(409, "同义或同名岗位已存在，请转入既有岗位演化")

    if not caps:
        raise HTTPException(422, "候选定义至少需要一项能力")
    job = models.Job(
        name=title, slug=slug, category=definition.get("category") or "其他",
        track=definition.get("track"), industry=definition.get("industry"),
        recruitment_type=definition.get("recruitment_type") or "mixed",
        level=definition.get("level") or definition.get("seniority") or "middle",
        is_new=True, status="published", summary=definition.get("summary") or "",
        core_responsibilities=definition.get("core_responsibilities") or [],
        typical_scenarios=definition.get("typical_scenarios") or [],
        emergence_score=publishability["emergence_score"],
        confidence=hallucination.job_confidence(caps),
        source_summary={"origin": "governed_discovery",
                        "discovery_run_id": candidate.discovery_run_id,
                        "evidence_count": publishability["discovery_evidence_count"]},
        version=1)
    db.add(job)
    db.flush()

    version_evidence_window = {
        **publishability["evidence_window"],
        "dimensions": {
            "job_name": job.name,
            "seniority": job.level or "unspecified",
            "recruitment_type": job.recruitment_type or "mixed",
            "track": job.track or "software",
            "industry": job.industry or "general",
        },
    }
    version = models.JobVersion(
        job_id=job.id, version=1, status="published", effective_at=datetime.utcnow(),
        evidence_window=version_evidence_window, summary=job.summary,
        responsibilities=job.core_responsibilities,
        typical_scenarios=job.typical_scenarios,
        contract_snapshot=None,
        created_by=actor.user_id)
    db.add(version)
    db.flush()

    source_count = 0
    seen: set[int] = set()
    for cap in caps:
        name = normalize_skill(str(cap.get("name") or ""))
        if not name:
            continue
        skill = graph_service.upsert_skill(
            db, name, cap.get("category") or skill_category(name),
            cap.get("skill_type") or skill_type(name), with_embedding=False,
            parent_name=cap.get("parent"))
        if skill.id in seen:
            continue
        seen.add(skill.id)
        importance = cap.get("importance") if cap.get("importance") in {"required", "bonus"} else "required"
        js = models.JobSkill(
            job_id=job.id, skill_id=skill.id, importance=importance,
            weight=float(cap.get("weight") or 0.5),
            level_required=cap.get("level_required") or "familiar",
            confidence=float(cap.get("confidence") or 0), factors=cap.get("factors") or {},
            source_count=int(cap.get("source_count") or 0), status="active",
            first_seen=datetime.utcnow(), last_seen=datetime.utcnow())
        db.add(js)
        db.flush()
        evidence = cap.get("evidence") or []
        graph_service.write_evidence(db, js.id, evidence)
        source_count += js.source_count or 0
        db.add(models.JobVersionSkill(
            job_version_id=version.id, skill_id=skill.id,
            capability_cluster=cap.get("capability_cluster") or cap.get("parent"),
            importance=importance, weight=js.weight, confidence=js.confidence,
            level_required=js.level_required, factors=js.factors,
            evidence_refs=[{"raw_jd_id": e.get("raw_jd_id"), "url": e.get("source_url")}
                           for e in evidence[:12]]))
    if not seen:
        raise HTTPException(422, "候选定义没有有效能力")
    db.flush()
    version.contract_snapshot = role_contract.build_contract_from_version(db, job, version)
    for evidence in authority_evidence:
        db.add(models.AuthorityEvidence(
            job_id=job.id, kind=evidence.get("kind") or "report",
            title=(evidence.get("title") or "")[:256],
            issuer=(evidence.get("issuer") or evidence.get("source") or "")[:128],
            url=evidence.get("url") or evidence.get("source_url"),
            excerpt=evidence.get("excerpt") or evidence.get("content"),
            local_file=evidence.get("local_file")))
    job.evidence_count = source_count
    candidate.status = "published"
    candidate.published_job_id = job.id
    return job


@router.post("/candidates/{candidate_id}/review")
def review_candidate(candidate_id: int, payload: CandidateReviewRequest,
                     actor: Actor = Depends(require_admin), db: Session = Depends(get_db)):
    candidate = db.query(models.JobCandidate).get(candidate_id)
    if not candidate:
        raise HTTPException(404, "候选不存在")
    allowed_states = {"submitted", "approved"} if payload.action == "approve" else {"submitted"}
    if candidate.status not in allowed_states:
        raise HTTPException(409, "当前候选状态不可审核")
    if payload.publish and payload.action != "approve":
        raise HTTPException(422, "只有通过审核才能发布")
    if payload.publish:
        require_write()  # admin 也必须经过公共知识 READ_ONLY 闸

    try:
        db.add(models.CandidateReview(
            candidate_id=candidate.id, revision=candidate.current_revision,
            action=payload.action, comment=payload.comment, reviewer_id=actor.user_id))
        candidate.status = "approved" if payload.action == "approve" else "rejected"
        job = _publish_candidate(db, candidate, actor) if payload.publish else None
        add_audit(db, actor, "candidate.review", "job_candidate", candidate.id,
                  summary={"action": payload.action, "status": candidate.status,
                           "revision": candidate.current_revision,
                           "version": 1 if job else None})
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise
    return {"id": candidate.id, "status": candidate.status,
            "current_revision": candidate.current_revision,
            "published_job": ({"id": job.id, "name": job.name, "version": job.version}
                              if job else None)}


@router.post("/evolution-runs", status_code=201)
def create_evolution_run(payload: EvolutionRunRequest,
                         actor: Actor = Depends(require_admin), db: Session = Depends(get_db)):
    job = db.query(models.Job).get(payload.job_id)
    if not job or job.status != "published":
        raise HTTPException(404, "岗位不存在")
    if payload.idempotency_key:
        existing = db.query(models.EvolutionRun).filter(
            models.EvolutionRun.created_by == actor.user_id,
            models.EvolutionRun.idempotency_key == payload.idempotency_key).first()
        if existing:
            return {"idempotent_replay": True, "run": _evolution_dict(db, existing, True)}
    run = models.EvolutionRun(
        job_id=job.id, organization_id=None, created_by=actor.user_id,
        from_version=job.version or 1, proposed_version=(job.version or 1) + 1,
        status="pending", idempotency_key=payload.idempotency_key,
        input_snapshot=payload.evidence_batch,
        proposed_snapshot=payload.proposed_snapshot, diff={})
    db.add(run)
    db.flush()
    add_audit(db, actor, "evolution.create", "evolution_run", run.id,
              summary={"status": run.status, "version": run.proposed_version})
    db.commit()
    return {"idempotent_replay": False, "run": _evolution_dict(db, run, True)}


def _evolution_dict(db: Session, row: models.EvolutionRun, detail: bool = False) -> dict:
    job = db.get(models.Job, row.job_id)
    value = {"id": row.id, "job_id": row.job_id,
             "job_name": job.name if job else None,
             "organization_id": row.organization_id, "created_by": row.created_by,
             "from_version": row.from_version, "proposed_version": row.proposed_version,
             "status": row.status, "stats": row.stats or {}, "error": row.error,
             "created_at": row.created_at.isoformat(),
             "updated_at": row.updated_at.isoformat()}
    if detail:
        reviews = db.query(models.EvolutionReview).filter(
            models.EvolutionReview.evolution_run_id == row.id).order_by(
            models.EvolutionReview.id).all()
        value.update({
            "input_snapshot": row.input_snapshot or {},
            "proposed_snapshot": row.proposed_snapshot or {},
            "diff": row.diff or [],
            "reviews": [{"id": review.id, "action": review.action,
                         "comment": review.comment, "reviewer_id": review.reviewer_id,
                         "created_at": review.created_at.isoformat()}
                        for review in reviews],
        })
    return value


def _evolution_or_404(db: Session, run_id: int) -> models.EvolutionRun:
    row = db.get(models.EvolutionRun, run_id)
    if not row:
        raise HTTPException(404, "演化任务不存在")
    return row


@router.get("/evolution-runs")
def list_evolution_runs(status: str | None = None, job_id: int | None = None,
                        page: int = 1, size: int = 20,
                        actor: Actor = Depends(require_admin), db: Session = Depends(get_db)):
    page, size = _page(page, size)
    q = db.query(models.EvolutionRun)
    if status:
        q = q.filter(models.EvolutionRun.status == status)
    if job_id is not None:
        q = q.filter(models.EvolutionRun.job_id == job_id)
    total = q.count()
    rows = q.order_by(models.EvolutionRun.updated_at.desc(),
                      models.EvolutionRun.id.desc()).offset((page - 1) * size).limit(size).all()
    return {"items": [_evolution_dict(db, row) for row in rows],
            "total": total, "page": page, "size": size}


@router.get("/evolution-runs/{run_id}")
def get_evolution_run(run_id: int, actor: Actor = Depends(require_admin),
                      db: Session = Depends(get_db)):
    return _evolution_dict(db, _evolution_or_404(db, run_id), True)


@router.post("/evolution-runs/{run_id}/propose")
def propose_evolution(run_id: int, payload: EvolutionProposeRequest,
                      actor: Actor = Depends(require_admin), db: Session = Depends(get_db)):
    run = _evolution_or_404(db, run_id)
    if run.status not in {"pending", "rejected"}:
        raise HTTPException(409, "只有 pending/rejected 任务可生成 proposal")
    job = db.get(models.Job, run.job_id)
    if not job or job.status != "published":
        raise HTTPException(404, "岗位不存在")
    snapshot = payload.proposed_snapshot or run.proposed_snapshot or {}
    try:
        proposed = evolution_service.normalize_proposed_capabilities(snapshot)
    except (TypeError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc
    before = evolution_service.current_capabilities(db, job)
    changes = evolution_service.compute_snapshot_diff(before, proposed)
    if not changes:
        raise HTTPException(409, "proposal 与当前版本没有差异")
    previous_input = run.input_snapshot or {}
    evidence_batch = (payload.evidence_batch or previous_input.get("evidence_batch")
                      or {"evidence": previous_input.get("evidence", [])})
    run.from_version = job.version or 1
    run.proposed_version = run.from_version + 1
    run.input_snapshot = {
        "job_id": job.id, "version": run.from_version,
        "capabilities": before, "evidence_batch": evidence_batch,
    }
    run.proposed_snapshot = {
        "job_id": job.id, "from_version": run.from_version,
        "version": run.proposed_version, "capabilities": proposed,
    }
    run.diff = changes
    run.stats = {
        "changes": len(changes),
        "added": sum(c["change_type"] == "add" for c in changes),
        "deleted": sum(c["change_type"] == "delete" for c in changes),
        "modified": sum(c["change_type"] == "modify" for c in changes),
    }
    run.status, run.error = "proposed", None
    add_audit(db, actor, "evolution.propose", "evolution_run", run.id,
              summary={"status": run.status, "count": len(changes),
                       "version": run.proposed_version})
    db.commit()
    return _evolution_dict(db, run, True)


@router.post("/evolution-runs/{run_id}/review")
def review_evolution(run_id: int, payload: EvolutionReviewRequest,
                     actor: Actor = Depends(require_admin), db: Session = Depends(get_db)):
    run = _evolution_or_404(db, run_id)
    if run.status != "proposed":
        raise HTTPException(409, "只有 proposed 任务可审核")
    db.add(models.EvolutionReview(
        evolution_run_id=run.id, action=payload.action,
        comment=payload.comment, reviewer_id=actor.user_id))
    run.status = "approved" if payload.action == "approve" else "rejected"
    add_audit(db, actor, "evolution.review", "evolution_run", run.id,
              summary={"status": run.status, "action": payload.action,
                       "version": run.proposed_version})
    db.commit()
    return _evolution_dict(db, run, True)


@router.post("/evolution-runs/{run_id}/publish")
def publish_evolution(run_id: int, actor: Actor = Depends(require_admin),
                      db: Session = Depends(get_db)):
    run = _evolution_or_404(db, run_id)
    if run.status == "published":
        return {"idempotent_replay": True, "run": _evolution_dict(db, run, True)}
    if run.status != "approved":
        raise HTTPException(409, "只有 approved 任务可发布")
    require_write()
    try:
        job = db.get(models.Job, run.job_id)
        if not job or job.status != "published":
            raise HTTPException(404, "岗位不存在")
        if (job.version or 1) != run.from_version:
            raise HTTPException(409, "岗位当前版本已变化，请重新生成 proposal")
        before = (run.input_snapshot or {}).get("capabilities") or []
        proposed = evolution_service.normalize_proposed_capabilities(
            run.proposed_snapshot or {})
        current = evolution_service.current_capabilities(db, job)
        if evolution_service.compute_snapshot_diff(before, current):
            raise HTTPException(409, "岗位能力已变化，请重新生成 proposal")

        baseline = evolution_service.snapshot_job_version(db, job, created_by=actor.user_id)
        baseline_caps = evolution_service.version_capabilities(db, baseline)
        if evolution_service.compute_snapshot_diff(before, baseline_caps):
            raise RuntimeError("当前 JobVersion 快照与岗位能力不一致")

        changes = list(run.diff or [])
        evolution_service.apply_evolution(db, job, proposed, changes, commit=False)
        if job.version != run.proposed_version:
            raise RuntimeError("Job.current version 未同步 proposed_version")
        published_version = evolution_service.snapshot_job_version(
            db, job, created_by=actor.user_id)
        db.flush()
        logged_rows = db.query(models.CapabilityChange).filter(
            models.CapabilityChange.job_id == job.id,
            models.CapabilityChange.version == job.version).order_by(
            models.CapabilityChange.id).all()
        logged = [{
            "change_type": row.change_type, "skill_name": row.skill_name,
            "old_value": row.old_value, "new_value": row.new_value,
        } for row in logged_rows]
        after = evolution_service.version_capabilities(db, published_version)
        evolution_service.assert_snapshot_reconciled(
            before=baseline_caps, proposed=proposed, after=after, changes=logged)
        if published_version.version != job.version:
            raise RuntimeError("JobVersion 与 Job.current version 不一致")

        run.status, run.error = "published", None
        add_audit(db, actor, "evolution.publish", "evolution_run", run.id,
                  summary={"status": run.status, "count": len(logged),
                           "version": job.version})
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        failed = db.get(models.EvolutionRun, run_id)
        if failed:
            failed.error = str(exc)[:2000]
            db.commit()
        raise HTTPException(409, {"code": "EVOLUTION_RECONCILIATION_FAILED",
                                  "message": str(exc)}) from exc
    return {"idempotent_replay": False, "run": _evolution_dict(db, run, True),
            "job": {"id": job.id, "name": job.name, "version": job.version}}


@router.get("/users")
def users(status: str | None = None, role: str | None = None, page: int = 1, size: int = 20,
          actor: Actor = Depends(require_admin), db: Session = Depends(get_db)):
    page, size = _page(page, size)
    q = db.query(models.AppUser)
    if status:
        q = q.filter(models.AppUser.status == status)
    if role:
        q = q.filter(models.AppUser.role == role)
    total = q.count()
    rows = q.order_by(models.AppUser.id).offset((page - 1) * size).limit(size).all()
    memberships = {m.user_id: m.organization_id for m in db.query(models.OrganizationMember).filter(
        models.OrganizationMember.user_id.in_({u.id for u in rows}),
        models.OrganizationMember.status == "active").all()} if rows else {}
    org_names = dict(db.query(models.Organization.id, models.Organization.name).filter(
        models.Organization.id.in_(set(memberships.values()))).all()) if memberships else {}
    return {"items": [{"id": u.id, "username": u.username, "role": u.role,
                       "status": u.status,
                       "organization_id": memberships.get(u.id),
                       "organization_name": org_names.get(memberships.get(u.id)),
                       "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
                       "created_at": u.created_at.isoformat()} for u in rows],
            "total": total, "page": page, "size": size}


@router.patch("/users/{user_id}")
def update_user(user_id: int, payload: dict, actor: Actor = Depends(require_admin),
                db: Session = Depends(get_db)):
    user = db.query(models.AppUser).get(user_id)
    if not user:
        raise HTTPException(404, "用户不存在")
    role = payload.get("role", user.role)
    status = payload.get("status", user.status)
    if role not in {"user", "hr", "admin"} or status not in {"active", "disabled"}:
        raise HTTPException(422, "role/status 不在固定模板内")
    if user.role == "admin" and (role != "admin" or status != "active"):
        other_admins = db.query(models.AppUser).filter(
            models.AppUser.role == "admin", models.AppUser.status == "active",
            models.AppUser.id != user.id).count()
        if other_admins == 0:
            raise HTTPException(409, "不能停用或降级最后一个管理员")
    if role == "hr":
        org_id = payload.get("organization_id")
        org = db.query(models.Organization).get(org_id) if org_id else None
        if not org or org.status != "active":
            raise HTTPException(422, "HR 必须关联有效组织")
        db.query(models.OrganizationMember).filter(
            models.OrganizationMember.user_id == user.id,
            models.OrganizationMember.organization_id != org.id).update(
            {models.OrganizationMember.status: "disabled"}, synchronize_session=False)
        member = db.query(models.OrganizationMember).filter(
            models.OrganizationMember.user_id == user.id,
            models.OrganizationMember.organization_id == org.id).first()
        if member:
            member.status = "active"
        else:
            db.add(models.OrganizationMember(organization_id=org.id, user_id=user.id,
                                             role="hr", status="active"))
    user.role, user.status = role, status
    add_audit(db, actor, "admin.user.update", "app_user", user.id,
              summary={"status": status})
    db.commit()
    return {"id": user.id, "username": user.username, "role": user.role,
            "status": user.status, "permissions": list(ROLE_PERMISSIONS[user.role])}


@router.get("/permissions")
def permission_templates(actor: Actor = Depends(require_admin)):
    return {"items": [{"role": role, "permissions": list(permissions)}
                      for role, permissions in ROLE_PERMISSIONS.items()]}


@router.get("/organizations")
def organizations(status: str | None = None, page: int = 1, size: int = 20,
                  actor: Actor = Depends(require_admin), db: Session = Depends(get_db)):
    page, size = _page(page, size)
    q = db.query(models.Organization)
    if status:
        q = q.filter(models.Organization.status == status)
    total = q.count()
    rows = q.order_by(models.Organization.id).offset((page - 1) * size).limit(size).all()
    member_counts = dict(db.query(models.OrganizationMember.organization_id,
                                  func.count(models.OrganizationMember.id))
                         .group_by(models.OrganizationMember.organization_id).all())
    return {"items": [{"id": o.id, "name": o.name, "status": o.status,
                       "member_count": member_counts.get(o.id, 0),
                       "created_at": o.created_at.isoformat()} for o in rows],
            "total": total, "page": page, "size": size}


@router.post("/organizations", status_code=201)
def create_organization(payload: dict, actor: Actor = Depends(require_admin),
                        db: Session = Depends(get_db)):
    name = str(payload.get("name") or "").strip()
    if not name or len(name) > 128:
        raise HTTPException(422, "组织名称不能为空且不超过 128 字符")
    if db.query(models.Organization).filter(models.Organization.name == name).first():
        raise HTTPException(409, "组织名称已存在")
    row = models.Organization(name=name, status="active", created_by=actor.user_id)
    db.add(row)
    db.flush()
    add_audit(db, actor, "admin.organization.create", "organization", row.id)
    db.commit()
    return {"id": row.id, "name": row.name, "status": row.status,
            "created_at": row.created_at.isoformat()}


@router.patch("/organizations/{organization_id}")
def update_organization(organization_id: int, payload: dict,
                        actor: Actor = Depends(require_admin), db: Session = Depends(get_db)):
    row = db.query(models.Organization).get(organization_id)
    if not row:
        raise HTTPException(404, "组织不存在")
    status = payload.get("status", row.status)
    if status not in {"active", "disabled"}:
        raise HTTPException(422, "status 必须是 active/disabled")
    row.status = status
    add_audit(db, actor, "admin.organization.update", "organization", row.id,
              summary={"status": status})
    db.commit()
    return {"id": row.id, "name": row.name, "status": row.status}


@router.get("/audit-logs")
def audit_logs(action: str | None = None, result: str | None = None,
               page: int = 1, size: int = 50,
               actor: Actor = Depends(require_admin), db: Session = Depends(get_db)):
    page, size = _page(page, size)
    q = db.query(models.AuditLog)
    if action:
        q = q.filter(models.AuditLog.action == action)
    if result:
        q = q.filter(models.AuditLog.result == result)
    total = q.count()
    rows = q.order_by(models.AuditLog.created_at.desc()).offset((page - 1) * size).limit(size).all()
    usernames = dict(db.query(models.AppUser.id, models.AppUser.username).filter(
        models.AppUser.id.in_({r.actor_user_id for r in rows if r.actor_user_id})).all()) if rows else {}
    return {"items": [{"id": r.id, "actor_user_id": r.actor_user_id,
                       "actor_id": r.actor_user_id,
                       "actor_username": usernames.get(r.actor_user_id),
                       "organization_id": r.organization_id, "action": r.action,
                       "target_type": r.target_type, "target_id": r.target_id,
                       "result": r.result, "summary": r.summary or {},
                       "created_at": r.created_at.isoformat()} for r in rows],
            "total": total, "page": page, "size": size}


def _percentile(values: list[int], pct: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, math.ceil(len(ordered) * pct) - 1))]


@router.get("/usage/daily")
def usage_daily(days: int = 30, actor: Actor = Depends(require_admin),
                db: Session = Depends(get_db)):
    days = min(366, max(1, days))
    start = (datetime.utcnow() - timedelta(days=days - 1)).replace(
        hour=0, minute=0, second=0, microsecond=0)
    rows = db.query(models.UsageEvent).filter(
        models.UsageEvent.created_at >= start).order_by(models.UsageEvent.created_at).all()
    buckets: dict[str, list[models.UsageEvent]] = {}
    for row in rows:
        buckets.setdefault(row.created_at.date().isoformat(), []).append(row)
    items = []
    for day, events in sorted(buckets.items()):
        durations = [e.duration_ms for e in events]
        features: dict[str, int] = {}
        for event in events:
            features[event.feature] = features.get(event.feature, 0) + 1
        items.append({"date": day, "active_users": len({e.user_id for e in events if e.user_id}),
                      "total": len(events), "success": sum(bool(e.success) for e in events),
                      "errors": sum(not bool(e.success) for e in events),
                      "error_rate": round(sum(not bool(e.success) for e in events) / len(events), 4),
                      "p50_ms": _percentile(durations, .50),
                      "p95_ms": _percentile(durations, .95), "features": features,
                      "logins": features.get("login", 0),
                      "job_views": features.get("job_view", 0),
                      "discovery_runs": features.get("discovery", 0),
                      "matches": features.get("match", 0),
                      "batch_resumes": features.get("batch_resume", 0),
                      "team_reviews": features.get("team_review", 0)})
    return {"items": items, "days": days}


@router.get("/feedback")
def feedback_tickets(status: str | None = None, page: int = 1, size: int = 50,
                     actor: Actor = Depends(require_admin), db: Session = Depends(get_db)):
    page, size = _page(page, size)
    valid_statuses = {"submitted", "triaged", "approved", "rejected", "applied"}
    if status and status not in valid_statuses:
        raise HTTPException(422, "status 不在反馈状态机内")
    query = db.query(models.FeedbackTicket)
    if status:
        query = query.filter(models.FeedbackTicket.status == status)
    total = query.count()
    rows = query.order_by(
        models.FeedbackTicket.updated_at.desc(), models.FeedbackTicket.id.desc()
    ).offset((page - 1) * size).limit(size).all()
    if not rows:
        return {"items": [], "total": total, "page": page, "size": size}

    revisions = db.query(models.FeedbackRevision).filter(
        models.FeedbackRevision.ticket_id.in_({row.id for row in rows})
    ).all()
    current_revisions = {
        (revision.ticket_id, revision.revision): revision for revision in revisions
    }
    usernames = dict(db.query(models.AppUser.id, models.AppUser.username).filter(
        models.AppUser.id.in_({row.owner_user_id for row in rows})
    ).all())
    organization_ids = {row.organization_id for row in rows if row.organization_id}
    organization_names = dict(db.query(models.Organization.id, models.Organization.name).filter(
        models.Organization.id.in_(organization_ids)
    ).all()) if organization_ids else {}

    items = []
    for row in rows:
        revision = current_revisions.get((row.id, row.current_revision))
        items.append({
            "id": row.id, "status": row.status,
            "owner_user_id": row.owner_user_id,
            "owner_username": usernames.get(row.owner_user_id),
            "organization_id": row.organization_id,
            "organization_name": organization_names.get(row.organization_id),
            "target_type": row.target_type, "target_id": row.target_id,
            "current_revision": row.current_revision,
            "category": revision.category if revision else None,
            "content": revision.content if revision else None,
            "evidence": revision.evidence if revision else [],
            "applied_record_type": row.applied_record_type,
            "applied_record_id": row.applied_record_id,
            "created_at": row.created_at.isoformat(),
            "updated_at": row.updated_at.isoformat(),
        })
    return {"items": items, "total": total, "page": page, "size": size}


@router.post("/feedback/{ticket_id}/review")
def review_feedback(ticket_id: int, payload: FeedbackReviewRequest,
                    actor: Actor = Depends(require_admin), db: Session = Depends(get_db)):
    ticket = db.get(models.FeedbackTicket, ticket_id)
    if not ticket:
        raise HTTPException(404, "反馈不存在")
    feedback_service.transition(
        db, ticket, actor, action=payload.action, comment=payload.comment,
        applied_record_type=payload.applied_record_type,
        applied_record_id=payload.applied_record_id)
    db.commit()
    return feedback_service.ticket_detail(db, ticket)
