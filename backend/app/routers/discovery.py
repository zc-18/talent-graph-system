"""新岗位发现与定义路由。"""
from __future__ import annotations
import logging
from time import perf_counter
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session, sessionmaker
from ..db import get_db
from .. import models
from ..schemas import (CandidatePatchRequest, DiscoverRequest, DefineRequest,
                       DiscoveryRunRequest)
from ..services import discovery, graph_service
from ..guards import is_read_only, READ_ONLY_MESSAGE
from ..auth import Actor, actor_for_user, add_audit, add_usage, current_actor
from ..ownership import owned_query, require_org, require_owner

log = logging.getLogger(__name__)

# 排队中的行必须先落库才能返回 run_id，但 conclusion 是 NOT NULL —— 用这个哨兵占位，
# 前端据此知道结论尚未产生（而不是把它当成一个真的分类结果去渲染）。
PENDING_CONCLUSION = "PENDING"

router = APIRouter(prefix="/api/discovery", tags=["discovery"])


def _candidate_detail(db: Session, candidate: models.JobCandidate) -> dict:
    revisions = db.query(models.JobCandidateRevision).filter(
        models.JobCandidateRevision.candidate_id == candidate.id).order_by(
        models.JobCandidateRevision.revision).all()
    reviews = db.query(models.CandidateReview).filter(
        models.CandidateReview.candidate_id == candidate.id).order_by(
        models.CandidateReview.id).all()
    current = next((r for r in reversed(revisions)
                    if r.revision == candidate.current_revision), None)
    current_payload = ({"id": current.id, "revision": current.revision,
                        "definition": current.definition,
                        **(current.definition or {}),
                        "change_note": current.change_note,
                        "created_at": current.created_at.isoformat()}
                       if current else None)
    definition = current.definition if current else {}
    return {
        "id": candidate.id, "status": candidate.status,
        "title": definition.get("job_title") or definition.get("name"),
        "job_title": definition.get("job_title") or definition.get("name"),
        "owner_user_id": candidate.owner_user_id,
        "organization_id": candidate.organization_id,
        "discovery_run_id": candidate.discovery_run_id,
        "current_revision_number": candidate.current_revision,
        "current_revision": current_payload,
        "definition": definition,
        "published_job_id": candidate.published_job_id,
        "revisions": [{"id": r.id, "revision": r.revision,
                       "definition": r.definition, "change_note": r.change_note,
                       "created_by": r.created_by,
                       "created_at": r.created_at.isoformat()} for r in revisions],
        "reviews": [{"id": r.id, "revision": r.revision, "action": r.action,
                    "comment": r.comment, "reviewer_id": r.reviewer_id,
                    "created_at": r.created_at.isoformat()} for r in reviews],
        "created_at": candidate.created_at.isoformat(),
        "updated_at": candidate.updated_at.isoformat(),
    }


def _scope_candidate(candidate: models.JobCandidate, actor: Actor):
    return (require_org(candidate, actor) if actor.role == "hr"
            else require_owner(candidate, actor))


def _find_published_job(db: Session, *names: str | None) -> models.Job | None:
    """Resolve a canonical title even when imported jobs use stable source slugs."""
    candidates = list(dict.fromkeys(name.strip() for name in names if name and name.strip()))
    for name in candidates:
        matched = db.query(models.Job).filter(
            models.Job.name == name, models.Job.status == "published").first()
        if matched:
            return matched
    for name in candidates:
        matched = db.query(models.Job).filter(
            models.Job.slug == graph_service.slugify(name),
            models.Job.status == "published").first()
        if matched:
            return matched
    return None


def _matched_job_dict(job: models.Job | None) -> dict | None:
    return ({"id": job.id, "name": job.name, "version": job.version}
            if job else None)


def _ensure_evolution_run(db: Session, discovery_run: models.DiscoveryRun,
                          job: models.Job, actor: Actor) -> tuple[models.EvolutionRun, bool]:
    key = f"discovery:{discovery_run.id}"
    existing = db.query(models.EvolutionRun).filter(
        models.EvolutionRun.created_by == actor.user_id,
        models.EvolutionRun.idempotency_key == key).first()
    if existing:
        return existing, False
    row = models.EvolutionRun(
        job_id=job.id, organization_id=actor.organization_id,
        created_by=actor.user_id, from_version=job.version or 1,
        proposed_version=(job.version or 1) + 1, status="pending",
        idempotency_key=key,
        input_snapshot={"discovery_run_id": discovery_run.id,
                        "evidence": discovery_run.evidence_snapshot or []},
        proposed_snapshot={}, diff={}, stats={})
    db.add(row)
    db.flush()
    add_audit(db, actor, "evolution.create", "evolution_run", row.id,
              summary={"status": row.status, "version": row.proposed_version})
    return row, True


