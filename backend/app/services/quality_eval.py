"""Pure quality metrics for role-contract goldens and shadow recomputation."""
from __future__ import annotations

from collections import Counter, defaultdict
from itertools import combinations


def precision_at_k(predicted: list[str], expected: set[str] | list[str], k: int = 10) -> float:
    top = predicted[:k]
    if not top:
        return 0.0
    gold = set(expected)
    return round(len(set(top) & gold) / len(top), 4)


def recall_at_k(predicted: list[str], expected: set[str] | list[str], k: int = 10) -> float:
    gold = set(expected)
    if not gold:
        return 0.0
    return round(len(set(predicted[:k]) & gold) / len(gold), 4)


def importance_accuracy(contract: dict, golden: dict) -> float:
    """Score required/bonus labels over every expected golden cluster."""
    expected = {name: "bonus" for name in golden.get("bonus_clusters", [])}
    expected.update({name: "required" for name in golden.get("required_clusters", [])})
    if not expected:
        return 0.0
    predicted = {
        cluster.get("name"): cluster.get("importance")
        for cluster in contract.get("clusters", [])
        if cluster.get("name")
    }
    return round(sum(predicted.get(name) == label for name, label in expected.items()) /
                 len(expected), 4)


def contract_slice_signature(contract: dict) -> tuple[tuple[str, str, float, str], ...]:
    """Return an order-independent signature of the contract fields used by matching."""
    return tuple(sorted(
        (
            str(cluster.get("name", "")),
            str(cluster.get("importance", "")),
            round(float(cluster.get("weight", 0.0)), 4),
            str(cluster.get("level_required", "")),
        )
        for cluster in contract.get("clusters", [])
    ))


def slice_difference_report(rows: list[dict]) -> dict:
    """Compare RoleContracts for distinct seniority/recruitment slices of each job."""
    by_job: dict[str, list[dict]] = {}
    for row in rows:
        job_name = str(row.get("job_name") or row.get("canonical_title") or "")
        if job_name:
            by_job.setdefault(job_name, []).append(row)

    dimensions = ("seniority", "recruitment_type", "track", "industry")
    comparisons = []
    for job_name, profiles in sorted(by_job.items()):
        ordered = sorted(profiles, key=lambda row: str(row.get("profile_id") or row.get("id") or ""))
        for left, right in combinations(ordered, 2):
            left_slice = tuple(left.get(field, "unspecified") for field in dimensions)
            right_slice = tuple(right.get(field, "unspecified") for field in dimensions)
            if left_slice == right_slice:
                continue
            left_signature = contract_slice_signature(left.get("contract", {}))
            right_signature = contract_slice_signature(right.get("contract", {}))
            comparisons.append({
                "job_name": job_name,
                "left_profile_id": left.get("profile_id") or left.get("id"),
                "right_profile_id": right.get("profile_id") or right.get("id"),
                "left_slice": dict(zip(dimensions, left_slice)),
                "right_slice": dict(zip(dimensions, right_slice)),
                "different": left_signature != right_signature,
                "left_signature": [list(item) for item in left_signature],
                "right_signature": [list(item) for item in right_signature],
            })
    different_count = sum(row["different"] for row in comparisons)
    return {
        "comparison_count": len(comparisons),
        "different_count": different_count,
        "difference_rate": round(different_count / len(comparisons), 4) if comparisons else 0.0,
        "comparisons": comparisons,
    }


def stratified_metric_report(
    rows: list[dict],
    *,
    dimensions: tuple[str, ...],
    metric_fields: tuple[str, ...],
) -> dict[str, dict[str, dict]]:
    """Aggregate row-level numeric metrics for every governed dimension."""
    report: dict[str, dict[str, dict]] = {}
    for dimension in dimensions:
        groups: dict[str, list[dict]] = defaultdict(list)
        for row in rows:
            value = str(row.get(dimension) or "unspecified")
            groups[value].append(row)
        report[dimension] = {
            value: {
                "n": len(items),
                **{
                    metric: round(sum(float(item.get(metric, 0.0)) for item in items) /
                                  len(items), 4)
                    for metric in metric_fields
                },
            }
            for value, items in sorted(groups.items())
        }
    return report


