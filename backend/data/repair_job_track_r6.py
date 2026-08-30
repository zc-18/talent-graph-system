# -*- coding: utf-8 -*-
"""给 32 个岗位回填 `job.track` / `job.industry`（R6 缺陷②）。

**症状**：`job` 表 32 行的 `track` 与 `industry` 全部为 NULL，`source_summary` 里也没有
（实测 `JSON_EXTRACT(source_summary,'$.track') IS NOT NULL` 命中 0 行）。于是
`role_contract.build_contract_from_job` 的

    track = job.track or source.get("track", "software")

一律回落 `"software"`，**所有岗位都按软件岗判技能冲突**。后果实测：智能硬件开发工程师
那条 `示波器`（parent=嵌入式开发、2 家雇主、required、已 active）命中
`job_resolution._TRACK_CONFLICTS["software"]`，被静默从岗位契约里删掉——
图谱里事实是「已交叉验证的硬件能力」，契约里却看不到它，属于「事实与呈现不一致」，
与 CLAUDE.md 记的日志/事实分歧同源。

**修法**：取值来自现成的 `job_resolution.resolve_job_query(job.name)`，不新写推断逻辑。
只 UPDATE `job` 表两列，不碰任何能力关系、证据、演化记录，因此也不改置信度
（置信度公式里没有 track 这个因子）。

**两处人工修正**（函数输出有明确缺陷，逐个核对时发现，写死在 OVERRIDES 里）：

* `AI产品经理` → 函数给 `algorithm`。`resolve_job_query` 的 `_first` 按
  hardware→algorithm→data→ops→product 顺序取首个命中，而 algorithm 的词表里有裸词
  `"ai"`，"AI产品经理" 先命中 algorithm，product 永远轮不到。它就是产品岗，改 `product`。
* `大数据平台工程师` → 函数给 industry `internet`，因为 industry 词表里 internet 含
  `"平台"`，命中的是「数据平台」的平台。它不是互联网行业岗，改 `general`。

其余 30 个岗位原样采用函数输出，未做任何人工微调。

**已知副作用（不是 bug，是这次修复的目的）**：`build_contract_from_job` 选分级画像切片时
按 `JobLevelSkill.track.in_({track,"unspecified"})` 过滤，并且 `slice_rank` 把
track/industry 精确匹配排在切片大小之前。`job_level_skill` 的行本来就是
`leveling.bucket_slices` 按**每条 JD 自己的 track/industry** 分桶写进去的
（algorithm 443 行、software 237、data 113、ops 61、hardware 15…），而读侧因为
job.track 为 NULL 一直按 software 找——写侧和读侧口径不一致，很多切片根本取不到。
回填后两侧对齐，43 个 (岗位,级别) 组合会改选切片，明细见 dry-run 输出。

用法（backend/ 下）。**dry-run 才可以对着生产库跑（零写入）；`--apply` 对
`talent_graph_v3` 会被 `repair_safety.assert_shadow_apply_target` 无条件拒绝**——
先用 `data/clone_database_r6.py` 克隆出影子库，在影子库上 apply、验收，再切 DB_NAME：

    $env:DB_NAME='talent_graph_v3'
    uv run python -X utf8 data/repair_job_track_r6.py            # dry-run，可对生产库

    $env:DB_NAME='talent_graph_v4_shadow'
    uv run python -X utf8 data/repair_job_track_r6.py --apply         --allow-shadow --confirm-database talent_graph_v4_shadow
"""
from __future__ import annotations
import argparse
import sys
from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import SessionLocal  # noqa: E402
from app import models  # noqa: E402
from app.services import repair_safety, role_contract  # noqa: E402
from app.services.job_resolution import (  # noqa: E402
    INDUSTRIES, TRACKS, resolve_job_query, role_skill_conflict)
from app.services.taxonomy import normalize_skill  # noqa: E402

BACKUP_DIR = Path(__file__).resolve().parent / "backup"