def _save_if_absent(db: Session, definition: dict) -> tuple[dict | None, dict | None]:
    """岗位已在图谱中则**拒绝落库**，返回冲突说明而不是覆盖。

    `graph_service.upsert_job` 会先清空该岗位的 JobSkill/Evidence 再按传入能力项重建
    ——这对「全量语料重跑管线」是对的，但发现接口传进去的是 LLM 根据几条检索证据
    现生成的十来项能力。对一个已经由 2306 条真实 JD 交叉验证建好的岗位调用它，
    等于用弱证据抹掉强证据：实测线上点了两次「发现」，提示词工程师 301 条能力关系
    连同证据被物理删除、只剩 7 条，且 is_new 被翻成 True，新兴岗位数从 6 变 7，
    交付文档里的口径当场对不上。

    语义上也说得通：已经在图谱里的岗位本就不是「新发现」。此时返回定义预览 +
    冲突提示，既不破坏数据，也让「系统能识别岗位已存在、避免重复建岗」这件事
    在演示中可见。要更新既有岗位能力请走演化接口。
    """
    slug = graph_service.slugify(definition["job_title"])
    existing = db.query(models.Job).filter(models.Job.slug == slug).first()
    if existing:
        n_caps = db.query(models.JobSkill).filter(
            models.JobSkill.job_id == existing.id,
            models.JobSkill.status == "active").count()
        return None, {
            "reason": "already_exists",
            "job_id": existing.id, "job_name": existing.name,
            "is_new": bool(existing.is_new), "version": existing.version,
            "active_capabilities": n_caps,
            "message": (f"「{existing.name}」已在图谱中（v{existing.version}，"
                        f"{n_caps} 项已验证能力），未覆盖。"
                        "如需更新其能力项，请使用「岗位能力演化」。"),
        }
    return None, {
        "reason": "governed_workflow_required",
        "message": "新岗位必须通过候选提交与管理员审核发布。",
        "workflow": "/api/discovery/runs",
    }


@router.get("/seeds")
def seeds():
    """内置新兴岗位候选种子。"""
    return {"seeds": discovery.EMERGING_SEEDS}


@router.post("/discover")
def discover(payload: DiscoverRequest, db: Session = Depends(get_db)):
    """检索某新兴岗位证据并生成定义；save=True 且岗位尚不存在时落库。"""
    cand = discovery.discover_candidates(payload.keyword, db=db)
    if cand.get("verdict") == "AMBIGUOUS":
        return {"candidate": cand, "definition": None, "saved": None,
                "conflict": {"reason": "ambiguous_query",
                             "candidates": cand["resolution"]["candidates"],
                             "message": "岗位输入存在歧义，请先选择轨道。"}}
    if cand.get("verdict") == "ESTABLISHED":
        existing = _find_published_job(db, cand["existing_job"], payload.keyword)
        return {"candidate": cand, "definition": None, "saved": None,
                "conflict": {"reason": "established_job",
                             "job_id": existing.id if existing else None,
                             "job_name": existing.name if existing else cand["existing_job"],
                             "message": "该输入命中既有岗位，请查看画像或发起演化。"}}
    definition = discovery.define_new_job(payload.keyword, cand["evidence"])
    definition["emergence_score"] = max(definition.get("emergence_score", 0), cand["emergence_score"])
    # 只读模式下强制 dry-run：检索与定义生成照跑（这才是要演示的东西），但不落库。
    # Legacy preview endpoints never publish directly. Persisted work must use /runs ->
    # candidate revisions -> admin review, which is the only transactional publication path.
    saved, conflict = None, None
    return {"candidate": {"keyword": cand["keyword"], "emergence_score": cand["emergence_score"],
                          "evidence_count": cand["evidence_count"],
                          "independent_sources": cand.get("independent_sources", 0),
                          "signals": cand.get("signals", {}),
                          "evidence": cand["evidence"][:6]},
            "definition": definition, "saved": saved, "conflict": conflict,
            **({"dry_run": True, "notice": (READ_ONLY_MESSAGE if is_read_only()
                                               else "请通过候选提交与管理员审核发布")}
               if payload.save else {})}


