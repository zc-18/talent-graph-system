# -*- coding: utf-8 -*-
"""按「标题真正属于本簇且角色一致」重选岗位定义文本（R6 收尾修复）。

岗位的 summary / core_responsibilities / typical_scenarios 是从**某一条** JD 的解析结果
整段复制来的，`ingest.build_graph_from_rows` 原先用「core_responsibilities 条数最多」挑这
条代表 JD。条数最多 ≠ 最有代表性：只要有一条跑偏的 JD 写得够长，它就会定义整个岗位。实测
后果是岗位详情页展示完全不相干的职责与行业场景：

    自动驾驶算法工程师   → 金融信贷风控 / 评分卡模型开发 / NLP文本处理
    车联网系统工程师     → 半导体采购与供应商管理 / Foundry 供应链
    人工智能数字人训练师 → 电商库存管理 / 大促备货调拨
    工业互联网工程师     → 电芯性能分析 / 热管理仿真 / 电化学建模

选取口径与 `ingest._representative_parse` 完全一致，两级偏好后再比条数：

1. **同域**：该 JD 自身标题经 `title_key` 归一化后等于本岗位。这里**刻意不传
   `cluster_hint`**——hint 记录的是采集时用的检索词，而「检索词命中正文而非标题」正是
   跑偏 JD 混进簇里的原因；信任它等于把要排除的行重新放进来（实测：信任 hint 时
   40 条里 39 条“合格”，只看标题时只剩 12 条）。
2. **同角色**：标题里含本岗位的角色名词（算法工程师／系统工程师／训练师…）。否则
   「项目管理资深工程师（自动驾驶&智能座舱）」这类同域异角色的 JD 会代表算法岗。

注意这只决定「哪一条 JD 替岗位说话」，**不改变任何 JD 的簇归属**——跑偏 JD 的能力项与
证据链保持原样，本脚本不重建图谱、不碰能力关系、不碰置信度与闸门，只改两个文本字段
（summary 含 JD 条数统计，保留原值）。

解析结果全部来自 `data/parsed_cache_real.json`（按 JD 正文 hash 命中），**不发起任何 LLM
调用**；同域同角色且命中缓存的 JD 为 0 时直接跳过该岗位、保持原值，不猜。

用法（backend/ 目录）：

    uv run python -X utf8 data/repair_job_definition_r6.py                 # dry-run，零写入
    uv run python -X utf8 data/repair_job_definition_r6.py --apply         # 写库（先备份）
    uv run python -X utf8 data/repair_job_definition_r6.py --jobs "自动驾驶算法工程师"

2026-08-30 已对上表 4 个岗位执行 --apply（备份 data/backup/job_definition_r6_*.json）。
全库 dry-run 另有约 15 个岗位会变动，多为横向替换而非明确改善，未执行。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import models  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.services import cleaning, ingest  # noqa: E402

CACHE = Path(__file__).resolve().parent / "parsed_cache_real.json"
BACKUP_DIR = Path(__file__).resolve().parent / "backup"
FIELDS = ("summary", "core_responsibilities", "typical_scenarios")


def _load_cache() -> dict:
    if not CACHE.exists():
        raise SystemExit(f"缺少解析缓存，无法在不调用 LLM 的情况下修复：{CACHE}")
    return json.loads(CACHE.read_text(encoding="utf-8"))


def _attached_raw_jds(db, job: models.Job) -> list[models.RawJD]:
    relations = db.query(models.JobSkill).filter(
        models.JobSkill.job_id == job.id).all()
    if not relations:
        return []
    raw_ids = {row.raw_jd_id for row in db.query(models.Evidence).filter(
        models.Evidence.job_skill_id.in_([r.id for r in relations])).all()
        if row.raw_jd_id}
    if not raw_ids:
        return []
    return db.query(models.RawJD).filter(models.RawJD.id.in_(raw_ids)).all()


def _on_title(raws: list[models.RawJD], job_name: str) -> list[models.RawJD]:
    """Keep only JDs whose own title normalizes to this job's cluster."""
    out = []
    for row in raws:
        title = (row.job_title or "").strip()
        if not title:
            continue
        try:
            # No cluster_hint: the hint is the collection query, and a query that matched body
            # text is precisely how an off-topic JD joins the cluster. See ingest._representative_parse.
            if ingest.title_key(title) == job_name:
                out.append(row)
        except Exception:
            continue
    return out


