"""每日动态挖掘（模拟聚合源）观测路由。

本路由**只读**三张观测表（DailyMiningRun / DailyMiningItem / DailySkillDelta），
不触发任何挖掘、不写任何行——演示站是公开可点的，历史上两次图谱损坏都源于访客
点按钮触发了写路径，所以这里连 `require_write` 都不需要引入：没有写。

它也不 import `services/mining.py`：观测层的形状由 ORM 模型定义，读侧不该依赖
夜间作业的实现。
"""
from __future__ import annotations

import json
import time
from collections import defaultdict
from datetime import datetime
from typing import Any, Iterator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import models
from ..config import settings
from ..db import SessionLocal, get_db

router = APIRouter(prefix="/api/mining", tags=["mining"])

TIER = "simulated"
DEFAULT_PLATFORM = "boss_sim"

# 漏斗的六个阶段。夜间作业按同样的 key 记 stage_log；这里的 label 只在
# stage_log 缺失（老运行、失败运行）时兜底，正常路径以记录值为准。
STAGE_LABELS: dict[str, str] = {
    "read": "抓取岗位数据",
    "validate": "结构校验与正文长度门",
    "dedup": "去重",
    "map": "岗位归一",
    "extract": "技能抽取",
    "write": "增量入图",
}

# 回放节奏：总时长目标与单阶段上下限（秒）
REPLAY_TOTAL_SECONDS = 23.0
REPLAY_STAGE_MIN = 1.5
REPLAY_STAGE_MAX = 6.0
REPLAY_TICKS_MIN = 6
REPLAY_TICKS_MAX = 10

MAX_JOBS = 40
MAX_DELTAS_PER_JOB = 40
TOP_SKILLS = 15


# --------------------------------------------------------------------------- helpers
def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _run_item(run: models.DailyMiningRun) -> dict:
    """列表视图的一行：漏斗计数 + 成本 + 写入量。"""
    return {
        "run_date": run.run_date,
        "status": run.status,
        "rows_read": _int(run.rows_read),
        "rows_valid": _int(run.rows_valid),
        "rows_dedup": _int(run.rows_dedup),
        "rows_mapped": _int(run.rows_mapped),
        "jobs_touched": _int(run.jobs_touched),
        "new_skill_points": _int(run.new_skill_points),
        "skills_created": _int(run.skills_created),
        "job_skills_created": _int(run.job_skills_created),
        "evidence_created": _int(run.evidence_created),
        "llm_calls": _int(run.llm_calls),
        "llm_cost_cny": round(float(run.llm_cost_cny or 0.0), 4),
        "llm_budget_hit": bool(run.llm_budget_hit),
        "dry_run": bool(run.dry_run),
        "finished_at": _iso(run.finished_at),
    }


def _run_detail(run: models.DailyMiningRun) -> dict:
    """详情视图：列表字段 + 分片游标 + 起止时间 + 错误。"""
    detail = _run_item(run)
    detail.update({
        "shard_index": _int(run.shard_index),
        "cursor_start": _int(run.cursor_start),
        "cursor_end": _int(run.cursor_end),
        "started_at": _iso(run.started_at),
        "error": run.error,
        "source_label": run.source_label or settings.mining_source_label,
        "platform": run.platform or DEFAULT_PLATFORM,
    })
    return detail


def _stage_entries(run: models.DailyMiningRun) -> list[dict]:
    """把 stage_log 归一为有序的 dict 列表；缺失/损坏时返回空列表。"""
    raw = run.stage_log
    if isinstance(raw, str):                       # 个别驱动会把 JSON 列读成字符串
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            raw = None
    if not isinstance(raw, list):
        return []
    entries = [e for e in raw if isinstance(e, dict)]
    # order 缺失时退回原始顺序（enumerate 的下标做次键，保证稳定）
    return [e for _, e in sorted(enumerate(entries),
                                 key=lambda p: (_int(p[1].get("order", p[0])), p[0]))]