@router.post("/define")
def define(payload: DefineRequest, db: Session = Depends(get_db)):
    """基于给定证据(可人工补充)生成/保存新岗位定义。"""
    evidence = payload.evidence
    if not evidence:
        evidence = discovery.discover_candidates(payload.keyword, db=db)["evidence"]
    definition = discovery.define_new_job(payload.keyword, evidence)
    saved, conflict = None, None
    return {"definition": definition, "saved": saved, "conflict": conflict,
            **({"dry_run": True, "notice": (READ_ONLY_MESSAGE if is_read_only()
                                               else "请通过候选提交与管理员审核发布")}
               if payload.save else {})}


def _evolution_run_for(db: Session, run: models.DiscoveryRun) -> models.EvolutionRun | None:
    """Resolve the evolution run an ESTABLISHED discovery already spawned.

    Keyed by run id, so it also works on the read path where no actor filter applies.
    """
    return db.query(models.EvolutionRun).filter(
        models.EvolutionRun.idempotency_key == f"discovery:{run.id}").first()


def _run_envelope(db: Session, run: models.DiscoveryRun, *,
                  candidate: models.JobCandidate | None = None,
                  matched: models.Job | None = None,
                  evolution_run: models.EvolutionRun | None = None,
                  idempotent_replay: bool = False) -> dict:
    """The single response shape for create / replay / poll.

    The poll endpoint returns exactly what the synchronous POST returns, so a client
    renders a finished background run through the same code path as an inline one.
    """
    if candidate is None:
        candidate = db.query(models.JobCandidate).filter(
            models.JobCandidate.discovery_run_id == run.id).first()
    if matched is None and run.matched_job_id:
        matched = db.get(models.Job, run.matched_job_id)
    if evolution_run is None:
        evolution_run = _evolution_run_for(db, run)
    classification = None if run.conclusion == PENDING_CONCLUSION else run.conclusion
    return {"idempotent_replay": idempotent_replay,
            "run_id": run.id, "status": run.status, "error": run.error,
            "classification": classification,
            "candidate_id": candidate.id if candidate else None,
            "run": _run_dict(run),
            "candidate": _candidate_detail(db, candidate) if candidate else None,
            "matched_job": _matched_job_dict(matched),
            "evolution_run_id": evolution_run.id if evolution_run else None}


def _execute_run(db: Session, run: models.DiscoveryRun, actor: Actor) -> dict:
    """The expensive half of a discovery run: web retrieval + LLM definition.

    Worst case this is ~200s of network and model latency, which is why the HTTP route
    can hand it to a background worker. The caller owns the commit, so the same body
    serves both the inline path (request session) and the worker path (its own session).

    READ_ONLY does not gate this: a run only writes owner/org-scoped workflow rows
    (DiscoveryRun / JobCandidate / revisions), never public graph knowledge. The
    read-only demo therefore still produces a real definition to render.
    """
    started = perf_counter()
    keyword = run.query
    found = discovery.discover_candidates(keyword, db=db)
    verdict = (found.get("verdict") or "INSUFFICIENT_EVIDENCE").upper()
    conclusion = verdict if verdict in {"ESTABLISHED", "AMBIGUOUS"} else "NEW"
    resolution = found.get("resolution") or {}
    canonical = found.get("existing_job") or resolution.get("canonical_title") or keyword
    matched = (_find_published_job(db, canonical, keyword)
               if conclusion == "ESTABLISHED" else None)
    evidence = found.get("evidence", [])
    signals = {**(found.get("signals") or {}),
               "source_verdict": verdict,
               "emergence_score": float(found.get("emergence_score") or 0)}
    definition = discovery.define_new_job(keyword, evidence) if conclusion == "NEW" else None

    # Caller-supplied conditions win over the resolver's guesses, as in the original flow.
    run.conditions = {**resolution, **(run.conditions or {})}
    run.evidence_snapshot = evidence
    run.signal_snapshot = signals
    run.conclusion = conclusion
    run.matched_job_id = matched.id if matched else None
    run.status = "completed"
    run.error = None
    db.flush()

    candidate = db.query(models.JobCandidate).filter(
        models.JobCandidate.discovery_run_id == run.id).first()
    if conclusion == "NEW" and candidate is None:
        candidate = models.JobCandidate(
            discovery_run_id=run.id, owner_user_id=run.owner_user_id,
            organization_id=run.organization_id, status="draft", current_revision=1)
        db.add(candidate)
        db.flush()
        db.add(models.JobCandidateRevision(
            candidate_id=candidate.id, revision=1, definition=definition,
            change_note="initial discovery", created_by=run.owner_user_id))
    evolution_run = None
    if conclusion == "ESTABLISHED" and matched:
        evolution_run, _ = _ensure_evolution_run(db, run, matched, actor)
    add_audit(db, actor, "discovery.run", "discovery_run", run.id,
              summary={"status": conclusion})
    add_usage(db, actor, "discovery", int((perf_counter() - started) * 1000))
    return _run_envelope(db, run, candidate=candidate, matched=matched,
                         evolution_run=evolution_run)


