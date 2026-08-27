from app.services.quality_eval import (
    classification_metrics,
    precision_at_k,
    stratified_classification_report,
    stratified_metric_report,
)
from data import evaluate


def test_precision_at_k_penalizes_duplicate_predictions():
    assert precision_at_k(["A", "A"], {"A"}, 10) == 0.5


def test_metric_report_computes_each_governed_stratum():
    rows = [
        {"domain": "AI", "job": "算法", "f1": 1.0},
        {"domain": "AI", "job": "测试", "f1": 0.5},
        {"domain": "云", "job": "运维", "f1": 0.8},
    ]
    report = stratified_metric_report(
        rows, dimensions=("domain", "job"), metric_fields=("f1",))
    assert report["domain"]["AI"] == {"n": 2, "f1": 0.75}
    assert report["job"]["运维"] == {"n": 1, "f1": 0.8}


def test_classification_report_uses_macro_f1_within_each_stratum():
    rows = [
        {"domain": "AI", "truth": "high", "predicted": "high"},
        {"domain": "AI", "truth": "low", "predicted": "high"},
        {"domain": "云", "truth": "low", "predicted": "low"},
    ]
    labels = ("high", "medium", "low")
    overall = classification_metrics(rows, labels)
    report = stratified_classification_report(
        rows, dimensions=("domain",), labels=labels)
    assert overall["accuracy"] == 0.6667
    assert report["domain"]["云"]["accuracy"] == 1.0


def test_match_evaluator_never_uses_fixture_truth_skills_as_input(monkeypatch):
    pair = {
        "id": "pair-1", "resume_id": "resume-1",
        "resume_skills": ["DO_NOT_INJECT"],
        "target_category": "Data Science", "contract_capabilities": ["Python"],
        "ground_truth_label": "high", "truth_independent": False,
        "annotation_source": "deterministic_rule",
    }
    monkeypatch.setattr(evaluate, "_load", lambda _path: [pair])
    monkeypatch.setattr(
        evaluate, "_resume_skill_inputs",
        lambda _allow_model=False: ({
            "resume-1": {"skills": ["Python"], "mode": "test", "format": "txt"}},
            evaluate.Counter({"test": 1})))
    observed = []

    def fake_match(_caps, skills, _levels, use_semantic=False):
        observed.append((skills, use_semantic))
        return {"overall_score": 50}

    monkeypatch.setattr(evaluate.matching, "match", fake_match)
    artifact = evaluate.eval_match("test-run")
    assert observed == [(["Python"], False)]
    assert artifact["rows"][0]["truth_skills_injected"] is False
    assert artifact["summary"]["release_metric_eligible"] is False