def _funnel_from_stage_log(entries: list[dict]) -> list[dict]:
    funnel = []
    for entry in entries:
        key = str(entry.get("key") or "")
        dropped = entry.get("dropped") or {}
        if not isinstance(dropped, dict):
            dropped = {}
        reasons = {str(k): _int(v) for k, v in dropped.items()}
        in_count = _int(entry.get("in_count"))
        out_count = _int(entry.get("out_count"))
        samples = entry.get("samples") or []
        # 夜间作业记 stage_log 时**总会**写 dropped 键（_stage_entry 保证），空字典
        # 就是「这一级一条都没丢」的如实记录。所以不能用 `sum(...) or max(0, in-out)`：
        # `or` 会把合法的 0 当成缺失而回落去减，而各级的 in/out 未必同单位。
        # 只有真正没有 dropped 键的老运行才走减法兜底。
        has_dropped = isinstance(entry.get("dropped"), dict)
        funnel.append({
            "key": key,
            # 已落库的旧批次仍保存着历史 label；公开回放按稳定 key 使用当前展示名。
            "label": STAGE_LABELS.get(key) or entry.get("label") or key,
            "in": in_count,
            "out": out_count,
            # 记录的 dropped 明细优先；没有明细就用 in-out 兜底，两者不该打架
            "dropped": (sum(reasons.values()) if has_dropped
                        else max(0, in_count - out_count)),
            "reasons": reasons,
            "detail": ("完成本批次岗位数据抓取" if key == "read"
                       else entry.get("detail") or ""),
            "duration_ms": _int(entry.get("duration_ms")),
            "samples": samples if isinstance(samples, list) else [],
        })
    return funnel


def _funnel_from_counters(run: models.DailyMiningRun) -> list[dict]:
    """stage_log 缺失时的降级漏斗：只用计数列，不编造丢弃原因。

    夜间作业早期版本没记 stage_log，失败运行也可能只写了一半——这条路径保证
    详情页仍然能画出漏斗，而不是 500。
    """
    read = _int(run.rows_read)
    valid = _int(run.rows_valid)
    dedup = _int(run.rows_dedup)
    mapped = _int(run.rows_mapped)
    cost = round(float(run.llm_cost_cny or 0.0), 4)
    stages = [
        ("read", read, read, "完成当日岗位数据抓取"),
        ("validate", read, valid, "结构校验与正文长度门"),
        ("dedup", valid, dedup, "同源重复行去重"),
        ("map", dedup, mapped, "归一到策展岗位"),
        ("extract", mapped, mapped,
         f"LLM 调用 {_int(run.llm_calls)} 次，成本 ¥{cost}"),
        ("write", mapped, mapped,
         f"新增技能 {_int(run.skills_created)} 项 / 岗位技能 {_int(run.job_skills_created)} 条 / "
         f"证据 {_int(run.evidence_created)} 条（均为 candidate 态）"),
    ]
    return [{
        "key": key, "label": STAGE_LABELS[key], "in": in_c, "out": out_c,
        "dropped": max(0, in_c - out_c), "reasons": {},
        "detail": detail, "duration_ms": 0, "samples": [],
    } for key, in_c, out_c, detail in stages]


def _funnel(run: models.DailyMiningRun) -> list[dict]:
    entries = _stage_entries(run)
    return _funnel_from_stage_log(entries) if entries else _funnel_from_counters(run)


def _delta_payload(delta: models.DailySkillDelta) -> dict:
    industries = delta.industries if isinstance(delta.industries, list) else []
    samples = delta.sample_titles if isinstance(delta.sample_titles, list) else []
    plan = delta.training_plan if isinstance(delta.training_plan, list) else []
    return {
        "skill_name": delta.skill_name,
        # skill_id 为空 = 这个技能词只被观测到、**没有进图谱**：它没能和任何粗粒度
        # 概念共现（见 services/mining.py 里 PMI 挂父的说明），所以没建 Skill 节点，
        # 也就没有能力关系。前端必须把这种行渲染成「仅观测·未入图」，
        # 不能显示成普通技能点，更不能给它链到一个不存在的技能详情页。
        "skill_id": delta.skill_id,
        "in_graph": delta.skill_id is not None,
        "delta_type": delta.delta_type,
        "prev_support": _int(delta.prev_support),
        "curr_support": _int(delta.curr_support),
        "prev_status": delta.prev_status,
        "curr_status": delta.curr_status,
        # industry_count 是弱独立性信号，前端必须按「公司领域」而不是「雇主」渲染
        "industry_count": _int(delta.industry_count),
        "industries": industries,
        "sample_titles": samples,
        "training_plan": plan,
    }


def _resolve_run(db: Session, run_date: str) -> models.DailyMiningRun:
    """run_date 支持 YYYY-MM-DD 或字面量 latest；latest 只返回可信的成功批次。"""
    query = db.query(models.DailyMiningRun)
    if run_date == "latest":
        run = query.filter(models.DailyMiningRun.status == "completed").order_by(
            models.DailyMiningRun.run_date.desc()).first()
    else:
        run = query.filter(models.DailyMiningRun.run_date == run_date).first()
    if not run:
        raise HTTPException(404, "该日期没有挖掘记录")
    return run


