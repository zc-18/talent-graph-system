"""R6 权威佐证补录（严格考证版）—— 默认 dry-run，必须 --apply 才写库。

背景：`external`（外部验证）是置信度公式里唯一的 0/1 二值因子
（`app/services/confidence.py` 的 `factors_from_jd(has_web=...)`）。
`app/services/confidence_batch.py` 只要该岗位存在一条 `publish_date <= as_of`
且 `url` 或 `local_file` 非空的 AuthorityEvidence，`has_authority` 即为 True，
并作用于该岗位**每一条** active 关系；岗位 confidence 是各关系 confidence 的
加权均值，因此补一条合规佐证 = 该岗位 confidence 精确 +0.100。

**本脚本只写 authority_evidence 一张表**，不碰 Job / JobSkill / Evidence /
JobVersion / CapabilityChange，因此不经过 `graph_service.upsert_job` 那条
「先清空再重建」的破坏性写路径。

数据来源：`data/authority/authority_sources_r6.json`，由
`data/authority/build_authority_r6.py` 从已归档的人社部官方 PDF 正文**逐字切片**
生成（非人工撰写）。找不到一手出处的岗位一律不收录 —— 录一条指向不存在文件的
佐证，比不补更糟。

用法（backend/ 下，务必先 `$env:DB_NAME='talent_graph_v3'`）：
    uv run python -X utf8 data/seed_authority_r6.py                # dry-run，打印计划
    uv run python -X utf8 data/seed_authority_r6.py --apply        # 写库
    uv run python -X utf8 data/seed_authority_r6.py --apply --recalculate  # 写库并重算置信度

幂等：以 (job_id, title) 判重，重复执行不会插入第二条。
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import models  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.services.confidence_batch import run_confidence_recalculation  # noqa: E402

BACKEND = Path(__file__).resolve().parents[1]
REGISTRY = BACKEND / "data" / "authority" / "authority_sources_r6.json"


def _load_registry() -> dict:
    if not REGISTRY.exists():
        raise SystemExit(f"登记表不存在：{REGISTRY}\n"
                         f"先运行 data/authority/build_authority_r6.py 生成。")
    return json.loads(REGISTRY.read_text(encoding="utf-8"))


def _parse_date(value: str | None) -> datetime | None:
    """publish_date 允许为空：confidence_batch 对 NULL 同样判定有效。"""
    if not value:
        return None
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d")
    except ValueError:
        raise SystemExit(f"publish_date 格式非法：{value!r}")


def _preflight(entries: dict[str, dict]) -> list[str]:
    """本地快照必须真实存在且非空 —— 这是「沉淀得下来才录」的硬闸。"""
    problems = []
    for job_name, entry in entries.items():
        for field in ("title", "issuer", "url", "excerpt", "local_file"):
            if not (entry.get(field) or "").strip():
                problems.append(f"{job_name}: 字段 {field} 为空")
        rel = (entry.get("local_file") or "").strip()
        if not rel:
            continue
        path = BACKEND / rel
        if not path.exists():
            problems.append(f"{job_name}: 本地快照不存在 {rel}")
        elif path.stat().st_size < 1024:
            problems.append(f"{job_name}: 本地快照过小（{path.stat().st_size}B）{rel}")
    return problems


def _existing(db, job_id: int, title: str):
    return db.query(models.AuthorityEvidence).filter(
        models.AuthorityEvidence.job_id == job_id,
        models.AuthorityEvidence.title == title[:250]).first()


def _resolve_jobs(db, entries: dict[str, dict]) -> tuple[dict[str, models.Job], list[str]]:
    names = list(entries)
    jobs = {row.name: row for row in db.query(models.Job).filter(
        models.Job.name.in_(names), models.Job.status == "published").all()}
    return jobs, [n for n in names if n not in jobs]


def _plan(db, entries: dict[str, dict]) -> dict:
    jobs, missing_jobs = _resolve_jobs(db, entries)
    rows = []
    for job_name, entry in entries.items():
        job = jobs.get(job_name)
        if not job:
            continue
        already_has_any = db.query(models.AuthorityEvidence).filter(
            models.AuthorityEvidence.job_id == job.id).count()
        rows.append({
            "job": job_name,
            "job_id": job.id,
            "confidence_now": round(float(job.confidence or 0.0), 4),
            "confidence_after_expected": round(float(job.confidence or 0.0) + 0.10, 4),
            "authority_rows_now": already_has_any,
            "action": "skip(已存在同名佐证)" if _existing(db, job.id, entry["title"]) else "insert",
            "title": entry["title"],
            "local_file": entry["local_file"],
        })
    inserts = [r for r in rows if r["action"] == "insert"]
    total = db.query(models.Job).filter(models.Job.status == "published").count()
    avg_now = sum(float(r.confidence or 0.0) for r in db.query(models.Job).filter(
        models.Job.status == "published").all()) / max(1, total)
    return {
        "mode": "dry-run",
        "writes": False,
        "registry": str(REGISTRY.relative_to(BACKEND)),
        "preflight_problems": _preflight(entries),
        "jobs_not_found_in_db": missing_jobs,
        "planned_inserts": len(inserts),
        "expected_avg_confidence": {
            "before": round(avg_now, 4),
            "after": round(avg_now + 0.10 * len(inserts) / max(1, total), 4),
            "note": "仅在随后跑一次置信度重算（--recalculate 或每日 02:30 批算）后生效",
        },
        "rows": rows,
    }


def apply_seed(db, entries: dict[str, dict]) -> dict:
    problems = _preflight(entries)
    if problems:
        raise SystemExit("预检失败，未写入任何数据：\n  " + "\n  ".join(problems))
    jobs, missing_jobs = _resolve_jobs(db, entries)
    if missing_jobs:
        raise SystemExit(f"以下岗位在库中不存在（或非 published），拒绝写入：{missing_jobs}")

    before = {row.id: float(row.confidence or 0.0) for row in jobs.values()}
    inserted, skipped = [], []
    for job_name, entry in entries.items():
        job = jobs[job_name]
        if _existing(db, job.id, entry["title"]):
            skipped.append(job_name)
            continue
        db.add(models.AuthorityEvidence(
            job_id=job.id,
            kind=entry.get("kind", "policy"),
            title=entry["title"][:250],
            issuer=entry["issuer"][:120],
            publish_date=_parse_date(entry.get("publish_date")),
            url=entry["url"][:500],
            excerpt=entry["excerpt"],
            local_file=entry["local_file"][:250],
        ))
        inserted.append(job_name)
    db.commit()
    return {"inserted": inserted, "skipped": skipped, "confidence_before": before}


def _verify(db, entries: dict[str, dict]) -> dict:
    """写后断言：每个岗位都拿得到一条 has_authority 判定为真的佐证。"""
    jobs, missing_jobs = _resolve_jobs(db, entries)
    assert not missing_jobs, f"岗位缺失：{missing_jobs}"
    as_of = datetime.utcnow()
    checked = {}
    for job_name, entry in entries.items():
        job = jobs[job_name]
        row = _existing(db, job.id, entry["title"])
        assert row is not None, f"{job_name}: 佐证未写入"
        # 与 confidence_batch.py 的 has_authority 判定条件逐条对齐
        assert (row.publish_date is None or row.publish_date <= as_of), \
            f"{job_name}: publish_date 晚于当前时间，批算会忽略它"
        assert bool((row.url or "").strip() or (row.local_file or "").strip()), \
            f"{job_name}: url 与 local_file 同时为空，批算会忽略它"
        assert (BACKEND / row.local_file).exists(), f"{job_name}: 本地快照丢失 {row.local_file}"
        # 幂等断言：同一 (job_id, title) 只能有一条
        n = db.query(models.AuthorityEvidence).filter(
            models.AuthorityEvidence.job_id == job.id,
            models.AuthorityEvidence.title == entry["title"][:250]).count()
        assert n == 1, f"{job_name}: 同名佐证有 {n} 条，幂等被破坏"
        checked[job_name] = {"authority_evidence_id": row.id,
                             "has_authority_ready": True}
    total_rows = db.query(models.AuthorityEvidence).count()
    return {"verified_jobs": len(checked), "authority_evidence_total": total_rows,
            "detail": checked}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true",
                        help="写入 authority_evidence；默认 dry-run")
    parser.add_argument("--recalculate", action="store_true",
                        help="写入后立刻跑一次五因子置信度重算（需 --apply）")
    args = parser.parse_args()
    if args.recalculate and not args.apply:
        parser.error("--recalculate 需要配合 --apply")

    registry = _load_registry()
    entries: dict[str, dict] = registry["sources"]
    print(f"[seed_authority_r6] DB_NAME={os.getenv('DB_NAME', '(default)')} "
          f"| 登记表 {len(entries)} 岗位", file=sys.stderr)

    db = SessionLocal()
    try:
        if not args.apply:
            print(json.dumps(_plan(db, entries), ensure_ascii=False, indent=2))
            return
        result = apply_seed(db, entries)
        confidence_run = None
        if args.recalculate:
            confidence_run = run_confidence_recalculation(
                db, trigger="authority_r6")
        payload = {
            "mode": "applied", "writes": True,
            "inserted": result["inserted"], "skipped": result["skipped"],
            "confidence_run": confidence_run,
            "verification": _verify(db, entries),
        }
        if not args.recalculate:
            payload["reminder"] = ("佐证已写入，但 job.confidence 要等一次置信度重算才会变化："
                                   "重跑本脚本加 --recalculate，或等每日 02:30(CST) 的 scheduled 批算。")
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
