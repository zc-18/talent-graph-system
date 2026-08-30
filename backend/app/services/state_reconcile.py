"""Governed correction of persisted JobSkill state from current evidence.

This is data repair, not temporal job evolution: it never creates a
CapabilityChange and never auto-promotes a candidate.  It refreshes derived
metrics for every status, demotes invalid active rows, revises only the current
version projection in place, and records one AuditLog repair manifest.
"""
from __future__ import annotations

import math
from copy import deepcopy
from datetime import datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import models
from . import confidence_batch, role_contract


MIN_EMPLOYERS = role_contract.MIN_EMPLOYERS
AUDIT_ACTION = "graph.repair.reconcile_job_skill"

# MySQL's JSON column does not round-trip a Python double bit-for-bit: writing
# 0.09523809523809523 and reading it back yields 0.09523809523809525. Comparing a freshly
# computed JSON value against a persisted one with `==` therefore reports drift that does
# not exist, which made every apply fail its own pre-commit verification even though
# source_count and confidence matched exactly. Confidence is already rounded to 4 decimals
# by the shared formula, so a relative tolerance far below that is still a strict gate.
# (Measured on the R6 shadow: this accounts for ~17 of 5647 rows the planner reports as
# stale. The rest are genuinely stale from recency decay since the last batch, not noise.)
JSON_REL_TOLERANCE = 1e-9
JSON_ABS_TOLERANCE = 1e-12


def json_equal(left, right) -> bool:
    """Compare persisted and recomputed JSON, tolerant of float round-trip only.

    Structure is compared exactly: key sets, list length and order, types and every
    non-numeric value must match. Only the final float comparison is given a tolerance,
    and bools are never treated as numbers.
    """
    if isinstance(left, bool) or isinstance(right, bool):
        return left is right
    if isinstance(left, dict) and isinstance(right, dict):
        return (set(left) == set(right)
                and all(json_equal(left[key], right[key]) for key in left))
    if isinstance(left, (list, tuple)) and isinstance(right, (list, tuple)):
        return (len(left) == len(right)
                and all(json_equal(a, b) for a, b in zip(left, right)))
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return math.isclose(left, right,
                            rel_tol=JSON_REL_TOLERANCE, abs_tol=JSON_ABS_TOLERANCE)
    return left == right


def factors_equal(left, right) -> bool:
    """Compare a persisted factor dict against a recomputed one."""
    return json_equal(left, right)


def _metrics_match(relation, item) -> bool:
    """True when a persisted relation already carries its recomputed derived metrics."""
    return (relation.source_count == item["source_count"]
            and relation.confidence == item["confidence"]
            and factors_equal(relation.factors, item["factors"]))


def _current_version(db: Session, job: models.Job):
    return (db.query(models.JobVersion)
            .filter(models.JobVersion.job_id == job.id,
                    models.JobVersion.version == (job.version or 1))
            .order_by(models.JobVersion.id.desc()).first())


def employer_chain_violations(db: Session) -> list[str]:
    """Refuse employer folding deeper than one level: two resolutions disagree.

    ``confidence_batch._employer_key`` — which produces ``job_skill.source_count``
    and therefore decides the >=2 independent employer gate — resolves exactly one
    ``parent_id`` hop, while ``role_contract.build_contract_from_version`` walks
    the whole chain to its root.  Given a grandchild employer the gate counts two
    independent employers where the contract counts one, so the relation stays
    ``active`` in the graph while the projection silently drops the capability.
    That is the fact/presentation split this repair exists to close, so it is a
    verification failure rather than something to reconcile around.
    """
    children = db.query(models.Employer).filter(
        models.Employer.parent_id.isnot(None)).all()
    if not children:
        return []
    parents = {row.id: row for row in db.query(models.Employer).filter(
        models.Employer.id.in_({row.parent_id for row in children})).all()}
    errors: list[str] = []
    for child in children:
        parent = parents.get(child.parent_id)
        if parent is None:
            errors.append(
                f"employer {child.id} 指向不存在的母公司 {child.parent_id}")
        elif parent.parent_id is not None:
            errors.append(
                f"employer {child.id} 的母公司 {parent.id} 自身仍有母公司："
                "雇主折叠超过一层，雇主计数与契约投影口径会分叉")
    return errors


