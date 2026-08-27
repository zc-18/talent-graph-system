"""Fourth-round release gate.

The gate consumes artifacts from one evaluation run and refuses stale, partial, or
cross-run results. It never connects to a database and is therefore safe to run in
CI or against a production checkout.

Usage (from ``backend``)::

    uv run python data/release_gate.py --artifacts artifacts/release/latest
"""
from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


REQUIRED_E2E_SCENARIOS = {
    "existing_job_evolution_publish",
    "emerging_job_discovery_publish",
    "personal_resume_match_history",
    "hr_batch_topk_team",
    "feedback_review_apply",
    "read_only_public_private_boundaries",
    "uni_app_match_history",
}
REQUIRED_SECURITY_TESTS = {
    "test_read_only_blocks_authenticated_admin_public_write",
    "test_cross_user_history_is_hidden",
    "test_hr_private_batch_writes_in_read_only_and_is_org_scoped",
    "test_feedback_cross_owner_and_audit_redaction",
}


@dataclass(frozen=True)
class Check:
    key: str
    passed: bool
    actual: Any
    expected: str
    evidence: str


def _read(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _value(data: dict[str, Any] | None, dotted: str) -> Any:
    current: Any = data
    for part in dotted.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _repo_artifact(repo_root: Path, value: Any) -> Path | None:
    if not isinstance(value, str) or Path(value).is_absolute():
        return None
    candidate = (repo_root / value).resolve()
    try:
        candidate.relative_to(repo_root.resolve())
    except ValueError:
        return None
    return candidate


def _numeric(value: Any, predicate: Callable[[float], bool]) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and predicate(float(value))


def _minimum(key: str, data: dict[str, Any] | None, field: str, threshold: float,
             evidence: str) -> Check:
    actual = _value(data, field)
    return Check(key, _numeric(actual, lambda x: x >= threshold), actual,
                 f">= {threshold:g}", evidence)


def _maximum(key: str, data: dict[str, Any] | None, field: str, threshold: float,
             evidence: str) -> Check:
    actual = _value(data, field)
    return Check(key, _numeric(actual, lambda x: x <= threshold), actual,
                 f"<= {threshold:g}", evidence)


def _equals(key: str, data: dict[str, Any] | None, field: str, expected: Any,
            evidence: str) -> Check:
    actual = _value(data, field)
    return Check(key, actual == expected, actual, repr(expected), evidence)


def _all_group_metrics(data: dict[str, Any] | None, field: str, metric: str,
                       threshold: float,
                       required_groups: set[str] | None = None) -> tuple[bool, Any]:
    groups = _value(data, field)
    if not isinstance(groups, dict) or not groups:
        return False, groups
    values = {name: item.get(metric) if isinstance(item, dict) else None
              for name, item in groups.items()}
    selected = required_groups or set(values)
    return (selected.issubset(values) and
            all(_numeric(values[name], lambda x: x >= threshold) for name in selected)), values


def _sha256(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _load_run(artifacts: Path) -> tuple[dict[str, Any] | None, dict[str, dict[str, Any] | None]]:
    manifest = _read(artifacts / "release_manifest.json")
    names = [
        "eval_jd_result.json", "eval_resume_result.json", "eval_match_result.json",
        "eval_role_contract_result.json", "eval_emergence_result.json",
        "eval_hr_ranking_result.json", "eval_traceability_result.json",
        "eval_security_result.json", "eval_e2e_result.json",
        "eval_engineering_result.json", "eval_migration_result.json",
    ]
    return manifest, {name: _read(artifacts / name) for name in names}


def evaluate_release(artifacts: Path, repo_root: Path) -> dict[str, Any]:
    """Evaluate all hard gates and return a machine-readable report."""
    manifest, docs = _load_run(artifacts)
    checks: list[Check] = []
    run_id = _value(manifest, "run_id")
    declared = _value(manifest, "artifacts")
    expected_files = set(docs)
    checks.append(Check("single_run_manifest", isinstance(run_id, str) and bool(run_id.strip()),
                        run_id, "non-empty run_id", "release_manifest.json"))
    checks.append(Check("all_artifacts_declared", isinstance(declared, list) and
                        expected_files.issubset(set(declared or [])), declared,
                        "all required result files", "release_manifest.json"))
    declared_hashes = _value(manifest, "artifact_sha256")
    hash_evidence = {}
    for name in sorted(expected_files):
        actual_hash = _sha256(artifacts / name)
        declared_hash = declared_hashes.get(name) if isinstance(declared_hashes, dict) else None
        hash_evidence[name] = {
            "declared": declared_hash,
            "actual": actual_hash,
            "matches": isinstance(declared_hash, str) and declared_hash == actual_hash,
        }
    checks.append(Check(
        "artifact_hashes_verified",
        bool(hash_evidence) and all(item["matches"] for item in hash_evidence.values()),
        hash_evidence,
        "SHA-256 for every required artifact matches the sealed manifest",
        "release_manifest.json",
    ))
    checks.append(_equals("legacy_metrics_excluded", manifest, "legacy_metrics_excluded", True,
                          "release_manifest.json"))
    for name, doc in docs.items():
        checks.append(Check(f"artifact_present:{name}", doc is not None, doc is not None,
                            "True", name))
        checks.append(Check(f"same_run:{name}", bool(run_id) and _value(doc, "run_id") == run_id,
                            _value(doc, "run_id"), repr(run_id), name))

    jd = docs["eval_jd_result.json"]
    checks.extend([
        _minimum("jd_real_holdout_size", jd, "summary.n", 100, "eval_jd_result.json"),
        _minimum("jd_f1", jd, "summary.f1", 0.90, "eval_jd_result.json"),
        _equals("jd_truth_is_independent", jd, "summary.truth_independent", True,
                "eval_jd_result.json"),
        _equals("jd_annotation_complete", jd, "summary.annotation_complete", True,
                "eval_jd_result.json"),
        _minimum("jd_human_annotated_count", jd, "summary.human_annotated_count", 100,
                 "eval_jd_result.json"),
        _minimum("jd_dual_annotated_count", jd, "summary.dual_annotated_count", 100,
                 "eval_jd_result.json"),
        _equals("jd_conflicts_adjudicated", jd, "summary.conflicts_adjudicated", True,
                "eval_jd_result.json"),
    ])
    jd_dimensions = _value(jd, "summary.stratification_dimensions")
    required_jd_dimensions = {"domain", "job", "seniority", "recruitment_type"}
    checks.append(Check(
        "jd_required_stratification",
        isinstance(jd_dimensions, list) and required_jd_dimensions.issubset(set(jd_dimensions)),
        jd_dimensions,
        "domain/job/seniority/recruitment_type represented",
        "eval_jd_result.json",
    ))
    ok, actual = _all_group_metrics(jd, "by_domain", "f1", 0.85)
    checks.append(Check("jd_each_domain_f1", ok, actual, "every domain F1 >= 0.85",
                        "eval_jd_result.json"))

    resume = docs["eval_resume_result.json"]
    checks.extend([
        _minimum("resume_holdout_size", resume, "summary.n", 100, "eval_resume_result.json"),
        _minimum("resume_real_file_count", resume, "summary.real_file_count", 100,
                 "eval_resume_result.json"),
        _minimum("resume_original_file_count", resume, "summary.original_file_count", 100,
                 "eval_resume_result.json"),
        _minimum("resume_human_annotated_count", resume, "summary.human_annotated_count", 100,
                 "eval_resume_result.json"),
        _minimum("resume_f1", resume, "summary.f1", 0.90, "eval_resume_result.json"),
        _equals("resume_annotation_complete", resume, "summary.annotation_complete", True,
                "eval_resume_result.json"),
    ])
    ok, actual = _all_group_metrics(
        resume, "by_format", "f1", 0.90, required_groups={"pdf", "docx"})
    checks.append(Check("resume_each_supported_format_f1", ok, actual,
                        "PDF and DOCX F1 >= 0.90", "eval_resume_result.json"))
    resume_formats = _value(resume, "by_format")
    required_resume_formats = {"pdf", "docx", "txt"}
    checks.append(Check(
        "resume_required_formats_present",
        isinstance(resume_formats, dict) and required_resume_formats.issubset(resume_formats) and
        all(_numeric(_value(resume_formats, f"{name}.n"), lambda x: x > 0)
            for name in required_resume_formats),
        {name: _value(resume_formats, f"{name}.n") for name in required_resume_formats},
        "non-empty original PDF, DOCX and TXT subsets",
        "eval_resume_result.json",
    ))
    diagnostics = _value(resume, "diagnostics")
    required_diagnostics = {"scanned_pdf", "legacy_doc", "encrypted", "empty", "corrupt", "oversized"}
    checks.append(Check("resume_error_diagnostics", isinstance(diagnostics, dict) and
                        required_diagnostics.issubset(diagnostics) and
                        all(diagnostics[name].get("passed") is True for name in required_diagnostics),
                        diagnostics, "six explicit failure classes pass", "eval_resume_result.json"))

    match = docs["eval_match_result.json"]
    checks.extend([
        _minimum("match_independent_pairs", match, "summary.n", 100, "eval_match_result.json"),
        _minimum("match_accuracy", match, "summary.classification_acc", 0.90,
                 "eval_match_result.json"),
        _minimum("match_macro_f1", match, "summary.macro_f1", 0.90, "eval_match_result.json"),
        _equals("match_truth_is_independent", match, "summary.truth_independent", True,
                "eval_match_result.json"),
        _minimum("match_human_annotated_pairs", match, "summary.human_annotated_count", 100,
                 "eval_match_result.json"),
        _minimum("match_dual_reviewed_pairs", match, "summary.dual_reviewed_count", 100,
                 "eval_match_result.json"),
        _equals("match_conflicts_adjudicated", match, "summary.conflicts_adjudicated", True,
                "eval_match_result.json"),
    ])

    contract = docs["eval_role_contract_result.json"]
    golden_fixture = _read(
        repo_root / "backend" / "tests" / "fixtures" / "job_profile_golden.json")
    fixture_profiles = _value(golden_fixture, "profiles")
    fixture_profile_ids = [row.get("id") for row in fixture_profiles
                           if isinstance(row, dict)] if isinstance(fixture_profiles, list) else []
    golden_profiles = _value(contract, "golden_profiles")
    actual_golden_profiles = golden_profiles if isinstance(golden_profiles, list) else []
    actual_failed_profile_ids = [
        row.get("profile_id") for row in actual_golden_profiles
        if not isinstance(row, dict) or row.get("passed") is not True
    ]
    actual_profile_ids = [row.get("profile_id") for row in actual_golden_profiles
                          if isinstance(row, dict)]
    expected_profile_ids = _value(contract, "summary.golden_expected_profile_ids")
    expected_profile_ids = expected_profile_ids if isinstance(expected_profile_ids, list) else []
    missing_profile_ids = _value(contract, "summary.missing_golden_profile_ids")
    missing_golden_jobs = _value(contract, "missing_golden_jobs")
    golden_summary = {
        "expected_profile_count": _value(contract, "summary.golden_expected_profile_count"),
        "expected_profile_ids": expected_profile_ids,
        "fixture_profile_ids": fixture_profile_ids,
        "profile_count": _value(contract, "summary.golden_profile_count"),
        "pass_count": _value(contract, "summary.golden_pass_count"),
        "fail_count": _value(contract, "summary.golden_fail_count"),
        "failed_profile_ids": _value(contract, "summary.failed_profile_ids"),
        "actual_profile_count": len(actual_golden_profiles),
        "actual_profile_ids": actual_profile_ids,
        "actual_failed_profile_ids": actual_failed_profile_ids,
        "missing_profile_ids": missing_profile_ids,
        "missing_golden_jobs": missing_golden_jobs,
    }
    golden_all_pass = (
        isinstance(golden_profiles, list)
        and len(golden_profiles) >= 10
        and len(expected_profile_ids) >= 10
        and len(fixture_profile_ids) >= 10
        and len(set(expected_profile_ids)) == len(expected_profile_ids)
        and len(set(fixture_profile_ids)) == len(fixture_profile_ids)
        and len(set(actual_profile_ids)) == len(actual_profile_ids)
        and set(actual_profile_ids) == set(expected_profile_ids) == set(fixture_profile_ids)
        and not actual_failed_profile_ids
        and missing_profile_ids == []
        and missing_golden_jobs == []
        and golden_summary["expected_profile_count"] == len(expected_profile_ids)
        and golden_summary["profile_count"] == len(golden_profiles)
        and golden_summary["pass_count"] == len(golden_profiles)
        and golden_summary["fail_count"] == 0
        and golden_summary["failed_profile_ids"] == []
    )
    checks.extend([
        _minimum("golden_job_count", contract, "summary.job_count", 10,
                 "eval_role_contract_result.json"),
        _minimum("golden_unique_job_count", contract, "summary.unique_job_count", 10,
                 "eval_role_contract_result.json"),
        _minimum("contract_precision_at_10", contract, "summary.precision_at_10", 0.90,
                 "eval_role_contract_result.json"),
        Check("contract_all_golden_profiles_pass", golden_all_pass, golden_summary,
              "at least 10 evaluated golden profiles and every profile passes",
              "eval_role_contract_result.json"),
        _minimum("contract_min_core_clusters", contract, "summary.min_core_clusters", 8,
                 "eval_role_contract_result.json"),
        _maximum("contract_max_core_clusters", contract, "summary.max_core_clusters", 12,
                 "eval_role_contract_result.json"),
        _equals("contract_forbidden_skill_hits", contract, "summary.forbidden_skill_hits", 0,
                "eval_role_contract_result.json"),
        _equals("contract_named_expert_signoff", contract, "summary.expert_signoff_status", "signed",
                "eval_role_contract_result.json"),
        _minimum("contract_expert_reviewer_count", contract, "summary.expert_reviewer_count", 2,
                 "eval_role_contract_result.json"),
        _minimum("contract_signed_job_count", contract, "summary.signed_job_count", 10,
                 "eval_role_contract_result.json"),
        _equals("contract_conflicts_arbitrated", contract,
                "summary.conflicts_arbitrated", True, "eval_role_contract_result.json"),
    ])

    emergence = docs["eval_emergence_result.json"]
    checks.extend([
        _minimum("emergence_case_count", emergence, "summary.n", 10,
                 "eval_emergence_result.json"),
        _minimum("emergence_accuracy", emergence, "summary.accuracy", 0.90,
                 "eval_emergence_result.json"),
        _equals("mature_false_positive", emergence, "summary.mature_false_positive", 0,
                "eval_emergence_result.json"),
    ])
    emergence_n = _value(emergence, "summary.n")
    emergence_human = _value(emergence, "summary.human_labeled_count")
    checks.append(Check(
        "emergence_human_truth",
        isinstance(emergence_n, int) and emergence_n >= 10 and emergence_human == emergence_n,
        {"cases": emergence_n, "human_labeled": emergence_human},
        "every emergence case has independent human truth",
        "eval_emergence_result.json",
    ))

    ranking = docs["eval_hr_ranking_result.json"]
    checks.extend([
        _minimum("hr_batch_count", ranking, "summary.batch_count", 5,
                 "eval_hr_ranking_result.json"),
        _minimum("hr_min_batch_size", ranking, "summary.min_candidates_per_batch", 20,
                 "eval_hr_ranking_result.json"),
        _minimum("hr_top5_precision", ranking, "summary.top5_precision", 0.80,
                 "eval_hr_ranking_result.json"),
        _equals("hr_ranking_deterministic", ranking, "summary.deterministic", True,
                "eval_hr_ranking_result.json"),
        _minimum("hr_human_labeled_candidates", ranking,
                 "summary.human_labeled_candidate_count", 100,
                 "eval_hr_ranking_result.json"),
        _equals("hr_annotation_complete", ranking, "summary.annotation_complete", True,
                "eval_hr_ranking_result.json"),
    ])

    trace = docs["eval_traceability_result.json"]
    total = _value(trace, "summary.active_core_total")
    evidenced = _value(trace, "summary.evidenced_core_total")
    checks.append(Check("active_core_traceability", isinstance(total, int) and total > 0 and total == evidenced,
                        {"total": total, "evidenced": evidenced}, "all active core capabilities evidenced",
                        "eval_traceability_result.json"))
    checks.extend([
        _equals("active_core_valid_urls", trace, "summary.valid_url_core_total", total,
                "eval_traceability_result.json"),
        _equals("active_core_employer_validated", trace,
                "summary.employer_validated_core_total", total,
                "eval_traceability_result.json"),
        _equals("trace_placeholder_urls", trace, "summary.placeholder_url_count", 0,
                "eval_traceability_result.json"),
    ])

    security = docs["eval_security_result.json"]
    checks.extend([
        _minimum("security_scope_case_count", security, "summary.scope_case_count", 1,
                 "eval_security_result.json"),
        _equals("cross_scope_successes", security, "summary.cross_scope_successes", 0,
                "eval_security_result.json"),
        _equals("sensitive_log_leaks", security, "summary.sensitive_log_leaks", 0,
                "eval_security_result.json"),
        _equals("read_only_boundary", security, "summary.read_only_private_append_passed", True,
                "eval_security_result.json"),
    ])
    security_names = _value(security, "junit.names")
    checks.append(Check(
        "required_security_cases",
        isinstance(security_names, list) and REQUIRED_SECURITY_TESTS.issubset(set(security_names)),
        security_names,
        "required read-only, cross-user, cross-org and redaction tests",
        "eval_security_result.json",
    ))

    e2e = docs["eval_e2e_result.json"]
    scenarios = _value(e2e, "scenarios")
    scenario_status = ({item.get("name"): item.get("passed") for item in scenarios
                        if isinstance(item, dict)} if isinstance(scenarios, list) else {})
    checks.append(Check(
        "seven_e2e_workflows",
        REQUIRED_E2E_SCENARIOS.issubset(scenario_status) and
        all(scenario_status[name] is True for name in REQUIRED_E2E_SCENARIOS),
        scenario_status,
        "all seven prescribed end-to-end workflows pass",
        "eval_e2e_result.json",
    ))

    engineering = docs["eval_engineering_result.json"]
    checks.extend([
        _minimum("backend_tests", engineering, "summary.tests_passed", 146,
                 "eval_engineering_result.json"),
        _equals("backend_test_failures", engineering, "summary.tests_failed", 0,
                "eval_engineering_result.json"),
        _minimum("backend_coverage", engineering, "summary.coverage_percent", 73,
                 "eval_engineering_result.json"),
        _equals("frontend_build", engineering, "summary.frontend_build_passed", True,
                "eval_engineering_result.json"),
        _maximum("ordinary_get_p95_ms", engineering, "summary.ordinary_get_p95_ms", 500,
                 "eval_engineering_result.json"),
        _maximum("single_match_p95_ms", engineering, "summary.single_match_p95_ms", 30000,
                 "eval_engineering_result.json"),
        _equals("batch_nonblocking", engineering, "summary.batch_nonblocking", True,
                "eval_engineering_result.json"),
        _minimum("batch_nonblocking_case_count", engineering,
                 "summary.batch_nonblocking_case_count", 1,
                 "eval_engineering_result.json"),
        _equals("uni_app_core_e2e", engineering, "summary.uni_app_core_e2e_passed", True,
                "eval_engineering_result.json"),
        _equals("uni_app_https_api", engineering, "summary.uni_app_https_api_configured", True,
                "eval_engineering_result.json"),
        _equals("hbuilderx_cloud_package", engineering,
                "summary.hbuilderx_cloud_package_passed", True,
                "eval_engineering_result.json"),
        _equals("hbuilderx_cloud_provider", engineering,
                "summary.hbuilderx_cloud_provider", "HBuilderX",
                "eval_engineering_result.json"),
        _equals("cloud_package_signed", engineering, "summary.cloud_package_signed", True,
                "eval_engineering_result.json"),
    ])
    build_id = _value(engineering, "summary.hbuilderx_cloud_build_id")
    checks.append(Check("hbuilderx_cloud_build_id", isinstance(build_id, str) and bool(build_id.strip()),
                        build_id, "non-empty HBuilderX cloud build id",
                        "eval_engineering_result.json"))
    package_rel = _value(engineering, "summary.cloud_package_path")
    package_path = _repo_artifact(repo_root, package_rel)
    checks.append(Check("cloud_package_artifact",
                        bool(package_path and package_path.is_file() and package_path.stat().st_size > 0),
                        package_rel, "existing non-empty HBuilderX cloud package artifact",
                        "eval_engineering_result.json"))

    migration = docs["eval_migration_result.json"]
    for field in ("shadow_migration", "backup_created", "state_reconciled", "rollback_rehearsed"):
        checks.append(_equals(f"migration_{field}", migration, f"summary.{field}", True,
                             "eval_migration_result.json"))
    for field in ("production_snapshot_imported", "employer_recompute_completed",
                  "shadow_cutover_rehearsed", "private_tables_preserved",
                  "production_health_verified"):
        checks.append(_equals(f"migration_{field}", migration, f"summary.{field}", True,
                             "eval_migration_result.json"))
    checks.append(_equals("migration_source_kind", migration, "source_kind",
                          "production_snapshot", "eval_migration_result.json"))
    checks.append(_equals("production_read_only", migration, "summary.production_read_only", True,
                          "eval_migration_result.json"))

    failed = [asdict(check) for check in checks if not check.passed]
    return {
        "run_id": run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "passed": not failed,
        "summary": {"checks": len(checks), "passed": len(checks) - len(failed), "failed": len(failed)},
        "checks": [asdict(check) for check in checks],
        "failed_checks": failed,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifacts", type=Path, default=Path("artifacts/release/latest"))
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    artifacts = args.artifacts.resolve()
    report = evaluate_release(artifacts, args.repo_root.resolve())
    output = args.output.resolve() if args.output else artifacts / "acceptance_report.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False))
    if report["failed_checks"]:
        for item in report["failed_checks"]:
            print(f"FAIL {item['key']}: actual={item['actual']!r}, expected={item['expected']}")
    print(f"report: {output}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