def _run_discovery_task(bind, run_id: int, user_id: int) -> None:
    """Background worker for one queued run.

    Takes the engine, not the request session: by the time this runs FastAPI has closed
    the session ``get_db`` yielded, so reusing it would raise on first access.
    """
    db = sessionmaker(bind=bind)()
    try:
        run = db.get(models.DiscoveryRun, run_id)
        user = db.get(models.AppUser, user_id)
        if run is None or user is None:
            return
        if run.status not in {"queued", "failed"}:
            return
        run.status = "running"
        run.error = None
        db.commit()
        _execute_run(db, run, actor_for_user(db, user))
        db.commit()
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        log.exception("discovery run %s failed", run_id)
        failed = db.get(models.DiscoveryRun, run_id)
        if failed is not None:
            failed.status = "failed"
            failed.error = f"{type(exc).__name__}: {exc}"[:2000]
            db.commit()
    finally:
        db.close()


@router.post("/runs", status_code=201)
def create_run(payload: DiscoveryRunRequest, background_tasks: BackgroundTasks,
               async_mode: bool = False,
               actor: Actor = Depends(current_actor),
               db: Session = Depends(get_db)):
    """Run discovery and persist only an owner/org-scoped workflow record.

    ``async_mode=true`` (a query parameter, so the shared request schema stays untouched)
    returns a ``queued`` run id immediately and moves retrieval + LLM into a background
    task; poll ``GET /runs/{id}`` for the result. The default stays synchronous because
    non-polling callers read the classification straight out of the create response.

    This remains available in READ_ONLY mode because it does not mutate public knowledge.
    """
    if payload.idempotency_key:
        existing = db.query(models.DiscoveryRun).filter(
            models.DiscoveryRun.owner_user_id == actor.user_id,
            models.DiscoveryRun.idempotency_key == payload.idempotency_key).first()
        if existing:
            # A failed run must not lock its key forever — re-run instead of replaying.
            if existing.status == "failed":
                existing.status = "queued"
                existing.error = None
                db.commit()
                if async_mode:
                    background_tasks.add_task(_run_discovery_task, db.get_bind(),
                                              existing.id, actor.user_id)
                    return _run_envelope(db, existing, idempotent_replay=True)
                _execute_run(db, existing, actor)
                db.commit()
                return _run_envelope(db, existing, idempotent_replay=True)
            evolution_run, created_evolution = None, False
            matched = (db.get(models.Job, existing.matched_job_id)
                       if existing.matched_job_id else None)
            if existing.conclusion == "ESTABLISHED" and matched:
                evolution_run, created_evolution = _ensure_evolution_run(
                    db, existing, matched, actor)
            if created_evolution:
                db.commit()
            return _run_envelope(db, existing, matched=matched,
                                 evolution_run=evolution_run, idempotent_replay=True)

    conditions = dict(payload.conditions)
    for key in ("track", "industry", "seniority", "recruitment_type", "keywords"):
        value = getattr(payload, key)
        if value not in (None, [], ""):
            conditions[key] = value

    run = models.DiscoveryRun(
        owner_user_id=actor.user_id, organization_id=actor.organization_id,
        query=payload.keyword, conditions=conditions,
        evidence_snapshot=[], signal_snapshot={},
        conclusion=PENDING_CONCLUSION, status="queued",
        idempotency_key=payload.idempotency_key)
    db.add(run)
    db.flush()

    if not async_mode:
        envelope = _execute_run(db, run, actor)
        db.commit()
        return envelope

    add_audit(db, actor, "discovery.run", "discovery_run", run.id,
              summary={"status": "queued"})
    db.commit()
    background_tasks.add_task(_run_discovery_task, db.get_bind(), run.id, actor.user_id)
    return _run_envelope(db, run)