def backup_projection(db: Session) -> dict:
    """Serialize every field the reconciler may mutate for exact restoration."""
    current_versions = [
        version for version, _job in (
            db.query(models.JobVersion, models.Job)
            .join(models.Job, models.Job.id == models.JobVersion.job_id)
            .filter(models.JobVersion.version
                    == func.coalesce(models.Job.version, 1))
            .order_by(models.JobVersion.id).all())]
    version_ids = {version.id for version in current_versions}
    return {
        "job_skill": [{
            "id": row.id, "job_id": row.job_id, "skill_id": row.skill_id,
            "status": row.status, "source_count": row.source_count,
            "confidence": row.confidence, "factors": row.factors,
        } for row in db.query(models.JobSkill).order_by(models.JobSkill.id)],
        "job": [{
            "id": row.id, "confidence": row.confidence,
            "evidence_count": row.evidence_count,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        } for row in db.query(models.Job).order_by(models.Job.id)],
        "current_versions": [{
            "id": row.id, "job_id": row.job_id, "version": row.version,
            "evidence_window": row.evidence_window,
            "contract_snapshot": row.contract_snapshot,
        } for row in current_versions],
        "current_version_skills": [{
            "id": row.id, "job_version_id": row.job_version_id,
            "skill_id": row.skill_id, "status": row.status,
            "confidence": row.confidence, "factors": row.factors,
            "evidence_refs": row.evidence_refs,
        } for row in (db.query(models.JobVersionSkill)
                      .filter(models.JobVersionSkill.job_version_id.in_(version_ids))
                      .order_by(models.JobVersionSkill.id).all())] if version_ids else [],
    }


def plan_all(db: Session, *, as_of: datetime,
             min_employers: int = MIN_EMPLOYERS) -> dict:
    """Read-only impact plan; no ORM attribute is changed and nothing is flushed."""
    stats = {
        "as_of": as_of.isoformat(), "jobs": 0, "relations": 0,
        "metrics_stale": 0, "active_below_gate": 0,
        "candidate_metrics_stale": 0, "deprecated_metrics_stale": 0,
        "candidate_eligible_but_not_promoted": 0,
        "version_skill_without_relation": 0,
        "employer_chain_violations": len(employer_chain_violations(db)),
    }
    jobs = db.query(models.Job).order_by(models.Job.id).all()
    stats["jobs"] = len(jobs)
    for job in jobs:
        state = confidence_batch.calculate_job_state(db, job, as_of)
        stats["relations"] += len(state["relations"])
        version = _current_version(db, job)
        if version is not None:
            # A version-skill row whose JobSkill is gone is never refreshed below,
            # yet build_contract_from_version still projects it. Report, never guess.
            skill_ids = {item["relation"].skill_id for item in state["relations"]}
            stats["version_skill_without_relation"] += sum(
                1 for row in db.query(models.JobVersionSkill.skill_id).filter(
                    models.JobVersionSkill.job_version_id == version.id).all()
                if row[0] not in skill_ids)
        for item in state["relations"]:
            relation = item["relation"]
            if not _metrics_match(relation, item):
                stats["metrics_stale"] += 1
                if relation.status == "candidate":
                    stats["candidate_metrics_stale"] += 1
                elif relation.status == "deprecated":
                    stats["deprecated_metrics_stale"] += 1
            if relation.status == "active" and item["source_count"] < min_employers:
                stats["active_below_gate"] += 1
            if relation.status == "candidate" and item["source_count"] >= min_employers:
                stats["candidate_eligible_but_not_promoted"] += 1
    return stats