# R6 人工复核后的完整岗位集。冻结全表而不是只冻结 resolver 例外，防止后续别名/词表
# 调整让一次历史修复脚本在重放时静默改判。两项与 job_profile_golden.json 对齐：
# 提示词工程师属于算法应用，生成式人工智能系统测试员属于软件测试。
EXPECTED_JOB_DIMENSIONS: dict[str, tuple[str, str]] = {
    "后端开发工程师": ("software", "general"),
    "Java开发工程师": ("software", "general"),
    "数据分析师": ("data", "general"),
    "机器学习工程师": ("software", "general"),
    "运维开发工程师(SRE)": ("ops", "general"),
    "自然语言处理工程师": ("software", "general"),
    "大数据平台工程师": ("data", "general"),
    "数据仓库工程师": ("data", "general"),
    "计算机视觉工程师": ("software", "general"),
    "深度学习工程师": ("software", "general"),
    "云计算工程师": ("ops", "general"),
    "推荐算法工程师": ("algorithm", "general"),
    "嵌入式软件工程师": ("hardware", "general"),
    "物联网开发工程师": ("software", "general"),
    "大数据开发工程师": ("data", "general"),
    "机器人算法工程师": ("algorithm", "general"),
    "大模型算法工程师": ("algorithm", "general"),
    "自动驾驶算法工程师": ("algorithm", "general"),
    "大模型推理优化工程师": ("algorithm", "general"),
    "AIGC算法工程师": ("algorithm", "general"),
    "多模态算法工程师": ("algorithm", "general"),
    "人工智能数字人训练师": ("algorithm", "general"),
    "边缘计算工程师": ("software", "general"),
    "AI智能体开发工程师": ("algorithm", "general"),
    "具身智能工程师": ("software", "general"),
    "生成式人工智能系统测试员": ("software", "general"),
    "AI产品经理": ("product", "general"),
    "数字孪生工程技术人员": ("software", "general"),
    "提示词工程师": ("algorithm", "general"),
    "车联网系统工程师": ("software", "automotive"),
    "工业互联网工程师": ("software", "manufacturing"),
    "智能硬件开发工程师": ("hardware", "general"),
}
assert len(EXPECTED_JOB_DIMENSIONS) == 32


def planned(job) -> tuple[str, str, str]:
    """返回冻结的 (track, industry, 来源说明)。未知岗位必须先经人工复核。"""
    target = EXPECTED_JOB_DIMENSIONS.get(job.name)
    if target is None:
        raise ValueError(f"岗位未进入 R6 人工判定表：{job.name}")
    resolved = resolve_job_query(job.name)
    origin = ("人工复核" if target != (resolved.track, resolved.industry)
              else "冻结的 resolve_job_query 复核结果")
    return target[0], target[1], origin


def _active_capabilities(db, job) -> list[dict]:
    """岗位列表卡片走的能力集：active 的 job_skill 直读（与 contract_summaries_for_jobs 同源）。

    track_conflict 是在这条路径上咬人的——`build_contract_from_job` 在 level 落在
    junior/middle/senior 时改读 `job_level_skill`，看不到这条缺陷。
    """
    rows = (db.query(models.JobSkill, models.Skill)
            .join(models.Skill, models.Skill.id == models.JobSkill.skill_id)
            .filter(models.JobSkill.job_id == job.id,
                    models.JobSkill.status == "active").all())
    parent_ids = {skill.parent_id for _, skill in rows if skill.parent_id}
    parents = dict(db.query(models.Skill.id, models.Skill.name).filter(
        models.Skill.id.in_(parent_ids)).all()) if parent_ids else {}
    caps = []
    for relation, skill in rows:
        factors = relation.factors or {}
        caps.append({
            "name": skill.name, "parent_name": parents.get(skill.parent_id),
            "category": skill.category, "skill_type": skill.skill_type,
            "importance": relation.importance, "weight": relation.weight,
            "level_required": relation.level_required, "confidence": relation.confidence,
            "support_ratio": float(factors.get("support", 0.0) or 0.0),
            "source_count": relation.source_count, "employer_count": relation.source_count,
            "status": relation.status,
            "granularity": "fine" if skill.parent_id else "coarse",
        })
    return caps


