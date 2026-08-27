"""Unit tests for the release evidence gate (no database or network)."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from data.build_release_evidence import _uni_app_core_e2e
from data.release_gate import evaluate_release


FILES = [
    "eval_jd_result.json", "eval_resume_result.json", "eval_match_result.json",
    "eval_role_contract_result.json", "eval_emergence_result.json",
    "eval_hr_ranking_result.json", "eval_traceability_result.json",
    "eval_security_result.json", "eval_e2e_result.json",
    "eval_engineering_result.json", "eval_migration_result.json",
]


def _write(path: Path, body: dict) -> None:
    path.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")


def _valid_artifacts(root: Path, repo: Path) -> None:
    run_id = "release-test-run"
    golden_profiles = [{"id": f"profile-{index}"} for index in range(14)]
    fixture_path = repo / "backend" / "tests" / "fixtures" / "job_profile_golden.json"
    fixture_path.parent.mkdir(parents=True, exist_ok=True)
    _write(fixture_path, {"profiles": golden_profiles})
    bodies = {
        "eval_jd_result.json": {"summary": {"n": 100, "f1": .91, "truth_independent": True,
                                                    "annotation_complete": True,
                                                    "human_annotated_count": 100,
                                                    "dual_annotated_count": 100,
                                                    "conflicts_adjudicated": True,
                                                    "stratification_dimensions": [
                                                        "domain", "job", "seniority",
                                                        "recruitment_type"]},
                                "by_domain": {"ai": {"f1": .9}, "cloud": {"f1": .86}}},
        "eval_resume_result.json": {"summary": {"n": 100, "real_file_count": 100, "f1": .91,
                                                        "original_file_count": 100,
                                                        "human_annotated_count": 100,
                                                        "annotation_complete": True},
                                    "by_format": {"pdf": {"n": 34, "f1": .91},
                                                  "docx": {"n": 33, "f1": .92},
                                                  "txt": {"n": 33, "f1": .94}},
                                    "diagnostics": {name: {"passed": True} for name in
                                                    ("scanned_pdf", "legacy_doc", "encrypted", "empty",
                                                     "corrupt", "oversized")}},
        "eval_match_result.json": {"summary": {"n": 100, "classification_acc": .91,
                                                       "macro_f1": .9, "truth_independent": True,
                                                       "human_annotated_count": 100,
                                                       "dual_reviewed_count": 100,
                                                       "conflicts_adjudicated": True}},
        "eval_role_contract_result.json": {"summary": {"job_count": 14, "precision_at_10": .9,
                                                                "unique_job_count": 10,
                                                                "golden_expected_profile_count": 14,
                                                                "golden_expected_profile_ids": [
                                                                    f"profile-{index}"
                                                                    for index in range(14)],
                                                                "golden_profile_count": 14,
                                                                "golden_pass_count": 14,
                                                                "golden_fail_count": 0,
                                                                "failed_profile_ids": [],
                                                                "missing_golden_profile_ids": [],
                                                                "min_core_clusters": 8,
                                                                "max_core_clusters": 12,
                                                                "forbidden_skill_hits": 0,
                                                                "expert_signoff_status": "signed",
                                                                "expert_reviewer_count": 2,
                                                                "signed_job_count": 10,
                                                                "conflicts_arbitrated": True},
                                                   "golden_profiles": [
                                                       {"profile_id": f"profile-{index}",
                                                        "passed": True}
                                                       for index in range(14)],
                                                   "missing_golden_jobs": []},
        "eval_emergence_result.json": {"summary": {"n": 10, "accuracy": .9,
                                                             "human_labeled_count": 10,
                                                             "mature_false_positive": 0}},
        "eval_hr_ranking_result.json": {"summary": {"batch_count": 5, "min_candidates_per_batch": 20,
                                                             "top5_precision": .8, "deterministic": True,
                                                             "human_labeled_candidate_count": 100,
                                                             "annotation_complete": True}},
        "eval_traceability_result.json": {"summary": {"active_core_total": 80,
                                                              "evidenced_core_total": 80,
                                                              "valid_url_core_total": 80,
                                                              "employer_validated_core_total": 80,
                                                              "placeholder_url_count": 0}},
        "eval_security_result.json": {"summary": {"scope_case_count": 12,
                                                           "cross_scope_successes": 0,
                                                           "sensitive_log_leaks": 0,
                                                           "read_only_private_append_passed": True},
                                      "junit": {"names": [
                                          "test_read_only_blocks_authenticated_admin_public_write",
                                          "test_cross_user_history_is_hidden",
                                          "test_hr_private_batch_writes_in_read_only_and_is_org_scoped",
                                          "test_feedback_cross_owner_and_audit_redaction"]}},
        "eval_e2e_result.json": {"scenarios": [{"name": name, "passed": True} for name in (
            "existing_job_evolution_publish", "emerging_job_discovery_publish",
            "personal_resume_match_history", "hr_batch_topk_team", "feedback_review_apply",
            "read_only_public_private_boundaries", "uni_app_match_history")]},
        "eval_engineering_result.json": {"summary": {"tests_passed": 200, "tests_failed": 0,
                                                             "coverage_percent": 75,
                                                             "frontend_build_passed": True,
                                                             "ordinary_get_p95_ms": 300,
                                                              "single_match_p95_ms": 5000,
                                                              "batch_nonblocking": True,
                                                              "batch_nonblocking_case_count": 1,
                                                             "uni_app_core_e2e_passed": True,
                                                             "uni_app_https_api_configured": True,
                                                             "hbuilderx_cloud_package_passed": True,
                                                             "hbuilderx_cloud_provider": "HBuilderX",
                                                             "hbuilderx_cloud_build_id": "cloud-build-123",
                                                             "cloud_package_signed": True,
                                                             "cloud_package_path": "cloud/app.apk"}},
        "eval_migration_result.json": {"summary": {"shadow_migration": True,
                                                            "backup_created": True,
                                                            "state_reconciled": True,
                                                            "rollback_rehearsed": True,
                                                            "production_read_only": True,
                                                            "production_snapshot_imported": True,
                                                            "employer_recompute_completed": True,
                                                            "shadow_cutover_rehearsed": True,
                                                            "private_tables_preserved": True,
                                                            "production_health_verified": True},
                                       "source_kind": "production_snapshot"},
    }
    package = repo / "cloud" / "app.apk"
    package.parent.mkdir(parents=True)
    package.write_bytes(b"cloud package")
    for name, body in bodies.items():
        _write(root / name, {"run_id": run_id, **body})
    _reseal(root, run_id)


def _reseal(root: Path, run_id: str = "release-test-run") -> None:
    hashes = {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in FILES}
    _write(root / "release_manifest.json", {
        "run_id": run_id, "artifacts": FILES, "artifact_sha256": hashes,
        "legacy_metrics_excluded": True,
    })


def test_release_gate_accepts_complete_same_run_evidence(tmp_path: Path):
    artifacts = tmp_path / "artifacts"
    repo = tmp_path / "repo"
    artifacts.mkdir()
    _valid_artifacts(artifacts, repo)
    report = evaluate_release(artifacts, repo)
    assert report["passed"] is True
    assert report["summary"]["failed"] == 0


def test_release_gate_rejects_old_partial_metrics(tmp_path: Path):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _write(artifacts / "eval_match_result.json",
           {"summary": {"n": 15, "classification_acc": 1.0, "match_f1": 1.0}})
    report = evaluate_release(artifacts, tmp_path)
    failed = {item["key"] for item in report["failed_checks"]}
    assert report["passed"] is False
    assert "single_run_manifest" in failed
    assert "match_independent_pairs" in failed
    assert "match_macro_f1" in failed
    assert "match_truth_is_independent" in failed


def test_release_gate_rejects_contracts_below_eight_core_clusters(tmp_path: Path):
    artifacts = tmp_path / "artifacts"
    repo = tmp_path / "repo"
    artifacts.mkdir()
    _valid_artifacts(artifacts, repo)
    path = artifacts / "eval_role_contract_result.json"
    body = json.loads(path.read_text(encoding="utf-8"))
    body["summary"]["min_core_clusters"] = 7
    _write(path, body)

    report = evaluate_release(artifacts, repo)

    assert report["passed"] is False
    assert any(item["key"] == "contract_min_core_clusters"
               for item in report["failed_checks"])


def test_release_gate_rejects_one_failed_golden_profile(tmp_path: Path):
    artifacts = tmp_path / "artifacts"
    repo = tmp_path / "repo"
    artifacts.mkdir()
    _valid_artifacts(artifacts, repo)
    path = artifacts / "eval_role_contract_result.json"
    body = json.loads(path.read_text(encoding="utf-8"))
    body["golden_profiles"][3]["passed"] = False
    body["summary"].update({
        "golden_pass_count": 13,
        "golden_fail_count": 1,
        "failed_profile_ids": [body["golden_profiles"][3]["profile_id"]],
    })
    _write(path, body)
    _reseal(artifacts)

    report = evaluate_release(artifacts, repo)

    assert report["passed"] is False
    assert any(item["key"] == "contract_all_golden_profiles_pass"
               for item in report["failed_checks"])


def test_release_gate_rejects_skipped_golden_profiles_even_if_ten_pass(tmp_path: Path):
    artifacts = tmp_path / "artifacts"
    repo = tmp_path / "repo"
    artifacts.mkdir()
    _valid_artifacts(artifacts, repo)
    path = artifacts / "eval_role_contract_result.json"
    body = json.loads(path.read_text(encoding="utf-8"))
    body["golden_profiles"] = body["golden_profiles"][:10]
    body["summary"].update({
        "job_count": 10,
        "golden_expected_profile_count": 10,
        "golden_expected_profile_ids": [row["profile_id"]
                                         for row in body["golden_profiles"]],
        "golden_profile_count": 10,
        "golden_pass_count": 10,
        "golden_fail_count": 0,
        "failed_profile_ids": [],
        "missing_golden_profile_ids": [],
    })
    body["missing_golden_jobs"] = []
    _write(path, body)
    _reseal(artifacts)

    report = evaluate_release(artifacts, repo)

    assert report["passed"] is False
    check = next(item for item in report["failed_checks"]
                 if item["key"] == "contract_all_golden_profiles_pass")
    assert len(check["actual"]["expected_profile_ids"]) == 10
    assert len(check["actual"]["actual_profile_ids"]) == 10
    assert len(check["actual"]["fixture_profile_ids"]) == 14


def test_release_gate_rejects_cross_run_artifact(tmp_path: Path):
    artifacts = tmp_path / "artifacts"
    repo = tmp_path / "repo"
    artifacts.mkdir()
    _valid_artifacts(artifacts, repo)
    body = json.loads((artifacts / "eval_resume_result.json").read_text(encoding="utf-8"))
    body["run_id"] = "stale-run"
    _write(artifacts / "eval_resume_result.json", body)
    report = evaluate_release(artifacts, repo)
    assert any(item["key"] == "same_run:eval_resume_result.json"
               for item in report["failed_checks"])


def test_release_gate_does_not_apply_pdf_docx_threshold_to_txt(tmp_path: Path):
    artifacts = tmp_path / "artifacts"
    repo = tmp_path / "repo"
    artifacts.mkdir()
    _valid_artifacts(artifacts, repo)
    path = artifacts / "eval_resume_result.json"
    body = json.loads(path.read_text(encoding="utf-8"))
    body["by_format"]["txt"]["f1"] = .5
    _write(path, body)

    report = evaluate_release(artifacts, repo)

    check = next(item for item in report["checks"]
                 if item["key"] == "resume_each_supported_format_f1")
    assert check["passed"] is True


def test_release_gate_rejects_missing_expert_signoff_and_uni_app_cloud_evidence(tmp_path: Path):
    artifacts = tmp_path / "artifacts"
    repo = tmp_path / "repo"
    artifacts.mkdir()
    _valid_artifacts(artifacts, repo)
    contract_path = artifacts / "eval_role_contract_result.json"
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    contract["summary"]["expert_signoff_status"] = "pending_named_experts"
    _write(contract_path, contract)
    engineering_path = artifacts / "eval_engineering_result.json"
    engineering = json.loads(engineering_path.read_text(encoding="utf-8"))
    engineering["summary"]["uni_app_core_e2e_passed"] = False
    engineering["summary"]["uni_app_https_api_configured"] = False
    engineering["summary"]["hbuilderx_cloud_package_passed"] = False
    _write(engineering_path, engineering)

    failed = {item["key"] for item in evaluate_release(artifacts, repo)["failed_checks"]}

    assert {"contract_named_expert_signoff", "uni_app_core_e2e",
            "uni_app_https_api", "hbuilderx_cloud_package"}.issubset(failed)


def test_release_gate_rejects_generated_or_rule_derived_truth(tmp_path: Path):
    artifacts = tmp_path / "artifacts"
    repo = tmp_path / "repo"
    artifacts.mkdir()
    _valid_artifacts(artifacts, repo)
    mutations = {
        "eval_jd_result.json": {"human_annotated_count": 0, "dual_annotated_count": 0},
        "eval_resume_result.json": {"original_file_count": 0, "human_annotated_count": 0},
        "eval_match_result.json": {"human_annotated_count": 0, "dual_reviewed_count": 0},
        "eval_hr_ranking_result.json": {"human_labeled_candidate_count": 0},
    }
    for name, values in mutations.items():
        path = artifacts / name
        body = json.loads(path.read_text(encoding="utf-8"))
        body["summary"].update(values)
        _write(path, body)
    _reseal(artifacts)

    failed = {item["key"] for item in evaluate_release(artifacts, repo)["failed_checks"]}

    assert {"jd_human_annotated_count", "jd_dual_annotated_count",
            "resume_original_file_count", "resume_human_annotated_count",
            "match_human_annotated_pairs", "match_dual_reviewed_pairs",
            "hr_human_labeled_candidates"}.issubset(failed)


def test_release_gate_rejects_placeholder_trace_and_demo_only_migration(tmp_path: Path):
    artifacts = tmp_path / "artifacts"
    repo = tmp_path / "repo"
    artifacts.mkdir()
    _valid_artifacts(artifacts, repo)
    trace_path = artifacts / "eval_traceability_result.json"
    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    trace["summary"].update({"valid_url_core_total": 0,
                             "employer_validated_core_total": 0,
                             "placeholder_url_count": 240})
    _write(trace_path, trace)
    migration_path = artifacts / "eval_migration_result.json"
    migration = json.loads(migration_path.read_text(encoding="utf-8"))
    migration["source_kind"] = "demo"
    migration["summary"].update({"production_snapshot_imported": False,
                                  "employer_recompute_completed": False,
                                  "shadow_cutover_rehearsed": False})
    _write(migration_path, migration)
    _reseal(artifacts)

    failed = {item["key"] for item in evaluate_release(artifacts, repo)["failed_checks"]}

    assert {"active_core_valid_urls", "active_core_employer_validated",
            "trace_placeholder_urls", "migration_production_snapshot_imported",
            "migration_employer_recompute_completed", "migration_shadow_cutover_rehearsed",
            "migration_source_kind"}.issubset(failed)


def test_release_gate_rejects_unsealed_artifact_changes(tmp_path: Path):
    artifacts = tmp_path / "artifacts"
    repo = tmp_path / "repo"
    artifacts.mkdir()
    _valid_artifacts(artifacts, repo)
    path = artifacts / "eval_emergence_result.json"
    body = json.loads(path.read_text(encoding="utf-8"))
    body["summary"]["accuracy"] = 1.0
    _write(path, body)

    failed = {item["key"] for item in evaluate_release(artifacts, repo)["failed_checks"]}

    assert "artifact_hashes_verified" in failed


def test_release_gate_rejects_cloud_package_outside_repo(tmp_path: Path):
    artifacts = tmp_path / "artifacts"
    repo = tmp_path / "repo"
    artifacts.mkdir()
    _valid_artifacts(artifacts, repo)
    outside = tmp_path / "outside.apk"
    outside.write_bytes(b"not a repo release artifact")
    path = artifacts / "eval_engineering_result.json"
    body = json.loads(path.read_text(encoding="utf-8"))
    body["summary"]["cloud_package_path"] = "../outside.apk"
    _write(path, body)
    _reseal(artifacts)

    failed = {item["key"] for item in evaluate_release(artifacts, repo)["failed_checks"]}

    assert "cloud_package_artifact" in failed


def test_uni_app_core_e2e_uses_same_run_scenario_and_h5_build(tmp_path: Path):
    _write(tmp_path / "eval_e2e_result.json", {
        "run_id": "current-run",
        "scenarios": [{"name": "uni_app_match_history", "passed": True}],
    })
    (tmp_path / "uniapp-h5-build.log").write_text(
        "DONE  Build complete", encoding="utf-8")

    passed, evidence = _uni_app_core_e2e(tmp_path, "current-run")

    assert passed is True
    assert evidence["same_run"] is True
    assert evidence["scenario_passed"] is True
    assert evidence["production_build_passed"] is True


def test_uni_app_core_e2e_does_not_depend_on_or_imply_cloud_package(tmp_path: Path):
    _write(tmp_path / "eval_e2e_result.json", {
        "run_id": "old-run",
        "scenarios": [{"name": "uni_app_match_history", "passed": True}],
    })
    (tmp_path / "uniapp-h5-build.log").write_text(
        "DONE  Build complete", encoding="utf-8")

    passed, evidence = _uni_app_core_e2e(tmp_path, "current-run")

    assert passed is False
    assert evidence["same_run"] is False
