"""每日动态挖掘作业入口 —— 模拟聚合源（BOSS直聘）的增量观测。

一次消费一个 1000 行分片，跑「读取 → 结构校验 → 去重 → 岗位归一 → 技能抽取 →
增量入图 → 日间对比 → 培训计划」，把漏斗、日间变化与新人培训顺序落进观测层三张表。

**默认试运行**：整条链路真跑一遍（含 LLM 补缺与增量计算），最后整体回滚，一行都不落库。
要真正写入必须显式 ``--apply``。

写库范围只有 skill / job_skill / evidence 三张表，且只 INSERT、status 恒为 candidate；
``job`` / ``raw_jd`` / ``crawl_batch`` / ``capability_change`` 与任何已存在的 job_skill 行
跑完必须与跑之前完全一致（原因见 app/services/mining.py 头部注释）。

用法（backend/ 目录下）：
    uv run python -X utf8 data/run_daily_mining.py --as-of 2026-09-03            # 试运行
    uv run python -X utf8 data/run_daily_mining.py --as-of 2026-09-03 --apply    # 落库
    uv run python -X utf8 data/run_daily_mining.py --as-of 2026-09-05 --backfill 3 --apply
    uv run python -X utf8 data/run_daily_mining.py --shard 7 --no-llm --rows 200

撤销某一天：``uv run python -X utf8 data/rollback_mining.py --run-date 2026-09-03 --apply``
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8")

from app.db import SessionLocal  # noqa: E402
from app.config import settings  # noqa: E402
from app.services.mining import run_daily_mining  # noqa: E402

STAGE_WIDTH = 12


def _print_funnel(summary: dict) -> None:
    print(f"\n=== {summary['run_date']}  分片 {summary['shard_index']:03d}"
          f"（原表行 {summary['cursor_start']}–{summary['cursor_end']}）"
          f"  {'试运行(未落库)' if summary['dry_run'] else '已落库'} ===")
    print(f"{'阶段':<{STAGE_WIDTH}}{'入':>7}{'出':>7}{'耗时ms':>9}  说明")
    for stage in summary.get("stage_log", []):
        label = stage.get("label", stage.get("key", ""))
        pad = STAGE_WIDTH - sum(2 if ord(c) > 127 else 1 for c in label)
        drops = "；".join(f"{k} {v}" for k, v in (stage.get("dropped") or {}).items())
        detail = stage.get("detail", "")
        if drops:
            detail = f"{detail}｜丢弃：{drops}" if detail else f"丢弃：{drops}"
        print(f"{label}{' ' * max(1, pad)}{stage.get('in_count', 0):>7}"
              f"{stage.get('out_count', 0):>7}{stage.get('duration_ms', 0):>9}  {detail}")

    print(f"\n漏斗：读入 {summary['rows_read']} → 有效 {summary['rows_valid']}"
          f" → 去重后 {summary['rows_dedup']} → 命中策展岗位 {summary['rows_mapped']}"
          f"（丢弃 {summary['rows_dropped']}）")
    print(f"LLM：{summary['llm_calls']} 次调用，"
          f"{summary['llm_prompt_tokens']}+{summary['llm_completion_tokens']} tokens，"
          f"¥{summary['llm_cost_cny']:.4f} / 预算 ¥{settings.mining_daily_budget_cny:.2f}"
          f"{'（已撞预算闸，剩余行降级为纯规则）' if summary['llm_budget_hit'] else ''}")
    print(f"入图：新建技能节点 {summary['skills_created']}、"
          f"候选能力关系 {summary['job_skills_created']}（status=candidate）、"
          f"证据 {summary['evidence_created']}；触达岗位 {summary['jobs_touched']} 个")
    counts = summary.get("delta_counts") or {}
    order = ("new", "support_up", "support_down", "vanished")
    detail = "、".join(f"{k} {counts[k]}" for k in order if counts.get(k)) or "无"
    print(f"日间变化：{summary['delta_total']} 条（{detail}）")


def _print_deltas(db, summary: dict, limit: int = 8) -> None:
    """打印当日变化样例；试运行已回滚，只能从返回摘要看总数。"""
    from app import models
    run = db.query(models.DailyMiningRun).filter(
        models.DailyMiningRun.run_date == summary["run_date"]).first()
    if run is None:
        return
    rows = db.query(models.DailySkillDelta, models.Job.name).join(
        models.Job, models.Job.id == models.DailySkillDelta.job_id).filter(
        models.DailySkillDelta.run_id == run.id).order_by(
        models.DailySkillDelta.delta_type, models.DailySkillDelta.id).limit(limit).all()
    if not rows:
        return
    print(f"\n变化样例（前 {len(rows)} 条）：")
    for delta, job_name in rows:
        plan = delta.training_plan or []
        steps = " → ".join(f"{s['step']}.{s['skill']}" for s in plan) or "—"
        print(f"  [{delta.delta_type:<12}] {job_name} / {delta.skill_name}"
              f"  支持 {delta.prev_support}→{delta.curr_support}"
              f"  领域数 {delta.industry_count}  状态 {delta.curr_status}")
        if plan:
            print(f"       培训顺序：{steps}")


def main() -> int:
    ap = argparse.ArgumentParser(description="每日动态挖掘（模拟聚合源）")
    ap.add_argument("--as-of", default=date.today().isoformat(),
                    help="运行日期 YYYY-MM-DD（默认今天，本地时区）")
    ap.add_argument("--apply", action="store_true", help="真正落库（默认试运行不落库）")
    ap.add_argument("--backfill", type=int, default=1,
                    help="连跑 N 天，以 --as-of 为最后一天，依次消费连续分片")
    ap.add_argument("--shard", type=int, default=None,
                    help="指定起始分片序号（默认接着上一次游标往下走）")
    ap.add_argument("--no-llm", action="store_true", help="关闭 LLM 补缺，纯规则抽取")
    ap.add_argument("--rows", type=int, default=None,
                    help=f"每日行数（默认 {settings.mining_rows_per_day}）")
    ap.add_argument("--force", action="store_true",
                    help="覆盖同日已有记录（该日若已落库需先跑 rollback_mining.py）")
    ap.add_argument("--json", action="store_true", help="额外输出机器可读摘要")
    args = ap.parse_args()

    try:
        last_day = datetime.strptime(args.as_of, "%Y-%m-%d").date()
    except ValueError:
        print(f"[mining] --as-of 格式应为 YYYY-MM-DD，收到 {args.as_of!r}", file=sys.stderr)
        return 2
    days = max(1, args.backfill)
    dates = [(last_day - timedelta(days=days - 1 - i)).isoformat() for i in range(days)]

    print(f"[mining] 目标库 {settings.db_name}｜来源 {settings.mining_source_label}"
          f"｜{'落库' if args.apply else '试运行'}｜{days} 天：{dates[0]} → {dates[-1]}")

    db = SessionLocal()
    summaries = []
    try:
        for i, run_date in enumerate(dates):
            shard = None if args.shard is None else args.shard + i
            summary = run_daily_mining(
                db, run_date=run_date, shard_index=shard, dry_run=not args.apply,
                use_llm=not args.no_llm, rows=args.rows, force=args.force)
            summaries.append(summary)
            _print_funnel(summary)
            if args.apply:
                _print_deltas(db, summary)
    except (FileNotFoundError, RuntimeError) as exc:
        print(f"\n[mining] 失败：{exc}", file=sys.stderr)
        return 1
    finally:
        db.close()

    if args.json:
        print("\n" + json.dumps(summaries, ensure_ascii=False, indent=2, default=str))
    if not args.apply:
        print("\n[mining] 以上为试运行结果，未写入任何表；确认无误后加 --apply")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