def _contract_counts(db, job, track: str, industry: str,
                     caps: list[dict] | None = None) -> tuple[int, int, str, int]:
    """按给定 track/industry 复算契约摘要（纯投影，不写库）。"""
    contract = role_contract.build_role_contract(
        caps if caps is not None else _active_capabilities(db, job),
        job_id=job.id, job_name=job.name, seniority=job.level or "unspecified",
        recruitment_type=job.recruitment_type or "unspecified",
        track=track, industry=industry, version=job.version or 1)
    summary = contract["summary"]
    return (summary["cluster_count"], summary["required_count"], contract["status"],
            summary["rejected"].get("track_conflict", 0))


def _slice_changes(db, job, track: str, industry: str) -> list[str]:
    """回填后分级画像切片选择的变化（读侧口径与写侧对齐带来的）。"""
    recruitment = getattr(job, "recruitment_type", None) or "unspecified"
    out = []
    for level in ("junior", "middle", "senior"):
        before = _pick_slice(db, job, "software", "general", recruitment, level)
        after = _pick_slice(db, job, track, industry, recruitment, level)
        if before != after:
            out.append(f"{level}: {before[0]}({before[1]}项) → {after[0]}({after[1]}项)")
    return out


def _pick_slice(db, job, track, industry, recruitment, level):
    recruitment_options = ({recruitment, "unspecified"}
                           if recruitment in {"campus", "social"} else {"unspecified"})
    rows = db.query(models.JobLevelSkill).filter(
        models.JobLevelSkill.job_id == job.id,
        models.JobLevelSkill.level == level,
        models.JobLevelSkill.recruitment_type.in_(recruitment_options),
        models.JobLevelSkill.track.in_({track, "unspecified"}),
        models.JobLevelSkill.industry.in_({industry, "general"})).all()
    grouped: dict[tuple[str, str, str], list] = defaultdict(list)
    for row in rows:
        grouped[(row.recruitment_type, row.track, row.industry)].append(row)
    if not grouped:
        return ("无切片", 0)

    def rank(key):
        specificity = sum((key[0] != "unspecified", key[1] != "unspecified",
                           key[2] not in {"general", "unspecified"}))
        return (key[1] == track, key[0] == recruitment, key[2] == industry,
                specificity, len(grouped[key]))

    selected = max(grouped, key=rank)
    return ("/".join(selected), len(grouped[selected]))