def plan(db, cache: dict, wanted: set[str]) -> list[dict]:
    results = []
    for job in db.query(models.Job).order_by(models.Job.id).all():
        if wanted and job.name not in wanted:
            continue
        raws = _attached_raw_jds(db, job)
        candidates = _on_title(raws, job.name)
        parsed = []
        for row in candidates:
            hit = cache.get(cleaning.exact_hash(row.raw_text or ""))
            if hit:
                parsed.append((row, hit))
        if not parsed:
            results.append({"job": job, "status": "no_on_title_parse",
                            "attached": len(raws), "on_title": len(candidates)})
            continue
        # Same two-stage preference as ingest._representative_parse: on-domain first,
        # then on-role, then richest. Keeps the repaired value identical to what a
        # future rebuild would produce, so this is a backfill and not a fork.
        pool = parsed
        noun = ingest._role_noun(job.name)
        if noun:
            pool = [p for p in parsed if noun in (p[0].job_title or "")] or parsed
        row, best = max(pool, key=lambda p: len(p[1].get("core_responsibilities") or []))
        new = {
            "summary": job.summary,          # summary 含 JD 条数统计，保留原值不动
            "core_responsibilities": [s for s in (best.get("core_responsibilities") or []) if s],
            "typical_scenarios": [s for s in (best.get("typical_scenarios") or []) if s][:6],
        }
        changed = [f for f in ("core_responsibilities", "typical_scenarios")
                   if (getattr(job, f) or []) != new[f]]
        results.append({
            "job": job, "status": "change" if changed else "same",
            "attached": len(raws), "on_title": len(candidates), "parsed": len(parsed),
            "source_title": row.job_title, "source_raw_jd_id": row.id,
            "changed_fields": changed, "new": new,
        })
    return results


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="写库（默认 dry-run）")
    ap.add_argument("--jobs", default="", help="逗号分隔岗位名，缺省=全部")
    args = ap.parse_args(argv)
    wanted = {j.strip() for j in args.jobs.split(",") if j.strip()}

    cache = _load_cache()
    db = SessionLocal()
    try:
        rows = plan(db, cache, wanted)
        changes = [r for r in rows if r["status"] == "change"]
        blocked = [r for r in rows if r["status"] == "no_on_title_parse"]
        for r in changes:
            job = r["job"]
            print(f"\n=== {job.name} (id={job.id})  关联JD={r['attached']} "
                  f"标题同簇={r['on_title']} 命中缓存={r['parsed']}")
            print(f"  代表JD: raw_jd_id={r['source_raw_jd_id']} 《{r['source_title']}》")
            for field in r["changed_fields"]:
                old, new = getattr(job, field) or [], r["new"][field]
                print(f"  - {field}:")
                print(f"      旧: {old[:3]}")
                print(f"      新: {new[:3]}")
        for r in blocked:
            print(f"\n[跳过] {r['job'].name}：标题同簇且命中缓存的 JD 为 0 "
                  f"（关联{r['attached']} / 同簇{r['on_title']}）——不猜，保持原值")
        print(f"\n合计：待修改 {len(changes)} 个岗位 / 无需改动 "
              f"{sum(1 for r in rows if r['status']=='same')} / 跳过 {len(blocked)}")

        if not args.apply:
            db.rollback()
            print("[dry-run] 零写入")
            return 0
        if not changes:
            print("没有需要写入的改动")
            return 0

        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = BACKUP_DIR / f"job_definition_r6_{stamp}.json"
        if path.exists():
            raise SystemExit(f"备份文件已存在，拒绝覆盖：{path}")
        path.write_text(json.dumps({
            "generated_at": stamp, "database": db.get_bind().url.database,
            "jobs": [{"id": r["job"].id, "name": r["job"].name,
                      **{f: getattr(r["job"], f) for f in FIELDS}} for r in changes],
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n备份已写入：{path}")

        for r in changes:
            for field in r["changed_fields"]:
                setattr(r["job"], field, r["new"][field])
        db.flush()
        # commit 前自检：每个改过的岗位都必须与代表 JD 的解析结果逐字一致
        errors = []
        for r in changes:
            for field in r["changed_fields"]:
                if (getattr(r["job"], field) or []) != r["new"][field]:
                    errors.append(f"{r['job'].name}.{field} 未按计划写入")
        if errors:
            db.rollback()
            raise SystemExit("commit 前自检失败：" + "; ".join(errors))
        db.commit()
        print(f"[committed] {len(changes)} 个岗位定义已按同簇代表 JD 回填")
        return 0
    except Exception as exc:
        db.rollback()
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