def _group_deltas(deltas: list[models.DailySkillDelta]) -> dict[int, list[models.DailySkillDelta]]:
    grouped: dict[int, list[models.DailySkillDelta]] = defaultdict(list)
    for delta in deltas:
        grouped[delta.job_id].append(delta)
    return grouped


_DELTA_ORDER = {"new": 0, "support_up": 1, "support_down": 2, "vanished": 3}


def _job_block(job_id: int, job: Any, rows: int,
               deltas: list[models.DailySkillDelta]) -> dict:
    counts = {"new": 0, "support_up": 0, "support_down": 0, "vanished": 0}
    for delta in deltas:
        if delta.delta_type in counts:
            counts[delta.delta_type] += 1
    ordered = sorted(deltas, key=lambda d: (_DELTA_ORDER.get(d.delta_type, 9),
                                            -_int(d.curr_support),
                                            d.skill_name or ""))
    block = {
        "job_id": job_id,
        "job_name": job.name if job is not None else f"岗位#{job_id}",
        "category": getattr(job, "category", None),
        "rows": rows,
        "new_count": counts["new"],
        "support_up": counts["support_up"],
        "support_down": counts["support_down"],
        "vanished": counts["vanished"],
        "deltas": [_delta_payload(d) for d in ordered[:MAX_DELTAS_PER_JOB]],
    }
    if len(ordered) > MAX_DELTAS_PER_JOB:
        # 截断必须说出来：静默砍掉变更项等于伪造「当日只有这么多变化」
        block["truncated"] = True
        block["deltas_total"] = len(ordered)
    return block


def _load_jobs(db: Session, job_ids: set[int]) -> dict[int, Any]:
    """一次批量取 Job，避免逐个 db.get 的 N+1。"""
    if not job_ids:
        return {}
    rows = db.query(models.Job.id, models.Job.name, models.Job.category).filter(
        models.Job.id.in_(job_ids)).all()
    return {row.id: row for row in rows}


def _job_blocks_for_run(db: Session, run: models.DailyMiningRun) -> tuple[list[dict], list, dict]:
    """构造当日岗位变化并稳定排序，详情与分页接口共享同一口径。"""
    deltas = (db.query(models.DailySkillDelta)
              .filter(models.DailySkillDelta.run_id == run.id).all())
    row_counts = dict(db.query(models.DailyMiningItem.job_id,
                               func.count(models.DailyMiningItem.id))
                      .filter(models.DailyMiningItem.run_id == run.id,
                              models.DailyMiningItem.job_id.isnot(None),
                              models.DailyMiningItem.drop_reason.is_(None))
                      .group_by(models.DailyMiningItem.job_id).all())
    grouped = _group_deltas(deltas)
    job_ids = set(grouped) | set(row_counts)
    jobs = _load_jobs(db, job_ids)
    blocks = [_job_block(job_id, jobs.get(job_id), _int(row_counts.get(job_id)),
                         grouped.get(job_id, []))
              for job_id in job_ids]
    blocks.sort(key=lambda block: (-block["new_count"], -block["rows"], block["job_id"]))
    return blocks, deltas, jobs


# --------------------------------------------------------------------------- endpoints
@router.get("/runs")
def list_runs(limit: int = 30, db: Session = Depends(get_db)):
    """每日挖掘台账列表（最新在前）。零运行时返回空 items，不报错。"""
    limit = min(90, max(1, limit))
    runs = (db.query(models.DailyMiningRun)
            .order_by(models.DailyMiningRun.run_date.desc())
            .limit(limit).all())
    latest = runs[0] if runs else None
    return {
        "source_label": (latest.source_label if latest and latest.source_label
                         else settings.mining_source_label),
        "platform": (latest.platform if latest and latest.platform else DEFAULT_PLATFORM),
        "tier": TIER,
        "enabled": bool(settings.mining_enabled),
        "schedule": (f"每日 {settings.mining_scheduler_hour:02d}:"
                     f"{settings.mining_scheduler_minute:02d} (Asia/Shanghai)"),
        "daily_budget_cny": round(float(settings.mining_daily_budget_cny or 0.0), 4),
        "items": [_run_item(run) for run in runs],
    }