def _verify(db) -> bool:
    """收尾自检：岗位全集、取值和版本投影均与冻结判定表一致。"""
    ok = True
    jobs = db.query(models.Job).order_by(models.Job.id).all()
    actual_names = {job.name for job in jobs}
    expected_names = set(EXPECTED_JOB_DIMENSIONS)
    if actual_names != expected_names:
        ok = False
        print(f"  [FAIL] 岗位集漂移：缺少={sorted(expected_names - actual_names)}；"
              f"新增={sorted(actual_names - expected_names)}")
    missing = [j.name for j in jobs if not j.track or not j.industry]
    if missing:
        ok = False
        print(f"  [FAIL] 仍有 {len(missing)} 个岗位 track/industry 为空：{missing[:5]}")
    bad = [f"{j.name}({j.track}/{j.industry})" for j in jobs
           if (j.track and j.track not in TRACKS) or (j.industry and j.industry not in INDUSTRIES)]
    if bad:
        ok = False
        print(f"  [FAIL] 取值不在受管词表内：{bad}")
    drift = [j.name for j in jobs if (j.track, j.industry) != planned(j)[:2]]
    if drift:
        ok = False
        print(f"  [FAIL] 与判定表不一致：{drift}")
    version_drift = []
    for version, job in (db.query(models.JobVersion, models.Job)
                         .join(models.Job, models.Job.id == models.JobVersion.job_id).all()):
        expected = EXPECTED_JOB_DIMENSIONS.get(job.name)
        window = version.evidence_window if isinstance(version.evidence_window, dict) else {}
        dimensions = window.get("dimensions") if isinstance(window.get("dimensions"), dict) else {}
        contract = version.contract_snapshot if isinstance(version.contract_snapshot, dict) else {}
        if (not expected or dimensions.get("track") != expected[0]
                or dimensions.get("industry") != expected[1]
                or contract.get("track") != expected[0]
                or contract.get("industry") != expected[1]):
            version_drift.append(f"{job.name}/v{version.version}")
    if version_drift:
        ok = False
        print(f"  [FAIL] 版本维度或契约投影未同步：{version_drift}")
    conflicts = _remaining_conflicts(db)
    print(f"  [OK] 32 岗及版本投影全部与冻结判定表一致" if ok else "  [FAIL] 见上")
    print(f"  回填后仍被 track_conflict 拒掉的 active 能力项：{len(conflicts)} 条"
          + ("".join(f"\n     {c}" for c in conflicts) if conflicts else ""))
    return ok


def _remaining_conflicts(db) -> list[str]:
    rows = (db.query(models.Job.name, models.Skill.name, models.JobSkill.source_count)
            .join(models.JobSkill, models.JobSkill.job_id == models.Job.id)
            .join(models.Skill, models.Skill.id == models.JobSkill.skill_id)
            .filter(models.JobSkill.status == "active").all())
    tracks = {j.name: (j.track or "software") for j in db.query(models.Job).all()}
    return [f"{jn} / {sn}（雇主 {sc}）" for jn, sn, sc in rows
            if role_skill_conflict(jn, tracks.get(jn, "software"), normalize_skill(sn or ""))]