def reconcile_all(db: Session, *, as_of: datetime, run_id: str,
                  min_employers: int = MIN_EMPLOYERS,
                  audit_action: str = AUDIT_ACTION,
                  audit_context: dict | None = None,
                  force_audit: bool = False) -> dict:
    """Stage a full target state. Caller verifies and owns commit/rollback."""
    stats = {
        "run_id": run_id, "as_of": as_of.isoformat(), "jobs": 0,
        "relations": 0, "metrics_refreshed": 0, "active_demoted": 0,
        "candidates_refreshed": 0, "deprecated_refreshed": 0,
        "versions_revised": 0, "snapshots_refreshed": 0,
        "auto_promoted": 0, "audit_created": False,
    }
    changed_any = False
    jobs = db.query(models.Job).order_by(models.Job.id).all()
    stats["jobs"] = len(jobs)
    for job in jobs:
        state = confidence_batch.calculate_job_state(db, job, as_of)
        stats["relations"] += len(state["relations"])
        for item in state["relations"]:
            relation = item["relation"]
            # Decide staleness before overwriting, and tolerantly: a bit-level JSON float
            # difference is not a refresh, and counting it as one would make the audit
            # manifest claim thousands of corrections that never happened.
            metrics_were_stale = not _metrics_match(relation, item)
            previous_status = relation.status
            relation.source_count = item["source_count"]
            relation.factors = item["factors"]
            relation.confidence = item["confidence"]
            if relation.status == "active" and relation.source_count < min_employers:
                relation.status = "candidate"
                stats["active_demoted"] += 1
            if metrics_were_stale or relation.status != previous_status:
                changed_any = True
                stats["metrics_refreshed"] += 1
                if previous_status == "candidate":
                    stats["candidates_refreshed"] += 1
                elif previous_status == "deprecated":
                    stats["deprecated_refreshed"] += 1

        summary = confidence_batch.summarize_job_state(state)
        old_job = (job.confidence, job.evidence_count)
        job.confidence = summary["confidence"]
        job.evidence_count = summary["evidence_count"]
        if old_job != (job.confidence, job.evidence_count):
            changed_any = True

        version = _current_version(db, job)
        if not version:
            continue
        snapshot_by_skill = {
            row.skill_id: row for row in db.query(models.JobVersionSkill).filter(
                models.JobVersionSkill.job_version_id == version.id).all()}
        version_changed = False
        for item in state["relations"]:
            relation = item["relation"]
            snapshot = snapshot_by_skill.get(relation.skill_id)
            if snapshot is None:
                continue
            refs = item["evidence_refs"][:12]
            snapshot_was_stale = (
                snapshot.status != relation.status
                or snapshot.confidence != relation.confidence
                or not json_equal(snapshot.evidence_refs, refs)
                or not json_equal(snapshot.factors, relation.factors))
            snapshot.status = relation.status
            snapshot.confidence = relation.confidence
            snapshot.factors = relation.factors
            snapshot.evidence_refs = refs
            if snapshot_was_stale:
                version_changed = True
                stats["snapshots_refreshed"] += 1
        db.flush()
        old_window = deepcopy(version.evidence_window)
        old_contract = deepcopy(version.contract_snapshot)
        version.contract_snapshot = role_contract.build_contract_from_version(db, job, version)
        version.evidence_window = deepcopy(version.evidence_window)
        if (version_changed or not json_equal(old_window, version.evidence_window)
                or not json_equal(old_contract, version.contract_snapshot)):
            changed_any = True
            stats["versions_revised"] += 1

    db.flush()
    if changed_any or force_audit:
        db.add(models.AuditLog(
            action=audit_action, target_type="database_shadow", target_id=run_id,
            result="success", summary={
                **stats, "context": audit_context or {},
                "policy": {
                    "min_employers": min_employers,
                    "candidate_auto_promotion": False,
                    "version_strategy": "revise_current_version_projection_in_place",
                    "capability_change_created": False,
                },
            }))
        stats["audit_created"] = True
        db.flush()
    return stats


def verify_all(db: Session, *, as_of: datetime,
               min_employers: int = MIN_EMPLOYERS) -> list[str]:
    """Return target-state violations; an empty list is commit permission."""
    errors: list[str] = employer_chain_violations(db)
    for job in db.query(models.Job).order_by(models.Job.id).all():
        state = confidence_batch.calculate_job_state(db, job, as_of)
        for item in state["relations"]:
            relation = item["relation"]
            if not _metrics_match(relation, item):
                errors.append(f"job_skill {relation.id} 派生字段不一致")
            if relation.status == "active" and item["source_count"] < min_employers:
                errors.append(
                    f"job_skill {relation.id} active 但独立雇主={item['source_count']}")

        summary = confidence_batch.summarize_job_state(state)
        if (job.confidence, job.evidence_count) != (
                summary["confidence"], summary["evidence_count"]):
            errors.append(f"job {job.id} 汇总字段不一致")

        version = _current_version(db, job)
        if not version:
            continue
        snapshots = {row.skill_id: row for row in db.query(models.JobVersionSkill).filter(
            models.JobVersionSkill.job_version_id == version.id).all()}
        for item in state["relations"]:
            relation = item["relation"]
            snapshot = snapshots.get(relation.skill_id)
            if not snapshot:
                continue
            if (snapshot.status != relation.status
                    or snapshot.confidence != relation.confidence
                    or not json_equal(snapshot.evidence_refs, item["evidence_refs"][:12])
                    or not json_equal(snapshot.factors, relation.factors)):
                errors.append(
                    f"job_version_skill {snapshot.id} 与当前关系投影不一致")
        old_window = deepcopy(version.evidence_window)
        expected_contract = role_contract.build_contract_from_version(db, job, version)
        # build_contract_from_version writes the recalculated window onto the row.
        # A verification pass must report drift without becoming a writer itself,
        # otherwise a caller that verifies and then commits for another reason
        # silently republishes the window it only meant to inspect.
        if not json_equal(old_window, version.evidence_window):
            version.evidence_window = old_window
            errors.append(f"job_version {version.id} evidence_window 不一致")
        if not json_equal(version.contract_snapshot, expected_contract):
            errors.append(f"job_version {version.id} contract_snapshot 不一致")
    return errors
