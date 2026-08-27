"""Build reproducible release evidence from the shadow, tests, API and HBuilderX cloud record."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen


def _write(root: Path, name: str, run_id: str, body: dict) -> None:
    (root / name).write_text(json.dumps({"run_id": run_id, **body}, ensure_ascii=False, indent=2),
                             encoding="utf-8")


def _junit(path: Path) -> dict:
    root = ET.parse(path).getroot()
    cases = root.findall(".//testcase")
    failed = [case.get("name") for case in cases
              if case.find("failure") is not None or case.find("error") is not None]
    skipped = [case.get("name") for case in cases if case.find("skipped") is not None]
    return {"tests": len(cases), "failed": failed, "skipped": skipped,
            "passed": len(cases) - len(failed) - len(skipped),
            "names": [case.get("name") for case in cases]}


def _percentile(values: list[float], percentile: float) -> float:
    values = sorted(values)
    return values[min(len(values) - 1, max(0, int(len(values) * percentile + .999999) - 1))]


def _request(url: str, body: dict | None = None) -> float:
    encoded = json.dumps(body).encode("utf-8") if body is not None else None
    request = Request(url, data=encoded,
                      headers={"Content-Type": "application/json"} if encoded else {})
    started = time.perf_counter()
    with urlopen(request, timeout=45) as response:
        if response.status >= 400:
            raise RuntimeError(f"HTTP {response.status}: {url}")
        response.read()
    return (time.perf_counter() - started) * 1000


def _performance(api_base: str) -> dict:
    get_samples = [_request(f"{api_base}/jobs/1") for _ in range(30)]
    match_body = {"job_id": 1, "skills": ["Java", "Spring Boot", "MySQL", "Redis"],
                  "skill_levels": {"Java": "proficient"},
                  "generate_suggestions": False, "save": False}
    match_samples = [_request(f"{api_base}/match/analyze", match_body) for _ in range(5)]
    return {
        "ordinary_get_samples": len(get_samples),
        "ordinary_get_p50_ms": round(statistics.median(get_samples), 2),
        "ordinary_get_p95_ms": round(_percentile(get_samples, .95), 2),
        "single_match_samples": len(match_samples),
        "single_match_p50_ms": round(statistics.median(match_samples), 2),
        "single_match_p95_ms": round(_percentile(match_samples, .95), 2),
    }


def _market_evidence(raw_root: Path, term: str, limit: int = 12) -> list[dict]:
    evidence, employers = [], set()
    for path in sorted(raw_root.rglob("*.jsonl")):
        with path.open(encoding="utf-8", errors="ignore") as handle:
            for line_number, line in enumerate(handle, 1):
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                blob = f"{row.get('job_title', '')} {row.get('raw_text', '')}"
                company = str(row.get("company") or "").strip()
                if term not in blob or not company or company in employers:
                    continue
                employers.add(company)
                evidence.append({
                    "title": row.get("job_title"), "content": row.get("raw_text", "")[:1200],
                    "url": row.get("url"), "provider": row.get("platform"),
                    "company": company, "location": row.get("location"),
                    "industry": (row.get("extra") or {}).get("category"),
                    "source_file": str(path.relative_to(raw_root.parent)),
                    "source_line": line_number,
                })
                if len(evidence) >= limit:
                    return evidence
    return evidence


def _hbuilderx_cloud_evidence(record_path: Path | None, repo_root: Path) -> tuple[dict, dict]:
    empty = {
        "uni_app_https_api_configured": False,
        "hbuilderx_cloud_package_passed": False,
        "hbuilderx_cloud_provider": None,
        "hbuilderx_cloud_build_id": None,
        "cloud_package_signed": False,
        "cloud_package_path": None,
        "cloud_package_sha256": None,
        "cloud_package_size": 0,
    }
    if record_path is None or not record_path.is_file():
        return empty, {"present": False, "reason": "HBuilderX cloud record not supplied"}
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return empty, {"present": False, "reason": f"invalid HBuilderX cloud record: {exc}"}
    if not isinstance(record, dict):
        return empty, {"present": False, "reason": "HBuilderX cloud record must be an object"}

    raw_package = record.get("package_path")
    package_path = None
    if isinstance(raw_package, str) and not Path(raw_package).is_absolute():
        candidate = (repo_root / raw_package).resolve()
        try:
            candidate.relative_to(repo_root.resolve())
            package_path = candidate
        except ValueError:
            pass
    package_exists = bool(package_path and package_path.is_file() and package_path.stat().st_size > 0)
    api_origin = str(record.get("api_origin") or "")
    parsed_api = urlparse(api_origin)
    https_ok = (parsed_api.scheme == "https" and bool(parsed_api.hostname) and
                not (parsed_api.hostname or "").endswith((".example", ".invalid", ".test")))
    provider = record.get("provider")
    build_id = str(record.get("cloud_build_id") or "").strip()
    signed = record.get("package_signed") is True
    cloud_ok = (provider == "HBuilderX" and record.get("status") == "success" and
                bool(build_id) and signed and package_exists)
    summary = {
        "uni_app_https_api_configured": https_ok,
        "hbuilderx_cloud_package_passed": cloud_ok,
        "hbuilderx_cloud_provider": provider,
        "hbuilderx_cloud_build_id": build_id or None,
        "cloud_package_signed": signed,
        "cloud_package_path": raw_package,
        "cloud_package_sha256": hashlib.sha256(package_path.read_bytes()).hexdigest()
        if package_exists and package_path else None,
        "cloud_package_size": package_path.stat().st_size if package_exists and package_path else 0,
    }
    return summary, {"present": True, **record, "package_exists": package_exists,
                     "package_within_repo": package_path is not None}


def _uni_app_core_e2e(artifacts: Path, run_id: str) -> tuple[bool, dict]:
    """Keep local H5 acceptance independent from cloud packaging evidence."""
    e2e_path = artifacts / "eval_e2e_result.json"
    build_log_path = artifacts / "uniapp-h5-build.log"
    try:
        e2e = json.loads(e2e_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        e2e = None
    scenarios = e2e.get("scenarios") if isinstance(e2e, dict) else None
    scenario = next((item for item in scenarios or []
                     if isinstance(item, dict)
                     and item.get("name") == "uni_app_match_history"), None)
    try:
        build_log = build_log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        build_log = ""
    same_run = isinstance(e2e, dict) and e2e.get("run_id") == run_id
    scenario_passed = isinstance(scenario, dict) and scenario.get("passed") is True
    build_passed = "DONE  Build complete" in build_log or "DONE Build complete" in build_log
    passed = bool(same_run and scenario_passed and build_passed)
    return passed, {
        "same_run": same_run,
        "scenario": "uni_app_match_history",
        "scenario_passed": scenario_passed,
        "production_build_passed": build_passed,
        "e2e_artifact": e2e_path.name,
        "build_log": build_log_path.name,
    }


def _valid_evidence_url(value: str | None) -> bool:
    try:
        parsed = urlparse(value or "")
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or not host:
        return False
    return not (host in {"localhost", "127.0.0.1", "::1"} or
                host.endswith((".invalid", ".test", ".example")))


def _placeholder_evidence_url(value: str | None) -> bool:
    try:
        host = (urlparse(value or "").hostname or "").lower()
    except ValueError:
        return True
    return bool(host) and (host in {"localhost", "127.0.0.1", "::1"} or
                           host.endswith((".invalid", ".test", ".example")))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--full-junit", type=Path, required=True)
    parser.add_argument("--security-junit", type=Path, required=True)
    parser.add_argument("--coverage", type=Path, required=True)
    parser.add_argument("--frontend-log", type=Path, required=True)
    parser.add_argument("--hbuilderx-cloud-record", type=Path)
    parser.add_argument("--api-base", default="http://127.0.0.1:8200/api")
    parser.add_argument("--data-origin", choices=("unspecified", "test", "demo",
                                                   "production_shadow_snapshot"),
                        default="unspecified")
    args = parser.parse_args()
    database, artifacts = args.database.resolve(), args.artifacts.resolve()
    artifacts.mkdir(parents=True, exist_ok=True)
    os.environ["DATABASE_URL_OVERRIDE"] = f"sqlite:///{database.as_posix()}"
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

    from app import models
    from app.db import SessionLocal
    from app.services.discovery import authority_matches, score_emergence
    from app.services.job_resolution import resolve_job_query
    from app.services.quality_eval import evaluate_contract
    from app.services.role_contract import build_contract_from_job

    session = SessionLocal()
    golden = json.loads((Path(__file__).resolve().parents[1] / "tests" / "fixtures" /
                         "job_profile_golden.json").read_text(encoding="utf-8"))
    jobs = {job.name: job for job in session.query(models.Job).filter(
        models.Job.status == "published").all()}
    all_contracts = []
    for job in jobs.values():
        contract = build_contract_from_job(session, job)
        all_contracts.append({"job_id": job.id, "job_name": job.name,
                              "version": job.version, "status": contract["status"],
                              "cluster_count": len(contract["clusters"])})
    golden_rows = []
    expected_profile_ids = [profile["id"] for profile in golden["profiles"]]
    missing_profile_ids = []
    for profile in golden["profiles"]:
        job = jobs.get(profile["canonical_title"])
        if not job:
            missing_profile_ids.append(profile["id"])
            continue
        contract = build_contract_from_job(
            session, job, seniority=profile["seniority"],
            recruitment_type=profile["recruitment_type"], track=profile["track"],
            industry=profile["industry"])
        result = evaluate_contract(contract, profile)
        golden_rows.append({"profile_id": profile["id"], "job_id": job.id,
                            "job_name": job.name, "version": job.version,
                            "contract_status": contract["status"],
                            "cluster_count": len(contract["clusters"]), **result})
    precision = round(sum(row["precision_at_10"] for row in golden_rows) /
                      max(1, len(golden_rows)), 4)
    failed_profile_ids = [row["profile_id"] for row in golden_rows if not row["passed"]]
    golden_pass_count = len(golden_rows) - len(failed_profile_ids)
    _write(artifacts, "eval_role_contract_result.json", args.run_id, {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "job_count": len(golden_rows), "published_job_count": len(all_contracts),
            "golden_expected_profile_count": len(expected_profile_ids),
            "golden_expected_profile_ids": expected_profile_ids,
            "golden_profile_count": len(golden_rows),
            "golden_pass_count": golden_pass_count,
            "golden_fail_count": len(failed_profile_ids),
            "failed_profile_ids": failed_profile_ids,
            "missing_golden_profile_ids": missing_profile_ids,
            "precision_at_10": precision,
            "min_core_clusters": min(row["cluster_count"] for row in all_contracts),
            "max_core_clusters": max(row["cluster_count"] for row in all_contracts),
            "forbidden_skill_hits": sum(len(row["prohibited_violations"]) for row in golden_rows),
            "expert_signoff_status": "pending_named_experts",
            "unique_job_count": len({row["job_name"] for row in golden_rows}),
            "expert_reviewer_count": 0,
            "signed_job_count": 0,
            "conflicts_arbitrated": False,
            "required_signoff_policy": golden["annotation_policy"],
        },
        "contracts": all_contracts, "golden_profiles": golden_rows,
        "missing_golden_jobs": sorted({p["canonical_title"] for p in golden["profiles"]} - set(jobs)),
    })

    mature_queries = ["Java", "初级Java开发工程师", "后端开发工程师", "软件测试工程师",
                      "自动化测试工程师", "云计算工程师", "AI Agent工程师",
                      "大模型算法工程师", "提示词工程师"]
    emergence_cases = []
    for query in mature_queries:
        resolution = resolve_job_query(query)
        predicted = "ESTABLISHED" if resolution.is_established else "OTHER"
        emergence_cases.append({"query": query, "expected": "ESTABLISHED",
                                "predicted": predicted, "passed": predicted == "ESTABLISHED"})
    ambiguous = resolve_job_query("测试工程师")
    emergence_cases.append({"query": "测试工程师", "expected": "AMBIGUOUS",
                            "predicted": "AMBIGUOUS" if ambiguous.requires_disambiguation else "OTHER",
                            "passed": ambiguous.requires_disambiguation})
    market = _market_evidence(Path(__file__).resolve().parent / "raw", "具身智能")
    scored = score_emergence("具身智能工程师", authority_matches("具身智能") + market,
                             history={"2018": 0, "2024": 0, "2026": len(market)})
    emergence_cases.append({"query": "具身智能工程师", "expected": "EMERGING",
                            "predicted": scored["verdict"], "score": scored["emergence_score"],
                            "market_evidence": market, "passed": scored["verdict"] == "EMERGING"})
    correct = sum(case["passed"] for case in emergence_cases)
    _write(artifacts, "eval_emergence_result.json", args.run_id, {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {"n": len(emergence_cases), "accuracy": round(correct / len(emergence_cases), 4),
                    "human_labeled_count": 0,
                    "annotation_method": "engineering_dictionary_and_curated_market_case",
                    "mature_false_positive": sum(case["expected"] == "ESTABLISHED" and
                                                  case["predicted"] == "EMERGING"
                                                  for case in emergence_cases)},
        "cases": emergence_cases,
    })

    active_core = session.query(models.JobSkill).filter(
        models.JobSkill.status == "active", models.JobSkill.importance == "required").all()
    all_evidence = session.query(models.Evidence).filter(
        models.Evidence.job_skill_id.in_({row.id for row in active_core})).all()
    by_relation: dict[int, list] = {}
    for evidence in all_evidence:
        by_relation.setdefault(evidence.job_skill_id, []).append(evidence)
    raw_ids = {evidence.raw_jd_id for evidence in all_evidence if evidence.raw_jd_id is not None}
    raw_rows = {row.id: row for row in session.query(models.RawJD).filter(
        models.RawJD.id.in_(raw_ids)).all()} if raw_ids else {}
    employer_ids = {row.employer_id for row in raw_rows.values() if row.employer_id is not None}
    employers = {row.id: row for row in session.query(models.Employer).filter(
        models.Employer.id.in_(employer_ids)).all()} if employer_ids else {}
    trace_rows = []
    for relation in active_core:
        evidences = by_relation.get(relation.id, [])
        urls = [evidence.source_url or getattr(raw_rows.get(evidence.raw_jd_id), "source_url", None)
                for evidence in evidences]
        employer_keys = set()
        for evidence in evidences:
            raw = raw_rows.get(evidence.raw_jd_id)
            employer = employers.get(raw.employer_id) if raw and raw.employer_id else None
            if employer:
                employer_keys.add(employer.parent_id or employer.id)
        trace_rows.append({
            "job_skill_id": relation.id, "job_id": relation.job_id,
            "skill_id": relation.skill_id, "evidenced": bool(evidences),
            "evidence_count": len(evidences),
            "valid_url_count": sum(_valid_evidence_url(url) for url in urls),
            "placeholder_url_count": sum(_placeholder_evidence_url(url) for url in urls),
            "employer_count": len(employer_keys),
            "employer_validated": len(employer_keys) >= 2,
        })
    _write(artifacts, "eval_traceability_result.json", args.run_id, {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {"active_core_total": len(trace_rows),
                    "evidenced_core_total": sum(row["evidenced"] for row in trace_rows),
                    "valid_url_core_total": sum(row["valid_url_count"] > 0 for row in trace_rows),
                    "employer_validated_core_total": sum(row["employer_validated"] for row in trace_rows),
                    "placeholder_url_count": sum(row["placeholder_url_count"] for row in trace_rows),
                    "data_origin": args.data_origin},
        "items": trace_rows,
    })
    session.close()

    security = _junit(args.security_junit.resolve())
    security_ok = not security["failed"] and not security["skipped"]
    _write(artifacts, "eval_security_result.json", args.run_id, {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {"scope_case_count": security["tests"],
                    "cross_scope_successes": 0 if security_ok else 1,
                    "sensitive_log_leaks": 0 if security_ok else 1,
                    "read_only_private_append_passed": security_ok},
        "junit": security,
    })

    full = _junit(args.full_junit.resolve())
    batch_nonblocking_cases = [name for name in full["names"] if name in {
        "test_hr_private_batch_writes_in_read_only_and_is_org_scoped",
    }]
    batch_nonblocking = bool(batch_nonblocking_cases) and not any(
        name in set(full["failed"]) | set(full["skipped"])
        for name in batch_nonblocking_cases)
    coverage = json.loads(args.coverage.resolve().read_text(encoding="utf-8"))
    totals = coverage["totals"]
    performance = _performance(args.api_base.rstrip("/"))
    frontend_log = args.frontend_log.resolve().read_text(encoding="utf-8", errors="replace")
    repo_root = Path(__file__).resolve().parents[2]
    cloud_summary, cloud_record = _hbuilderx_cloud_evidence(
        args.hbuilderx_cloud_record.resolve() if args.hbuilderx_cloud_record else None,
        repo_root,
    )
    uni_app_core_passed, uni_app_core_evidence = _uni_app_core_e2e(
        artifacts, args.run_id)
    summary = {
        "tests_passed": full["passed"], "tests_failed": len(full["failed"]),
        "coverage_percent": round(float(totals["percent_covered"]), 4),
        "frontend_build_passed": "built in" in frontend_log,
        **performance, "batch_nonblocking": batch_nonblocking,
        "batch_nonblocking_case_count": len(batch_nonblocking_cases),
        "uni_app_core_e2e_passed": uni_app_core_passed,
        **cloud_summary,
    }
    _write(artifacts, "eval_engineering_result.json", args.run_id, {
        "generated_at": datetime.now(timezone.utc).isoformat(), "summary": summary,
        "full_junit": full,
        "uni_app_core_e2e_evidence": uni_app_core_evidence,
        "hbuilderx_cloud_record": cloud_record,
    })

    required = ["eval_jd_result.json", "eval_resume_result.json", "eval_match_result.json",
                "eval_role_contract_result.json", "eval_emergence_result.json",
                "eval_hr_ranking_result.json", "eval_traceability_result.json",
                "eval_security_result.json", "eval_e2e_result.json",
                "eval_engineering_result.json", "eval_migration_result.json"]
    artifact_sha256 = {
        name: hashlib.sha256((artifacts / name).read_bytes()).hexdigest()
        for name in required
    }
    manifest = {"run_id": args.run_id, "generated_at": datetime.now(timezone.utc).isoformat(),
                "artifacts": required, "artifact_sha256": artifact_sha256,
                "legacy_metrics_excluded": True}
    (artifacts / "release_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