@router.get("/runs")
def list_runs(status: str | None = None, page: int = 1, size: int = 20,
              actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    """Owner/org-scoped run history — the client uses it to resume an unfinished poll."""
    size = min(max(size, 1), 100)
    page = max(page, 1)
    q = owned_query(db.query(models.DiscoveryRun), models.DiscoveryRun, actor)
    if status:
        q = q.filter(models.DiscoveryRun.status == status)
    total = q.count()
    rows = q.order_by(models.DiscoveryRun.id.desc()).offset((page - 1) * size).limit(size).all()
    return {"items": [_run_dict(row) for row in rows],
            "total": total, "page": page, "size": size}


@router.get("/runs/{run_id}")
def get_run(run_id: int, actor: Actor = Depends(current_actor),
            db: Session = Depends(get_db)):
    """Poll one run. Returns the same envelope the synchronous POST does."""
    run = db.get(models.DiscoveryRun, run_id)
    if not run:
        raise HTTPException(404, "发现任务不存在")
    if actor.role == "hr":
        require_org(run, actor)
    else:
        require_owner(run, actor)
    return _run_envelope(db, run)


def _run_dict(run: models.DiscoveryRun) -> dict:
    return {"id": run.id, "query": run.query, "conditions": run.conditions or {},
            "classification": run.conclusion, "matched_job_id": run.matched_job_id,
            "status": run.status, "error": run.error,
            "signals": run.signal_snapshot or {},
            "evidence": run.evidence_snapshot or [],
            "created_at": run.created_at.isoformat()}


@router.get("/candidates")
def list_candidates(status: str | None = None, page: int = 1, size: int = 20,
                    actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    size = min(max(size, 1), 100)
    page = max(page, 1)
    q = owned_query(db.query(models.JobCandidate), models.JobCandidate, actor)
    if status:
        q = q.filter(models.JobCandidate.status == status)
    total = q.count()
    rows = q.order_by(models.JobCandidate.updated_at.desc()).offset((page - 1) * size).limit(size).all()
    return {"items": [_candidate_detail(db, row) for row in rows],
            "total": total, "page": page, "size": size}


@router.get("/candidates/{candidate_id}")
def get_candidate(candidate_id: int, actor: Actor = Depends(current_actor),
                  db: Session = Depends(get_db)):
    candidate = db.query(models.JobCandidate).get(candidate_id)
    if not candidate:
        raise HTTPException(404, "候选不存在")
    _scope_candidate(candidate, actor)
    return _candidate_detail(db, candidate)


@router.patch("/candidates/{candidate_id}")
def revise_candidate(candidate_id: int, payload: CandidatePatchRequest,
                     actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    candidate = db.query(models.JobCandidate).get(candidate_id)
    if not candidate:
        raise HTTPException(404, "候选不存在")
    _scope_candidate(candidate, actor)
    if candidate.status not in {"draft", "rejected"}:
        raise HTTPException(409, "当前状态不可修订")
    if not payload.definition.get("job_title"):
        raise HTTPException(422, "definition.job_title 不能为空")
    candidate.current_revision += 1
    candidate.status = "draft"
    db.add(models.JobCandidateRevision(
        candidate_id=candidate.id, revision=candidate.current_revision,
        definition=payload.definition, change_note=payload.change_note,
        created_by=actor.user_id))
    add_audit(db, actor, "candidate.revise", "job_candidate", candidate.id,
              summary={"revision": candidate.current_revision})
    db.commit()
    return _candidate_detail(db, candidate)


@router.post("/candidates/{candidate_id}/submit")
def submit_candidate(candidate_id: int, actor: Actor = Depends(current_actor),
                     db: Session = Depends(get_db)):
    candidate = db.query(models.JobCandidate).get(candidate_id)
    if not candidate:
        raise HTTPException(404, "候选不存在")
    _scope_candidate(candidate, actor)
    if candidate.status not in {"draft", "rejected"}:
        raise HTTPException(409, "只有草稿或被拒候选可提交")
    candidate.status = "submitted"
    add_audit(db, actor, "candidate.submit", "job_candidate", candidate.id,
              summary={"revision": candidate.current_revision, "status": "submitted"})
    db.commit()
    return _candidate_detail(db, candidate)