@router.get("/runs/{run_date}")
def run_detail(run_date: str, db: Session = Depends(get_db)):
    """某日挖掘详情：漏斗 + 按岗位的技能点变化 + 当日热点技能。"""
    run = _resolve_run(db, run_date)

    blocks, deltas, jobs = _job_blocks_for_run(db, run)
    jobs_total = len(blocks)
    jobs_truncated = jobs_total > MAX_JOBS
    blocks = blocks[:MAX_JOBS]

    top = sorted(deltas, key=lambda d: (-_int(d.curr_support),
                                        _DELTA_ORDER.get(d.delta_type, 9),
                                        d.skill_name or ""))[:TOP_SKILLS]
    top_skills = [{
        "name": d.skill_name,
        "count": _int(d.curr_support),
        "job_id": d.job_id,
        "job_name": jobs[d.job_id].name if d.job_id in jobs else f"岗位#{d.job_id}",
        "delta_type": d.delta_type,
        "skill_id": d.skill_id,
        "in_graph": d.skill_id is not None,
    } for d in top]

    return {
        "run": _run_detail(run),
        "funnel": _funnel(run),
        "jobs": blocks,
        "jobs_total": jobs_total,
        "jobs_truncated": jobs_truncated,
        "top_skills": top_skills,
    }


@router.get("/runs/{run_date}/jobs")
def run_jobs(run_date: str, page: int = 1, size: int = 4,
             db: Session = Depends(get_db)):
    """某日岗位技能点变化分页；不影响采集回放和其余详情数据。"""
    page, size = max(1, page), min(20, max(1, size))
    run = _resolve_run(db, run_date)
    blocks, _, _ = _job_blocks_for_run(db, run)
    total = len(blocks)
    start = (page - 1) * size
    return {"items": blocks[start:start + size], "total": total,
            "page": page, "size": size}


@router.get("/skill-trend")
def skill_trend(days: int = 30, db: Session = Depends(get_db)):
    """新增技能点的日度趋势（由旧到新，直接喂图表 X 轴）。"""
    days = min(180, max(1, days))
    runs = (db.query(models.DailyMiningRun)
            .filter(models.DailyMiningRun.status == "completed")
            .order_by(models.DailyMiningRun.run_date.desc())
            .limit(days).all())
    runs.reverse()
    items = []
    cumulative = 0
    for run in runs:
        cumulative += _int(run.new_skill_points)
        items.append({
            "run_date": run.run_date,
            "new_skill_points": _int(run.new_skill_points),
            "rows_mapped": _int(run.rows_mapped),
            "skills_created": _int(run.skills_created),
            "cumulative_new": cumulative,     # 窗口内累计，不是全历史累计
        })
    return {"items": items}


@router.get("/jobs/{job_id}/deltas")
def job_deltas(job_id: int, limit: int = 30, db: Session = Depends(get_db)):
    """某岗位按日聚合的技能点变化（最新在前）。"""
    limit = min(90, max(1, limit))
    job = db.query(models.Job.id, models.Job.name).filter(
        models.Job.id == job_id).first()
    if not job:
        raise HTTPException(404, "岗位不存在")

    # 先定位该岗位有变化的最近 N 天，再一次性把这些天的 delta 全取回来（2 次查询）
    run_rows = (db.query(models.DailyMiningRun.id, models.DailyMiningRun.run_date)
                .join(models.DailySkillDelta,
                      models.DailySkillDelta.run_id == models.DailyMiningRun.id)
                .filter(models.DailySkillDelta.job_id == job_id)
                .group_by(models.DailyMiningRun.id, models.DailyMiningRun.run_date)
                .order_by(models.DailyMiningRun.run_date.desc())
                .limit(limit).all())
    if not run_rows:
        return {"job_id": job_id, "job_name": job.name, "items": []}

    run_ids = [row.id for row in run_rows]
    deltas = (db.query(models.DailySkillDelta)
              .filter(models.DailySkillDelta.job_id == job_id,
                      models.DailySkillDelta.run_id.in_(run_ids))
              .all())
    by_run: dict[int, list[models.DailySkillDelta]] = defaultdict(list)
    for delta in deltas:
        by_run[delta.run_id].append(delta)

    items = []
    for row in run_rows:
        block = _job_block(job_id, job, 0, by_run.get(row.id, []))
        item = {
            "run_date": row.run_date,
            "new_count": block["new_count"],
            "support_up": block["support_up"],
            "support_down": block["support_down"],
            "vanished": block["vanished"],
            "deltas": block["deltas"],
        }
        if block.get("truncated"):
            item["truncated"] = True
            item["deltas_total"] = block["deltas_total"]
        items.append(item)
    return {"job_id": job_id, "job_name": job.name, "items": items}