def _sync_version_projections(db, jobs: list) -> int:
    """Correct stored metadata/projections without fabricating evolution records."""
    by_id = {job.id: job for job in jobs}
    changed = 0
    versions = db.query(models.JobVersion).filter(
        models.JobVersion.job_id.in_(set(by_id))).all() if by_id else []
    for version in versions:
        job = by_id[version.job_id]
        track, industry = EXPECTED_JOB_DIMENSIONS[job.name]
        window = dict(version.evidence_window or {})
        dimensions = dict(window.get("dimensions") or {})
        dimensions.update({
            "job_name": job.name,
            "seniority": dimensions.get("seniority") or job.level or "unspecified",
            "recruitment_type": dimensions.get("recruitment_type")
                                or job.recruitment_type or "unspecified",
            "track": track, "industry": industry,
        })
        window["dimensions"] = dimensions
        version.evidence_window = window
        version.contract_snapshot = role_contract.build_contract_from_version(db, job, version)
        # build_contract_from_version mutates evidence_window while recalculating counts.
        # Assign a deep copy so SQLAlchemy always sees the JSON value as dirty.
        version.evidence_window = deepcopy(version.evidence_window)
        changed += 1
    return changed


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="真正写库（缺省仅 dry-run）")
    parser.add_argument("--allow-shadow", action="store_true",
                        help="显式批准非 SQLite shadow（当前生产库始终禁止）")
    parser.add_argument("--confirm-database", default=None,
                        help="必须精确填写实际连接的非生产 shadow 库名")
    args = parser.parse_args(argv)

    db = SessionLocal()
    try:
        jobs = db.query(models.Job).order_by(models.Job.id).all()
        actual_names = {job.name for job in jobs}
        expected_names = set(EXPECTED_JOB_DIMENSIONS)
        if actual_names != expected_names:
            print(f"[中止] 岗位集与冻结的 32 岗不一致：缺少="
                  f"{sorted(expected_names - actual_names)}；新增="
                  f"{sorted(actual_names - expected_names)}")
            return 2
        print(f"=== 岗位 {len(jobs)} 个｜现状 track 非空 "
              f"{sum(1 for j in jobs if j.track)}，industry 非空 "
              f"{sum(1 for j in jobs if j.industry)} ===\n")
        print(f"{'id':>3} {'岗位':<24} {'现':<10} {'→ track':<11} {'industry':<14} "
              f"{'簇/必备 现→后':<18} {'冲突拒 现→后':<12} 取值来源")
        plan, before_conflicts, after_conflicts = [], 0, 0
        for job in jobs:
            track, industry, origin = planned(job)
            caps = _active_capabilities(db, job)
            cur_clusters, cur_required, cur_status, cur_conflict = _contract_counts(
                db, job, "software", "general", caps)
            new_clusters, new_required, new_status, new_conflict = _contract_counts(
                db, job, track, industry, caps)
            before_conflicts += cur_conflict
            after_conflicts += new_conflict
            same = (job.track == track and job.industry == industry)
            mark = "  (已是该值)" if same else ""
            flag = "  ← 契约变化" if (cur_clusters, cur_required, cur_status) != (
                new_clusters, new_required, new_status) else ""
            print(f"{job.id:>3} {job.name:<24} {str(job.track):<10} {track:<11} {industry:<14} "
                  f"{f'{cur_clusters}/{cur_required}{cur_status[:1]} → {new_clusters}/{new_required}{new_status[:1]}':<20} "
                  f"{f'{cur_conflict} → {new_conflict}':<12} {origin}{mark}{flag}")
            if not same:
                plan.append((job, track, industry))

        print(f"\n需要写库的岗位：{len(plan)} 个"
              f"（其余已是目标值，重复执行不会再改——幂等）")
        print(f"契约里被 track_conflict 拒掉的 active 能力项合计："
              f"{before_conflicts} → {after_conflicts}")

        print("\n=== 分级画像切片选择的变化（读写口径对齐的直接结果）===")
        total_slice = 0
        for job, track, industry in plan:
            changes = _slice_changes(db, job, track, industry)
            if changes:
                total_slice += len(changes)
                print(f"  {job.name}：" + "；".join(changes))
        print(f"  合计 {total_slice} 个 (岗位,级别) 组合改选切片")

        if not args.apply:
            db.rollback()
            print("\n[dry-run] 未写库。shadow 发布需 --apply；生产发布另需双重确认。")
            return 0

        repair_safety.assert_shadow_apply_target(
            db, allow_shadow=args.allow_shadow,
            confirm_database=args.confirm_database)
        versions = db.query(models.JobVersion).order_by(models.JobVersion.id).all()
        snapshot = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "database": db.get_bind().url.database,
            "jobs": [{"job_id": j.id, "name": j.name, "track": j.track,
                      "industry": j.industry} for j in jobs],
            "job_versions": [{
                "id": row.id, "job_id": row.job_id,
                "evidence_window": row.evidence_window,
                "contract_snapshot": row.contract_snapshot,
            } for row in versions],
        }
        path = repair_safety.backup_path(BACKUP_DIR, "job_track_r6")
        repair_safety.write_json_exclusive(path, snapshot)
        try:
            for job, track, industry in plan:
                job.track, job.industry = track, industry
            db.flush()
            version_count = _sync_version_projections(db, jobs)
            db.flush()
            print("\n=== commit 前 _verify() ===")
            if not _verify(db):
                raise RuntimeError("岗位维度修复验证失败")
            db.commit()
        except Exception:
            db.rollback()
            raise
        print(f"\n已写库：{len(plan)} 个岗位、同步 {version_count} 个版本投影；"
              f"改动前原值备份在 {path}")
        return 0
    except Exception as exc:
        db.rollback()
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
