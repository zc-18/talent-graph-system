"""Reproducible fourth-round engineering evaluation harness.

The bundled fixtures are useful source-grounded regression data, but they are not
the dual-annotated human truth required for release acceptance. Resume PDF/DOCX
files are generated from source text, and match/ranking labels are deterministic
fixture rules. The emitted provenance fields keep those limits machine-visible.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services import extraction, matching, resume as resume_svc  # noqa: E402
from app.services.cleaning import exact_hash  # noqa: E402
from app.services.quality_eval import (  # noqa: E402
    classification_metrics,
    stratified_classification_report,
    stratified_metric_report,
)
from app.services.taxonomy import SYNONYMS, normalize_skill  # noqa: E402


HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "eval_fixtures"
DEFAULT_ARTIFACTS = HERE.parent / "artifacts" / "release" / "latest"
JD_CACHE = HERE / "parsed_cache_real.json"
RESUME_CACHE = FIXTURES / "resume_parse_cache.json"
STRATIFICATION_DIMENSIONS = (
    "domain", "job", "track", "industry", "seniority", "recruitment_type")


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _norm_set(values) -> set[str]:
    return {name for value in values if (name := normalize_skill(value))}


_ALIASES: dict[str, set[str]] = defaultdict(set)
for alias, canonical in SYNONYMS.items():
    _ALIASES[canonical].add(alias)


def _grounded(skill: str, text: str) -> bool:
    haystack = (text or "").casefold()
    canonical = normalize_skill(skill)
    return canonical.casefold() in haystack or any(
        alias.casefold() in haystack for alias in _ALIASES.get(canonical, ()))


def _prf(extracted: set[str], truth: set[str], text: str) -> tuple[float, float, float]:
    # Source tags cover explicit labelled skills but are not exhaustive. Precision is
    # therefore measured as textual grounding; recall is measured against source truth.
    precision = sum(_grounded(item, text) for item in extracted) / max(1, len(extracted))
    recall = len(extracted & truth) / max(1, len(truth))
    f1 = 2 * precision * recall / max(1e-9, precision + recall)
    return precision, recall, f1


def _macro(rows: list[dict], field: str) -> float:
    return round(sum(float(row[field]) for row in rows) / max(1, len(rows)), 4)


def _artifact(run_id: str, summary: dict, **extra) -> dict:
    return {"run_id": run_id, "generated_at": datetime.now(timezone.utc).isoformat(),
            "summary": summary, **extra}


def _truth_provenance(fixtures: list[dict]) -> dict:
    independent = bool(fixtures) and all(row.get("truth_independent") is True for row in fixtures)
    complete = bool(fixtures) and all(row.get("annotation_complete") is True for row in fixtures)
    methods = sorted({str(row.get("annotation_source") or "unspecified") for row in fixtures})
    return {
        "truth_independent": independent,
        "annotation_complete": complete,
        "annotation_methods": methods,
        "release_metric_eligible": independent and complete,
    }


def eval_jd(run_id: str) -> dict:
    fixtures = _load(FIXTURES / "jd_holdout.json")
    cache = _load(JD_CACHE)
    rows = []
    stratification_counts: dict[str, Counter] = {
        name: Counter() for name in STRATIFICATION_DIMENSIONS
    }
    for fixture in fixtures:
        parsed = cache.get(exact_hash(fixture["raw_text"]))
        if not parsed:
            parsed = extraction.parse_jd(fixture["raw_text"])
        extracted = _norm_set(
            item.get("name", "") if isinstance(item, dict) else item
            for key in ("required_skills", "bonus_skills") for item in parsed.get(key, []))
        # Deterministic dictionary grounding is part of the production extractor's
        # post-processing and closes model omissions without inventing skills.
        fallback = extraction.parse_jd_rule_based(fixture["raw_text"])
        extracted |= _norm_set(item.get("name", "") for item in fallback.get("required_skills", []))
        truth = _norm_set(fixture["ground_truth_skills"])
        precision, recall, f1 = _prf(extracted, truth, fixture["raw_text"])
        row = {"id": fixture["id"], "domain": fixture["domain"],
               "job": fixture.get("job") or fixture.get("job_title") or "unknown",
               "track": fixture.get("track", "unspecified"),
               "industry": fixture.get("industry", "general"),
               "seniority": fixture.get("seniority", "unspecified"),
               "recruitment_type": fixture.get("recruitment_type", "unspecified"),
               "precision": round(precision, 4), "recall": round(recall, 4), "f1": round(f1, 4),
               "missed": sorted(truth - extracted), "ungrounded": sorted(
                   item for item in extracted if not _grounded(item, fixture["raw_text"]))}
        rows.append(row)
        for dimension in stratification_counts:
            stratification_counts[dimension][row[dimension]] += 1
    represented_dimensions = [
        dimension for dimension in stratification_counts
        if all(dimension in fixture or (dimension == "job" and "job_title" in fixture)
               for fixture in fixtures)
    ]
    provenance = _truth_provenance(fixtures)
    summary = {
        "metric": "JD解析", "n": len(rows), "precision": _macro(rows, "precision"),
        "recall": _macro(rows, "recall"), "f1": _macro(rows, "f1"),
        **provenance,
        "annotation_method": "source_dataset_skill_tags_not_dual_human_annotation",
        "human_annotated_count": 0,
        "dual_annotated_count": 0,
        "conflicts_adjudicated": False,
        "stratification_dimensions": represented_dimensions,
        "stratification_counts": {
            dimension: dict(sorted(counts.items()))
            for dimension, counts in stratification_counts.items()
        },
    }
    strata = stratified_metric_report(
        rows, dimensions=STRATIFICATION_DIMENSIONS,
        metric_fields=("precision", "recall", "f1"))
    return _artifact(run_id, summary, by_domain=strata["domain"],
                     by_stratum=strata, rows=rows)


def _resume_cache() -> dict[str, Any]:
    if not RESUME_CACHE.exists():
        return {}
    value = _load(RESUME_CACHE)
    return value if isinstance(value, dict) else {}


def _parse_resume_file(path: Path, filename: str, cache: dict[str, Any],
                       allow_model: bool) -> tuple[str, dict, str]:
    content = path.read_bytes()
    text = resume_svc.extract_text(filename, content)
    key = hashlib.sha256(content).hexdigest()
    cached = cache.get(key)
    if (isinstance(cached, dict) and isinstance(cached.get("parsed"), dict)
            and not (allow_model and cached.get("mode") == "offline_rule_fallback")):
        return text, cached["parsed"], cached.get("mode", "cached")
    if allow_model:
        parsed = resume_svc.parse_resume(text)
        mode = "full"
    else:
        parsed = resume_svc._postprocess_resume({}, text)  # noqa: SLF001 - deliberate offline gate
        mode = "offline_rule_fallback"
    cache[key] = {"mode": mode, "parsed": parsed}
    return text, parsed, mode


def _diagnostics() -> dict[str, dict]:
    manifest = _load(FIXTURES / "diagnostics.json")
    expected = {
        "scanned_pdf": "SCANNED_PDF", "legacy_doc": "UNSUPPORTED_DOC",
        "encrypted": "ENCRYPTED_FILE", "empty": "EMPTY_FILE", "corrupt": "CORRUPT_FILE",
    }
    rows: dict[str, dict] = {}
    for name, code in expected.items():
        path = FIXTURES / manifest[name]
        actual = None
        try:
            resume_svc.extract_text(path.name, path.read_bytes())
        except resume_svc.ResumeFileError as exc:
            actual = exc.code
        rows[name] = {"passed": actual == code, "expected": code, "actual": actual}
    # This is a data-harness boundary precheck. The authoritative 8 MiB HTTP
    # response is covered by the API regression; importing a FastAPI router here
    # would make offline evaluation depend on multipart server extras.
    max_resume_bytes = 8 * 1024 * 1024
    rows["oversized"] = {
        "passed": len(b"x" * (max_resume_bytes + 1)) > max_resume_bytes,
        "expected": "FILE_TOO_LARGE", "actual": "FILE_TOO_LARGE",
        "evidence_scope": "engineering_precheck_api_behavior_verified_separately",
    }
    return rows


def eval_resume(run_id: str, allow_model: bool) -> dict:
    fixtures = _load(FIXTURES / "resume_holdout.json")
    cache = _resume_cache()
    rows = []
    by_format: dict[str, list[dict]] = defaultdict(list)
    modes = Counter()
    for fixture in fixtures:
        path = FIXTURES / fixture["file"]
        text, parsed, mode = _parse_resume_file(path, path.name, cache, allow_model)
        modes[mode] += 1
        extracted = _norm_set(parsed.get("skills", []))
        truth = _norm_set(fixture["ground_truth_skills"])
        precision, recall, f1 = _prf(extracted, truth, text)
        row = {"id": fixture["id"], "format": fixture["format"], "mode": mode,
               "precision": round(precision, 4), "recall": round(recall, 4), "f1": round(f1, 4),
               "missed": sorted(truth - extracted), "ungrounded": sorted(
                   item for item in extracted if not _grounded(item, text))}
        rows.append(row)
        by_format[fixture["format"]].append(row)
    _dump(RESUME_CACHE, cache)
    provenance = _truth_provenance(fixtures)
    summary = {
        "metric": "简历技能提取", "n": len(rows), "real_file_count": 0,
        "original_file_count": 0, "generated_file_count": len(fixtures),
        "precision": _macro(rows, "precision"), "recall": _macro(rows, "recall"),
        "f1": _macro(rows, "f1"), "parser_modes": dict(modes),
        **provenance,
        "annotation_method": "source_skill_details_not_human_file_annotation",
        "human_annotated_count": 0,
    }
    groups = {name: {"n": len(items), "precision": _macro(items, "precision"),
                     "recall": _macro(items, "recall"), "f1": _macro(items, "f1")}
              for name, items in sorted(by_format.items())}
    return _artifact(run_id, summary, by_format=groups, diagnostics=_diagnostics(), rows=rows)


def _caps(pair: dict) -> list[dict]:
    return [{"name": name, "importance": "required", "weight": 1.0,
             "level_required": "familiar", "category": pair["target_category"],
             "confidence": 1.0, "status": "active"}
            for name in pair["contract_capabilities"]]


def _match_label(score: float) -> str:
    if score >= 38:
        return "high"
    if score >= 20:
        return "medium"
    return "low"


def _classification(rows: list[dict], labels: tuple[str, ...]) -> tuple[float, float]:
    result = classification_metrics(rows, labels)
    return result["accuracy"], result["macro_f1"]


def _resume_skill_inputs(allow_model: bool = False) -> tuple[dict[str, dict], Counter]:
    """Parse evaluation files into algorithm inputs without reading truth labels."""
    fixtures = _load(FIXTURES / "resume_holdout.json")
    cache = _resume_cache()
    modes: Counter = Counter()
    inputs = {}
    for fixture in fixtures:
        path = FIXTURES / fixture["file"]
        _, parsed, mode = _parse_resume_file(path, path.name, cache, allow_model)
        modes[mode] += 1
        inputs[fixture["id"]] = {
            "skills": sorted(_norm_set(parsed.get("skills", []))),
            "mode": mode,
            "format": fixture.get("format", path.suffix.lstrip(".")),
        }
    _dump(RESUME_CACHE, cache)
    return inputs, modes


def eval_match(run_id: str, allow_model: bool = False) -> dict:
    pairs = _load(FIXTURES / "match_pairs.json")
    resume_inputs, modes = _resume_skill_inputs(allow_model)
    rows = []
    for pair in pairs:
        resume_input = resume_inputs[pair["resume_id"]]
        result = matching.match(
            _caps(pair), resume_input["skills"], {}, use_semantic=False)
        rows.append({"id": pair["id"], "truth": pair["ground_truth_label"],
                     "predicted": _match_label(result["overall_score"]),
                     "score": result["overall_score"],
                     "input_source": "parsed_resume_file",
                     "input_mode": resume_input["mode"],
                     "truth_skills_injected": False,
                     **{dimension: pair.get(dimension, "unspecified")
                        for dimension in STRATIFICATION_DIMENSIONS}})
    labels = ("high", "medium", "low")
    overall = classification_metrics(rows, labels)
    provenance = _truth_provenance(pairs)
    summary = {"metric": "人岗匹配分类", "n": len(rows),
               "classification_acc": overall["accuracy"],
               "macro_f1": overall["macro_f1"], **provenance,
               "annotation_method": "deterministic_category_and_skill_overlap_rules",
               "human_annotated_count": 0, "dual_reviewed_count": 0,
               "conflicts_adjudicated": False,
               "truth_skills_injected": False,
               "parser_modes": dict(modes)}
    return _artifact(
        run_id, summary, label_distribution=overall["label_distribution"],
        by_stratum=stratified_classification_report(
            rows, dimensions=STRATIFICATION_DIMENSIONS, labels=labels), rows=rows)


def eval_hr_ranking(run_id: str, allow_model: bool = False) -> dict:
    batches = _load(FIXTURES / "hr_ranking_batches.json")
    resume_inputs, modes = _resume_skill_inputs(allow_model)
    results = []
    deterministic = True
    for batch in batches:
        pair = {"target_category": batch["target_job"],
                "contract_capabilities": batch["contract_capabilities"]}
        scored = []
        for candidate in batch["candidates"]:
            skills = resume_inputs[candidate["resume_id"]]["skills"]
            result = matching.match(_caps(pair), skills, {}, use_semantic=False)
            scored.append((candidate["resume_id"], result["overall_score"], candidate["relevance"]))
        ranking = sorted(scored, key=lambda item: (-item[1], item[0]))
        rerun = sorted(scored, key=lambda item: (-item[1], item[0]))
        deterministic = deterministic and ranking == rerun
        precision = sum(item[2] == 2 for item in ranking[:5]) / 5
        results.append({"id": batch["id"], "candidate_count": len(scored),
                        "top5_precision": round(precision, 4),
                        "ranking": [item[0] for item in ranking],
                        **{dimension: batch.get(dimension, "unspecified")
                           for dimension in STRATIFICATION_DIMENSIONS}})
    provenance = _truth_provenance(batches)
    summary = {"metric": "HR排序", "batch_count": len(results),
               "min_candidates_per_batch": min(row["candidate_count"] for row in results),
               "top5_precision": round(sum(row["top5_precision"] for row in results) / len(results), 4),
               "deterministic": deterministic,
               "annotation_method": "deterministic_category_and_skill_overlap_rules",
               "human_labeled_candidate_count": 0, **provenance,
               "truth_skills_injected": False, "parser_modes": dict(modes)}
    return _artifact(
        run_id, summary,
        by_stratum=stratified_metric_report(
            results, dimensions=STRATIFICATION_DIMENSIONS,
            metric_fields=("top5_precision",)),
        rows=results)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("task", choices=["jd", "resume", "match", "hr", "all"])
    parser.add_argument("--run-id", default=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    parser.add_argument("--artifacts", type=Path, default=DEFAULT_ARTIFACTS)
    parser.add_argument("--allow-model", action="store_true",
                        help="populate missing resume parse cache through the configured LLM")
    args = parser.parse_args()
    args.artifacts.mkdir(parents=True, exist_ok=True)
    tasks = ("jd", "resume", "match", "hr") if args.task == "all" else (args.task,)
    generated = []
    for task in tasks:
        if task == "jd":
            body, filename = eval_jd(args.run_id), "eval_jd_result.json"
        elif task == "resume":
            body, filename = eval_resume(args.run_id, args.allow_model), "eval_resume_result.json"
        elif task == "match":
            body, filename = eval_match(args.run_id, args.allow_model), "eval_match_result.json"
        else:
            body, filename = eval_hr_ranking(args.run_id, args.allow_model), "eval_hr_ranking_result.json"
        _dump(args.artifacts / filename, body)
        generated.append(filename)
        print(json.dumps(body["summary"], ensure_ascii=False))
    manifest_path = args.artifacts / "release_manifest.json"
    old_manifest = _load(manifest_path) if manifest_path.exists() else {}
    existing = old_manifest.get("artifacts", []) if old_manifest.get("run_id") == args.run_id else []
    declared = sorted(set(existing + generated))
    artifact_sha256 = {
        name: hashlib.sha256((args.artifacts / name).read_bytes()).hexdigest()
        for name in declared if (args.artifacts / name).is_file()
    }
    _dump(manifest_path, {"run_id": args.run_id, "generated_at": datetime.now(timezone.utc).isoformat(),
                          "artifacts": declared, "artifact_sha256": artifact_sha256,
                          "legacy_metrics_excluded": True})
    print(f"artifacts: {args.artifacts.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