# --------------------------------------------------------------------------- replay (SSE)
def _plan_durations(weights: list[float], total: float = REPLAY_TOTAL_SECONDS,
                    lo: float = REPLAY_STAGE_MIN,
                    hi: float = REPLAY_STAGE_MAX) -> list[float]:
    """按记录的 duration_ms 比例分配回放时长，并夹在 [lo, hi] 内。

    直接按比例会让某个几秒的阶段独占整段动画；先夹再把余量在未夹住的阶段里
    重分配（注水法），总时长仍落在目标附近。
    """
    n = len(weights)
    if n == 0:
        return []
    total = min(max(total, lo * n), hi * n)
    durations = [lo] * n
    pending = set(range(n))
    budget = total
    while pending:
        wsum = sum(max(0.0, weights[i]) for i in pending)
        capped: list[int] = []
        for i in pending:
            if wsum > 0:
                share = budget * max(0.0, weights[i]) / wsum
            else:
                share = budget / len(pending)
            if share <= lo:
                durations[i] = lo
                capped.append(i)
            elif share >= hi:
                durations[i] = hi
                capped.append(i)
            else:
                durations[i] = share
        if not capped:
            break
        for i in capped:
            budget -= durations[i]
            pending.discard(i)
        if budget <= 0:
            for i in pending:
                durations[i] = lo
            break
    return [min(hi, max(lo, d)) for d in durations]


def _sample_text(sample: Any) -> str:
    """stage_log 的 samples 可能是字符串，也可能是带标题的对象。"""
    if isinstance(sample, dict):
        for key in ("title", "title_raw", "text", "name", "skill", "value"):
            if sample.get(key):
                return str(sample[key])[:80]
        return json.dumps(sample, ensure_ascii=False)[:80]
    return str(sample)[:80]


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


@router.get("/runs/{run_date}/replay")
def replay(run_date: str):
    """按录制的 stage_log 节奏回放当日挖掘过程（SSE）。

    **只回放，不重跑**：帧全部来自已落库的 stage_log。演示站公开可点，任何
    「点一下就开始挖」的设计都会重演历史上的图谱损坏事故。

    这里不用 Depends(get_db)：StreamingResponse 会把依赖的会话一直握到流结束
    （二十多秒），而连接池只有 5 条。所以自建会话、取完数据立刻关闭，生成器只
    闭包纯 Python 数据，绝不跨 time.sleep 持有连接。
    """
    db = SessionLocal()
    try:
        run = _resolve_run(db, run_date)
        actual_date = run.run_date
        source_label = run.source_label or settings.mining_source_label
        # stage_log 为空时不做计数兜底：回放的前提是「有录像」，没有就直接收尾
        stages = _funnel_from_stage_log(_stage_entries(run))
        summary = {
            "type": "summary",
            "new_skill_points": _int(run.new_skill_points),
            "jobs_touched": _int(run.jobs_touched),
            "skills_created": _int(run.skills_created),
        }
    finally:
        db.close()

    durations = _plan_durations([float(s["duration_ms"]) or 1.0 for s in stages])

    def gen() -> Iterator[str]:
        yield _sse({"type": "start", "run_date": actual_date,
                    "total_stages": len(stages), "source_label": source_label})
        for index, (stage, duration) in enumerate(zip(stages, durations)):
            yield _sse({"type": "stage", "index": index, "key": stage["key"],
                        "label": stage["label"], "phase": "begin", "in": stage["in"]})
            # 阶段越长给的 tick 越多（6~10），节奏均匀不至于有卡顿感
            span = REPLAY_STAGE_MAX - REPLAY_STAGE_MIN
            ratio = (duration - REPLAY_STAGE_MIN) / span if span > 0 else 0.0
            ticks = REPLAY_TICKS_MIN + int(round(ratio * (REPLAY_TICKS_MAX - REPLAY_TICKS_MIN)))
            ticks = min(REPLAY_TICKS_MAX, max(REPLAY_TICKS_MIN, ticks))
            slice_s = duration / (ticks + 1)
            samples = stage["samples"]
            for t in range(1, ticks + 1):
                time.sleep(slice_s)
                progress = round(t / (ticks + 1), 3)
                frame = {"type": "tick", "index": index, "progress": progress,
                         "processed": int(round(stage["in"] * progress))}
                if samples:
                    frame["sample"] = _sample_text(samples[(t - 1) % len(samples)])
                yield _sse(frame)
            time.sleep(slice_s)
            yield _sse({"type": "stage", "index": index, "key": stage["key"],
                        "label": stage["label"], "phase": "end", "in": stage["in"],
                        "out": stage["out"], "dropped": stage["dropped"],
                        "reasons": stage["reasons"], "detail": stage["detail"]})
        yield _sse(summary)
        yield _sse({"type": "done", "run_date": actual_date})
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                                      "Connection": "keep-alive"})