def classification_metrics(rows: list[dict], labels: tuple[str, ...]) -> dict:
    """Return accuracy and macro-F1 without weighting large strata twice."""
    if not rows:
        return {"n": 0, "accuracy": 0.0, "macro_f1": 0.0,
                "label_distribution": {}}
    accuracy = sum(row.get("predicted") == row.get("truth") for row in rows) / len(rows)
    f1s = []
    for label in labels:
        tp = sum(row.get("predicted") == label and row.get("truth") == label for row in rows)
        fp = sum(row.get("predicted") == label and row.get("truth") != label for row in rows)
        fn = sum(row.get("predicted") != label and row.get("truth") == label for row in rows)
        precision = tp / max(1, tp + fp)
        recall = tp / max(1, tp + fn)
        f1s.append(2 * precision * recall / max(1e-9, precision + recall))
    return {
        "n": len(rows),
        "accuracy": round(accuracy, 4),
        "macro_f1": round(sum(f1s) / len(labels), 4),
        "label_distribution": dict(sorted(Counter(
            str(row.get("truth") or "unspecified") for row in rows).items())),
    }


def stratified_classification_report(
    rows: list[dict],
    *,
    dimensions: tuple[str, ...],
    labels: tuple[str, ...],
) -> dict[str, dict[str, dict]]:
    """Compute classification metrics independently within each stratum."""
    report: dict[str, dict[str, dict]] = {}
    for dimension in dimensions:
        groups: dict[str, list[dict]] = defaultdict(list)
        for row in rows:
            groups[str(row.get(dimension) or "unspecified")].append(row)
        report[dimension] = {
            value: classification_metrics(items, labels)
            for value, items in sorted(groups.items())
        }
    return report


def evaluate_contract(contract: dict, golden: dict, k: int = 10) -> dict:
    predicted = [c["name"] for c in contract.get("clusters", [])]
    expected = list(golden.get("required_clusters", [])) + list(golden.get("bonus_clusters", []))
    prohibited_terms = tuple(str(x).casefold() for x in golden.get("prohibited", []))
    predicted_skills = [skill.get("name", "") for c in contract.get("clusters", [])
                        for skill in c.get("skills", [])]
    violations = [name for name in predicted_skills
                  if any(term in name.casefold() for term in prohibited_terms)]
    precision = precision_at_k(predicted, expected, k)
    recall = recall_at_k(predicted, expected, k)
    labels = importance_accuracy(contract, golden)
    return {
        "precision_at_10": precision,
        "recall_at_10": recall,
        "importance_accuracy": labels,
        "prohibited_violations": violations,
        "passed": precision >= 0.9 and recall >= 0.7 and labels >= 0.9 and not violations,
    }


def shadow_diff(job_name: str, before: list[dict], after: list[dict],
                before_contract: dict | None = None,
                after_contract: dict | None = None) -> dict:
    """Compare legacy channel-based results with employer-validated results."""
    before_state = {c["name"]: c.get("status", "active") for c in before}
    after_state = {c["name"]: c.get("status", "active") for c in after}
    names = sorted(set(before_state) | set(after_state))
    transitions = Counter(f"{before_state.get(n, 'missing')}->{after_state.get(n, 'missing')}" for n in names)
    changed = [n for n in names if before_state.get(n) != after_state.get(n)]
    before_clusters = [c["name"] for c in (before_contract or {}).get("clusters", [])]
    after_clusters = [c["name"] for c in (after_contract or {}).get("clusters", [])]
    return {
        "job_name": job_name,
        "before": _state_counts(before),
        "after": _state_counts(after),
        "transitions": dict(transitions),
        "changed_capabilities": changed,
        "contract": {
            "before": before_clusters,
            "after": after_clusters,
            "added": [x for x in after_clusters if x not in before_clusters],
            "removed": [x for x in before_clusters if x not in after_clusters],
        },
    }


def build_shadow_report(rows: list[dict]) -> dict:
    """Aggregate per-job shadow diffs without touching any database."""
    jobs = [shadow_diff(
        row["job_name"], row.get("before", []), row.get("after", []),
        row.get("before_contract"), row.get("after_contract")) for row in rows]
    transitions = Counter()
    for job in jobs:
        transitions.update(job["transitions"])
    return {
        "job_count": len(jobs),
        "before": {
            "active": sum(j["before"]["active"] for j in jobs),
            "candidate": sum(j["before"]["candidate"] for j in jobs),
        },
        "after": {
            "active": sum(j["after"]["active"] for j in jobs),
            "candidate": sum(j["after"]["candidate"] for j in jobs),
        },
        "transitions": dict(transitions),
        "jobs": jobs,
    }


def _state_counts(capabilities: list[dict]) -> dict:
    counts = Counter(c.get("status", "active") for c in capabilities)
    return {"total": len(capabilities), "active": counts["active"],
            "candidate": counts["candidate"], "deprecated": counts["deprecated"]}
