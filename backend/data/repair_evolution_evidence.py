"""一次性事故修复：给「演化幽灵能力行」补回真实证据，补不上的诚实降级为候选。

**这是事故修复脚本，不是可反复运行的运维工具。** 修的是下面这一次事故；跑完一次、
核对过 `data/check_state.py` 的数字之后就不该再跑（再跑不会炸，但它每次都会重新
判一遍全库 active 行的归属，属于没有必要的写库风险）。

--------------------------------------------------------------------------
事故：`apply_evolution` 在「写证据」修复之前留下的 345 行幽灵 active 能力
--------------------------------------------------------------------------
生产库 `talent_graph_v3` 的 `job_skill` 共 5647 行，其中 candidate(3258) 与
deprecated(1231) 100% 干净，问题全在 active 的 1158 行里：

* **345 行幽灵**：`source_count=0`、`factors` 全零、`confidence=0`，且 `evidence`
  表里**一条关联记录都没有**。集中在 15 个 `version>1` 的岗位上（大数据开发 42、
  自然语言处理 37、云计算 37、机器学习 31、计算机视觉 29、后端开发 27、深度学习 26、
  推荐算法 24、数据仓库 21、大数据平台 16、物联网开发 14、Java 13、SRE 13、
  数据分析师 12、嵌入式 3）。
* **218 行计数陈旧**：`source_count` 是 0 或 1，但 `evidence` 表里实际有 ≥2 条
  关联证据——只是计数字段没跟着重算。
* 其余 586 行正常（独立来源 ≥2 且证据 ≥2）。

根因已定位：`capability_change` 里 367 条 `add` 记录中 344 条（93.7%）精确命中这些
幽灵行，反过来 345 个幽灵行有 99.7% 能被 add 记录解释。也就是说，它们是历史上
`apply_evolution` 在 `graph_service.write_evidence` 那行修复**之前**写进去的遗留脏
数据：新增分支把 `status="active"` 硬编码了，而证据一条没写。

后果不只是难看：`job.confidence` 是能力项置信度的加权均值，被这些 0 分行拖死——
后端开发工程师 28 个 active 里 27 个是幽灵，岗位置信度 0.0334；全库均值 0.3261
就是这么来的。

代码侧的根因已在 `app/services/evolution.py::admission_status` 堵掉（落库状态改由
交叉验证闸门判定，调用方声称 active 不算数）。**本脚本只负责清理存量。**

--------------------------------------------------------------------------
修复口径：复用系统现有判据，不发明新逻辑（发明匹配逻辑 = 伪造证据）
--------------------------------------------------------------------------
1. 聚类：`ingest.title_key()`（`cluster_hint` 优先）+ `ingest.canonical_job_names()`，
   与 `run_pipeline.py --from-db` 的 `build_graph_from_rows` 完全同一套分组；岗位
   按 `graph_service.slugify(簇名)` 对回 `Job.slug`，与 `upsert_job` 的身份口径一致。
2. 解析：只读 `data/parsed_cache_real.json`（键是 `cleaning.exact_hash(raw_text)`）。
   **缓存缺失的 JD 直接跳过，绝不调用 LLM**，不引入新的随机性、不花钱。
   `is_duplicate` / `lag_days` 直接取库中已算好的值——重算需要全库 SimHash 池，
   单岗位子集算出来的重复判定会与主链路不一致（同 `repair_click_evidence.py`）。
3. 交叉验证：`hallucination.aggregate_capabilities()`，**用它默认的 min_employers
   门槛**（=2），不放宽、不改阈值。
4. 幽灵行处理：
   * 技能名在聚合结果里且判为 `active` → `graph_service.write_evidence` 写入它自带
     的证据（按 `raw_jd_id` 去重），`confidence`/`factors`/`source_count` 按聚合结果
     回填，保持 active；
   * 否则 → `status` 改为 `candidate`，理由记进日志。**不删行**——删除会让证据链
     断掉，留成候选项可查、可解释（与演化判据④「保留观察，未淘汰」一致）。
5. 计数陈旧行：按 `evidence` → `raw_jd.employer_id` 的**独立雇主数**重算
   `source_count`，并用 `confidence.factors_from_jd` + `confidence.compute` 重算
   factors/confidence。判据函数直接复用 `confidence_batch` 的
   `_is_valid_raw_jd/_dedup_key/_employer_key/_freshness/_source_authority`——
   与每日置信度批算同一套口径，**不新增第三套公式**。
   **只单向上调**：仅当「证据里的独立雇主数 > 库里存的 source_count」才修。
   原因是 `graph_service.MAX_EVIDENCE_PER_SKILL = 12` 会截断证据，一个被 30 家雇主
   支持的技能只留得下 12 条证据，反向重算会把合法计数改小、砸掉健康行。
6. 岗位级：`job.confidence = hallucination.job_confidence(修复后的库中能力集)`、
   `job.evidence_count = active 行 source_count 之和`——与 `upsert_job` /
   `apply_evolution` 现有口径逐字一致。

--------------------------------------------------------------------------
安全约束
--------------------------------------------------------------------------
* **默认 dry-run，只有 `--apply` 才写库。**
* **纯增量**：只 UPDATE `job_skill` 的 status/confidence/factors/source_count、
  INSERT `evidence`、UPDATE `job` 的 confidence/evidence_count。
  绝不 DELETE、绝不清空 JobSkill/Evidence、绝不碰
  `capability_change` / `job_version` / `job_version_skill` / `evolution_run`
  （621 条变更记录是演化演示的资产，丢了就毁掉一个评分项）。
  提交前会逐表核对这些不变量，不满足就整体回滚。
* `--apply` 前先把每一条将改行的原值导出到
  `data/backup/repair_evolution_evidence_<时间戳>.json`，可人工回灌。

用法（在 backend/ 下）：

    $env:DB_NAME='talent_graph_v3'
    uv run python -X utf8 data/repair_evolution_evidence.py                      # dry-run 全库
    uv run python -X utf8 data/repair_evolution_evidence.py --jobs "Java开发工程师"  # 小范围试跑
    uv run python -X utf8 data/repair_evolution_evidence.py --apply              # 真正写库

跑完建议再跑一次 `uv run python data/check_state.py` 核对，并考虑跑
`uv run python data/run_confidence_batch.py` 让全库置信度落到同一个证据口径上。
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import models  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.services import cleaning, confidence as conf, graph_service, hallucination, ingest  # noqa: E402
from app.services import confidence_batch as cb  # noqa: E402
from app.services.hallucination import job_confidence  # noqa: E402

DEFAULT_CACHE = Path(__file__).resolve().parent / "parsed_cache_real.json"
BACKUP_DIR = Path(__file__).resolve().parent / "backup"

# 提交前必须一行不差的表：本脚本对它们只读。
FROZEN_TABLES = {
    "capability_change": models.CapabilityChange,
    "job_version": models.JobVersion,
    "job_version_skill": models.JobVersionSkill,
    "evolution_run": models.EvolutionRun,
    "job_skill": models.JobSkill,          # 只 UPDATE，绝不增删行
    "raw_jd": models.RawJD,
    "skill": models.Skill,
    "job": models.Job,
}


# --------------------------------------------------------------------------
# 语料侧：与 build_graph_from_rows 同一套聚类 + 聚合
# --------------------------------------------------------------------------
def cluster_raw_jds(db) -> dict[str, list[models.RawJD]]:
    """按 `--from-db` 主链路的分组逻辑把 RawJD 归到规范岗位簇。

    与 `ingest.build_graph_from_rows` 一致：`cluster_hint` 优先的 `title_key()`，
    落不到 `canonical_job_names()` 的键属于「待映射」，不参与建图也不参与本次修复。
    """
    canonical = ingest.canonical_job_names()
    clusters: dict[str, list[models.RawJD]] = defaultdict(list)
    unmapped = 0
    for row in db.query(models.RawJD).all():
        key = ingest.title_key(
            row.job_title or "", getattr(row, "cluster_hint", None),
            getattr(row, "track", None), getattr(row, "industry", None))
        if key not in canonical:
            unmapped += 1
            continue
        clusters[key].append(row)
    print(f"[语料] RawJD 聚成 {len(clusters)} 个规范岗位簇，待映射（不参与修复）{unmapped} 条")
    return clusters


def aggregate_cluster(db, rows: list[models.RawJD], cache: dict) -> tuple[dict, dict, dict]:
    """对一个岗位簇跑一次交叉验证聚合，返回 (粗粒度索引, 细粒度索引, 命中统计)。

    输入构造与 `ingest._aggregate_clusters` 逐字段对齐（通胀检测那一步刻意略去：
    它只把 `inflation_flag` 写回 RawJD，不进入聚合输入，而本脚本不写 raw_jd）。
    """
    employer_ids = {r.employer_id for r in rows if getattr(r, "employer_id", None)}
    parents = dict(db.query(models.Employer.id, models.Employer.parent_id).filter(
        models.Employer.id.in_(employer_ids)).all()) if employer_ids else {}

    agg_input, source_meta, hit, miss = [], {}, 0, 0
    for row in rows:
        parsed = cache.get(cleaning.exact_hash(row.raw_text or ""))
        if not parsed:
            miss += 1
            continue
        hit += 1
        agg_input.append({
            "required_skills": parsed.get("required_skills", []),
            "bonus_skills": parsed.get("bonus_skills", []),
            "fine_skills": parsed.get("fine_skills", []),
            "lag_days": row.lag_days, "is_duplicate": row.is_duplicate,
            "raw_jd_id": row.id, "source": row.source, "company": row.company,
        })
        source_meta[row.id] = {
            "platform": getattr(row, "platform", None) or row.source,
            "authority": getattr(row, "source_authority", None) or 0.6,
            "employer_id": getattr(row, "employer_id", None),
            "employer_parent_id": parents.get(getattr(row, "employer_id", None)),
            "company": row.company,
        }

    if not agg_input:
        return {}, {}, {"jd": len(rows), "cache_hit": hit, "cache_miss": miss}

    agg = hallucination.aggregate_capabilities(agg_input, source_meta=source_meta)
    coarse, fine = {}, {}
    for cap in agg["capabilities"]:
        target = fine if cap.get("granularity") == "fine" else coarse
        prev = target.get(cap["name"])
        # 同名重复时保留 active / 高置信的那一条
        if prev is None or (cap["status"] == "active" and prev["status"] != "active") or (
                cap["status"] == prev["status"] and cap["confidence"] > prev["confidence"]):
            target[cap["name"]] = cap
    return coarse, fine, {"jd": len(rows), "cache_hit": hit, "cache_miss": miss,
                          "active_caps": sum(1 for c in agg["capabilities"]
                                             if c["status"] == "active")}


# --------------------------------------------------------------------------
# 证据侧：与 confidence_batch 同一套判据重算 source_count / factors / confidence
# --------------------------------------------------------------------------
def recompute_from_evidence(supporting: dict, external_keys: set[str],
                            total_valid_jds: int, employers: dict,
                            as_of: datetime) -> tuple[int, dict, float]:
    """给一条能力关系按证据重算 (独立雇主数, factors, confidence)。

    判据函数全部取自 `services.confidence_batch`（每日置信度批算用的同一套），
    公式取自 `services.confidence`。这里不定义任何新的打分逻辑。
    """
    employer_keys = {cb._employer_key(raw, employers) for raw in supporting.values()}
    employer_keys.discard(None)
    factors = conf.factors_from_jd(
        support_ratio=(len(supporting) / total_valid_jds) if total_valid_jds else 0.0,
        platforms={str(k) for k in employer_keys},
        avg_freshness=cb._mean([cb._freshness(raw, as_of) for raw in supporting.values()]),
        avg_authority=cb._mean([cb._source_authority(raw) for raw in supporting.values()]),
        has_web=bool(external_keys))
    return len(employer_keys), factors, conf.compute(factors)


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------
def plan_job(db, job: models.Job, coarse: dict, fine: dict, as_of: datetime) -> dict:
    """算出一个岗位的完整修复计划（只读，不写库）。"""
    rows = (db.query(models.JobSkill, models.Skill)
            .join(models.Skill, models.Skill.id == models.JobSkill.skill_id)
            .filter(models.JobSkill.job_id == job.id).all())
    relation_ids = [js.id for js, _ in rows]
    evidence_rows = db.query(models.Evidence).filter(
        models.Evidence.job_skill_id.in_(relation_ids)).all() if relation_ids else []

    by_relation: dict[int, list[models.Evidence]] = defaultdict(list)
    for ev in evidence_rows:
        by_relation[ev.job_skill_id].append(ev)

    # --- 1) 幽灵行：active 且一条关联证据都没有 ---
    backfilled, demoted, planned_ev = [], [], {}
    for js, skill in rows:
        if js.status != "active" or by_relation.get(js.id):
            continue
        cap = (fine if skill.parent_id else coarse).get(skill.name) \
            or (coarse if skill.parent_id else fine).get(skill.name)
        if cap is None:
            demoted.append((js, skill, "真实语料聚合结果中查无此能力项"))
            continue
        if cap["status"] != "active":
            demoted.append((js, skill,
                            f"语料支持不足未过交叉验证闸门"
                            f"（独立雇主 {cap['source_count']}，置信度 {cap['confidence']:.4f}）"))
            continue
        evidence = [e for e in (cap.get("evidence") or []) if e.get("raw_jd_id")]
        if not evidence:
            demoted.append((js, skill, "聚合结果没有可落库的 raw_jd 证据"))
            continue
        planned_ev[js.id] = evidence[:graph_service.MAX_EVIDENCE_PER_SKILL]
        backfilled.append((js, skill, cap, len(planned_ev[js.id])))

    # --- 2) 修复后的 active 集合（幽灵降级会改变它） ---
    demoted_ids = {js.id for js, _, _ in demoted}
    active_after = [(js, sk) for js, sk in rows
                    if js.status == "active" and js.id not in demoted_ids]

    # --- 3) 证据池（现有 + 计划新增），用于计数陈旧行的重算 ---
    raw_ids = {ev.raw_jd_id for ev in evidence_rows if ev.raw_jd_id}
    raw_ids |= {e["raw_jd_id"] for evs in planned_ev.values() for e in evs}
    raw_jds = {r.id: r for r in db.query(models.RawJD).filter(
        models.RawJD.id.in_(raw_ids)).all()} if raw_ids else {}
    employer_ids = {r.employer_id for r in raw_jds.values() if r.employer_id}
    employers = {e.id: e for e in db.query(models.Employer).filter(
        models.Employer.id.in_(employer_ids)).all()} if employer_ids else {}
    parent_ids = {e.parent_id for e in employers.values() if e.parent_id}
    if parent_ids:
        employers.update({e.id: e for e in db.query(models.Employer).filter(
            models.Employer.id.in_(parent_ids)).all()})

    def support_of(js_id: int) -> tuple[dict, set[str]]:
        supporting, external = {}, set()
        seen_raw: set[int] = set()
        for ev in by_relation.get(js_id, []):
            raw = raw_jds.get(ev.raw_jd_id)
            if ev.source_type == "jd" and cb._is_valid_raw_jd(raw, as_of):
                supporting.setdefault(cb._dedup_key(raw), raw)
                seen_raw.add(raw.id)
            elif (ev.source_type or "").casefold() in cb.REAL_EXTERNAL_TYPES:
                key = (ev.source_url or ev.snippet or "").strip()
                if key:
                    external.add(key)
        for e in planned_ev.get(js_id, []):
            raw = raw_jds.get(e["raw_jd_id"])
            if raw is not None and raw.id not in seen_raw and cb._is_valid_raw_jd(raw, as_of):
                supporting.setdefault(cb._dedup_key(raw), raw)
        return supporting, external

    valid_pool: dict = {}
    for js, _ in active_after:
        supporting, _ext = support_of(js.id)
        for key, raw in supporting.items():
            valid_pool.setdefault(key, raw)
    total_valid_jds = len(valid_pool)

    # --- 4) 计数陈旧行：有证据、且证据里的独立雇主数 > 库里存的 source_count ---
    restated = []
    for js, skill in active_after:
        if js.id in planned_ev or not by_relation.get(js.id):
            continue        # 幽灵行走聚合口径；无证据的行不在本分支
        supporting, external = support_of(js.id)
        employer_count, factors, confidence = recompute_from_evidence(
            supporting, external, total_valid_jds, employers, as_of)
        if employer_count <= int(js.source_count or 0):
            # 只单向上调：证据表被 MAX_EVIDENCE_PER_SKILL 截断过，反向重算会砸掉健康行
            continue
        restated.append((js, skill, employer_count, factors, confidence))

    # --- 5) 岗位级：与 upsert_job / apply_evolution 完全同一口径 ---
    new_values: dict[int, tuple[float, int]] = {}
    for js, _sk, cap, _n in backfilled:
        new_values[js.id] = (float(cap["confidence"]), int(cap["source_count"]))
    for js, _sk, employer_count, _f, confidence in restated:
        new_values[js.id] = (float(confidence), int(employer_count))

    caps_after = [{
        "name": sk.name, "status": "active",
        "weight": float(js.weight or 0.5),
        "confidence": new_values.get(js.id, (float(js.confidence or 0.0), 0))[0],
        "granularity": "fine" if sk.parent_id else "coarse",
    } for js, sk in active_after]
    evidence_count_after = sum(
        new_values.get(js.id, (0.0, int(js.source_count or 0)))[1] for js, _ in active_after)

    return {
        "job": job,
        "phantom_total": len(backfilled) + len(demoted),
        "backfilled": backfilled, "demoted": demoted, "restated": restated,
        "planned_evidence": planned_ev,
        "active_before": sum(1 for js, _ in rows if js.status == "active"),
        "active_after": len(active_after),
        "confidence_before": round(float(job.confidence or 0.0), 4),
        "confidence_after": job_confidence(caps_after),
        "evidence_count_before": int(job.evidence_count or 0),
        "evidence_count_after": evidence_count_after,
    }


def print_plan(plan: dict, corpus: dict) -> None:
    job = plan["job"]
    print(f"\n【{job.name}】 job_id={job.id} v{job.version}  "
          f"active {plan['active_before']}→{plan['active_after']}  "
          f"幽灵 {plan['phantom_total']}（补证据 {len(plan['backfilled'])} / "
          f"降级 {len(plan['demoted'])}）  计数陈旧 {len(plan['restated'])}")
    if corpus:
        print(f"    语料：JD {corpus.get('jd', 0)} 条，解析缓存命中 {corpus.get('cache_hit', 0)} / "
              f"缺失 {corpus.get('cache_miss', 0)}，聚合出 active 能力 {corpus.get('active_caps', 0)}")
    for js, sk, cap, n_ev in plan["backfilled"]:
        print(f"    [补证据] {sk.name:28s} 独立来源 {js.source_count or 0}→{cap['source_count']}  "
              f"置信度 {float(js.confidence or 0):.4f}→{cap['confidence']:.4f}  证据 +{n_ev}")
    for js, sk, reason in plan["demoted"]:
        print(f"    [降 候选] {sk.name:28s} active→candidate  理由：{reason}")
    for js, sk, employer_count, _factors, confidence in plan["restated"]:
        print(f"    [重算  ] {sk.name:28s} 独立来源 {js.source_count or 0}→{employer_count}  "
              f"置信度 {float(js.confidence or 0):.4f}→{confidence:.4f}")
    print(f"    [岗位  ] confidence {plan['confidence_before']:.4f}→{plan['confidence_after']:.4f}  "
          f"evidence_count {plan['evidence_count_before']}→{plan['evidence_count_after']}")


def backup(plans: list[dict]) -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    path = BACKUP_DIR / f"repair_evolution_evidence_{datetime.now():%Y%m%d_%H%M%S}.json"
    payload = []
    for plan in plans:
        job = plan["job"]
        rows = []
        for js, sk, cap, _n in plan["backfilled"]:
            rows.append({"action": "backfill_evidence", "job_skill_id": js.id,
                         "skill": sk.name, "before": _row_state(js)})
        for js, sk, reason in plan["demoted"]:
            rows.append({"action": "demote_to_candidate", "job_skill_id": js.id,
                         "skill": sk.name, "reason": reason, "before": _row_state(js)})
        for js, sk, *_rest in plan["restated"]:
            rows.append({"action": "restate_source_count", "job_skill_id": js.id,
                         "skill": sk.name, "before": _row_state(js)})
        payload.append({
            "job_id": job.id, "job": job.name,
            "job_before": {"confidence": float(job.confidence or 0.0),
                           "evidence_count": int(job.evidence_count or 0)},
            "rows": rows,
        })
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _row_state(js: models.JobSkill) -> dict:
    return {"status": js.status, "confidence": float(js.confidence or 0.0),
            "source_count": int(js.source_count or 0), "factors": js.factors or {}}


def apply_plan(db, plan: dict) -> int:
    """把一个岗位的计划写进会话（不 commit），返回新增证据行数。"""
    now = datetime.utcnow()
    written = 0
    for js, _sk, cap, _n in plan["backfilled"]:
        written += graph_service.write_evidence(db, js.id, plan["planned_evidence"][js.id])
        js.confidence = float(cap["confidence"])
        js.factors = cap.get("factors") or {}
        js.source_count = int(cap["source_count"])
        js.status = "active"
        js.last_seen = now
    for js, _sk, _reason in plan["demoted"]:
        js.status = "candidate"          # 保留行、保留证据链，绝不删除
        js.last_seen = now
    for js, _sk, employer_count, factors, confidence in plan["restated"]:
        js.source_count = int(employer_count)
        js.factors = factors
        js.confidence = float(confidence)
        js.last_seen = now
    job = plan["job"]
    job.confidence = plan["confidence_after"]
    job.evidence_count = plan["evidence_count_after"]
    job.updated_at = now
    return written


def table_counts(db) -> dict[str, int]:
    return {name: db.query(model).count() for name, model in FROZEN_TABLES.items()}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true", help="真正写库（缺省仅 dry-run）")
    ap.add_argument("--jobs", default="", help='只处理这些岗位，逗号分隔，如 "Java开发工程师,数据分析师"')
    ap.add_argument("--cache", default=str(DEFAULT_CACHE),
                    help="解析缓存路径（默认 data/parsed_cache_real.json；影子库演练时可换）")
    args = ap.parse_args()

    only = {n.strip() for n in args.jobs.split(",") if n.strip()} or None
    cache_path = Path(args.cache)
    cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
    print(f"=== 目标库: {settings.db_name}"
          f"{' (override)' if settings.database_url_override else ''} | "
          f"模式: {'APPLY 写库' if args.apply else 'DRY-RUN 只读'} ===")
    print(f"[缓存] {cache_path.name}: {len(cache)} 条解析结果"
          f"{'  ← 文件不存在，语料侧将全部落空' if not cache else ''}")
    if not cache:
        print("[警告] 没有解析缓存 → 所有幽灵行都会走「降级为候选」分支。"
              "本脚本绝不调用 LLM，请确认缓存路径是否正确。")

    db = SessionLocal()
    try:
        before_counts = table_counts(db)
        before_evidence = db.query(models.Evidence).count()

        clusters = cluster_raw_jds(db)
        # 主链路的岗位身份是 slugify(簇名)（`upsert_job` 就是这么找行的），所以 slug
        # 优先；岗位名兜底是给手工建的岗位用的（如 seed_new_jobs.py / 影子库演练，
        # 它们的 slug 不由 slugify 生成），簇名本身就是规范岗位名，口径不冲突。
        slug_to_cluster = {graph_service.slugify(k): k for k in clusters}
        name_to_cluster = {k: k for k in clusters}

        # 需要处理的岗位：有幽灵行或有计数陈旧行的 published 岗位
        jobs = db.query(models.Job).order_by(models.Job.id).all()
        if only:
            unknown = only - {j.name for j in jobs}
            if unknown:
                print(f"[警告] --jobs 里这些岗位库中不存在：{sorted(unknown)}")
            jobs = [j for j in jobs if j.name in only]

        as_of = cb._naive_utc(datetime.now(timezone.utc))
        plans, corpus_stats = [], {}
        agg_cache: dict[str, tuple[dict, dict, dict]] = {}
        for job in jobs:
            cluster_key = slug_to_cluster.get(job.slug) or name_to_cluster.get(job.name)
            if cluster_key is None:
                coarse, fine, stats = {}, {}, {}
            else:
                if cluster_key not in agg_cache:
                    agg_cache[cluster_key] = aggregate_cluster(db, clusters[cluster_key], cache)
                coarse, fine, stats = agg_cache[cluster_key]
            plan = plan_job(db, job, coarse, fine, as_of)
            if not (plan["backfilled"] or plan["demoted"] or plan["restated"]):
                continue
            plans.append(plan)
            corpus_stats[job.id] = stats

        if not plans:
            print("\n没有需要修复的行。")
            return 0

        for plan in plans:
            print_plan(plan, corpus_stats.get(plan["job"].id, {}))

        n_backfill = sum(len(p["backfilled"]) for p in plans)
        n_demote = sum(len(p["demoted"]) for p in plans)
        n_restate = sum(len(p["restated"]) for p in plans)
        n_evidence = sum(len(v) for p in plans for v in p["planned_evidence"].values())

        print("\n" + "=" * 78)
        print("== 汇总 ==")
        print(f"  涉及岗位            {len(plans)}")
        print(f"  幽灵行 · 补证据成功  {n_backfill}  （预计新增 evidence 行 ≤{n_evidence}）")
        print(f"  幽灵行 · 降级为候选  {n_demote}")
        print(f"  计数陈旧 · 重算      {n_restate}")
        print(f"  active 行总变化      "
              f"{sum(p['active_before'] for p in plans)}→{sum(p['active_after'] for p in plans)}")
        print("\n== 各岗位 confidence 前后对比 ==")
        print(f"  {'岗位':<26s}{'before':>9s}{'after':>9s}{'delta':>9s}   evidence_count")
        for plan in sorted(plans, key=lambda p: p["confidence_after"] - p["confidence_before"],
                           reverse=True):
            before, after = plan["confidence_before"], plan["confidence_after"]
            print(f"  {plan['job'].name:<26s}{before:>9.4f}{after:>9.4f}{after - before:>+9.4f}"
                  f"   {plan['evidence_count_before']}→{plan['evidence_count_after']}")
        avg_before = sum(p["confidence_before"] for p in plans) / len(plans)
        avg_after = sum(p["confidence_after"] for p in plans) / len(plans)
        print(f"  {'（涉及岗位均值）':<24s}{avg_before:>9.4f}{avg_after:>9.4f}{avg_after - avg_before:>+9.4f}")

        if not args.apply:
            print("\nDRY-RUN：一行都没有写库。确认无误后加 --apply。")
            return 0

        path = backup(plans)
        print(f"\n[备份] 将改行的原值已导出：{path}")
        written = sum(apply_plan(db, plan) for plan in plans)
        db.flush()

        after_counts = table_counts(db)
        drift = {k: (before_counts[k], after_counts[k])
                 for k in before_counts if before_counts[k] != after_counts[k]}
        after_evidence = db.query(models.Evidence).count()
        if drift or after_evidence < before_evidence:
            db.rollback()
            print(f"[中止] 不变量被破坏，已整体回滚：{drift or '证据行数不增反减'}")
            return 2
        db.commit()
        print(f"[完成] 新增证据 {written} 行（evidence {before_evidence}→{after_evidence}），"
              f"其余表行数一行未动。")
        print("       建议接着跑：uv run python data/check_state.py")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
