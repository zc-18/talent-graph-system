"""修复 2026-07-28 线上误点造成的图谱损伤。

事故经过：v3 切库上线后，演示站被点了 `POST /api/discovery/discover` ×2 与
`POST /api/evolution/update` ×4（服务器日志 CST 21:27–21:41）。两条写路径当时都没有
防护，造成三处损伤：

1. **提示词工程师**：discovery 对已存在岗位调用 `graph_service.upsert_job`，而后者
   会先清空 JobSkill/Evidence 再按传入能力项重建。传进去的是 LLM 依据几条检索证据
   现生成的十来项能力，于是 301 项交叉验证结果连同证据被**物理删除**只剩 7 项，
   且 `is_new` 被翻成 True——新兴岗位数从交付口径的 6 变成 7。
2. **Java开发工程师**：`apply_evolution` 用「本轮聚合出的 active 集合」判淘汰，与
   `compute_changes` 的淘汰判据脱钩，211 行被静默降级（v3-v5 三次点击一条 delete
   记录都没写），版本被推到 v5。
3. **AI产品经理**：同上，102 行被静默降级、版本推到 v2，而它**从未跑过合法批次演化**，
   那 1 add + 104 modify 全是误点产物。

代码侧的根因已修（`evolution.apply_evolution` 改为只降级变更日志判定为 delete 的
能力项；`routers/discovery` 对已存在岗位拒绝落库）。本脚本只负责把存量数据修回去。

修复策略按损伤类型分开：
- Java / AI产品经理 的行是 `deprecated`（未删除），可按 `last_seen` 时间戳**精准回滚**。
  Java 保留 2026-07-27 批次演化合法判定淘汰的 23 项，其余恢复 active。
- 提示词工程师 的行已被物理删除，无法回滚 → 删除该岗位记录，由
  `run_pipeline.py --from-db --use-cache --skip-existing-jobs` 从全量真实语料重建
  （该开关按 slug 跳过已存在岗位，因此只会重建这一个，其余 28 个岗位不受影响）。

幂等：已修复过再跑不会重复改动。用法（backend/ 下）：
    $env:DB_NAME='talent_graph_v3'
    uv run python -X utf8 data/repair_click_damage.py [--apply]
    # 随后重建被删岗位：
    uv run python -X utf8 data/run_pipeline.py --from-db --use-cache --skip-existing-jobs
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app import models  # noqa: E402

# 误点在库里留下的 naive-UTC 时间戳（服务器 CST 21:27:24 / 21:41:05 / 21:41:36）
DAMAGE_TS = ("2026-07-28 13:27:24", "2026-07-28 13:41:05", "2026-07-28 13:41:36")

# 岗位 → (回滚到的版本, 该版本之后的变更记录全部视为误点产物)
ROLLBACK = {"Java开发工程师": 2, "AI产品经理": 1}

# 行被物理删除、只能重建的岗位
REBUILD = ["提示词工程师"]


def legit_deleted_names(db, job_id: int, keep_version: int) -> set[str]:
    """该岗位在合法批次演化中被判定淘汰的技能名——这些必须保持 deprecated。"""
    return {r[0] for r in db.query(models.CapabilityChange.skill_name).filter(
        models.CapabilityChange.job_id == job_id,
        models.CapabilityChange.change_type == "delete",
        models.CapabilityChange.version <= keep_version).all()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真正写库（缺省仅 dry-run）")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        before = (db.query(func.count(models.JobSkill.id))
                  .filter(models.JobSkill.status == "active").scalar())
        print(f"当前 active 能力关系 {before} 条\n")

        # ---------- 1. 按时间戳精准回滚被静默降级的行 ----------
        for job_name, keep_ver in ROLLBACK.items():
            job = db.query(models.Job).filter(models.Job.name == job_name).first()
            if not job:
                print(f"[跳过] {job_name} 不存在")
                continue

            keep = legit_deleted_names(db, job.id, keep_ver)
            rows = (db.query(models.JobSkill)
                    .join(models.Skill, models.Skill.id == models.JobSkill.skill_id)
                    .filter(models.JobSkill.job_id == job.id,
                            models.JobSkill.status == "deprecated",
                            models.JobSkill.last_seen.in_(DAMAGE_TS)).all())
            restore = [js for js in rows
                       if db.query(models.Skill.name).filter(
                           models.Skill.id == js.skill_id).scalar() not in keep]
            stale = (db.query(models.CapabilityChange)
                     .filter(models.CapabilityChange.job_id == job.id,
                             models.CapabilityChange.version > keep_ver).all())

            print(f"{job_name} (id={job.id}, 现 v{job.version})")
            print(f"   误点降级行 {len(rows)} → 恢复 {len(restore)}，"
                  f"保留合法淘汰 {len(rows) - len(restore)} 项")
            print(f"   版本 v{job.version} → v{keep_ver}，删除误点变更记录 {len(stale)} 条")

            if args.apply:
                for js in restore:
                    js.status = "active"
                for ch in stale:
                    db.delete(ch)
                job.version = keep_ver
                db.commit()

        # ---------- 2. 删除被物理删空的岗位，交给 pipeline 重建 ----------
        print()
        for job_name in REBUILD:
            job = db.query(models.Job).filter(models.Job.name == job_name).first()
            if not job:
                print(f"[已删除] {job_name} 不在库中，等待 pipeline 重建")
                continue
            n = (db.query(func.count(models.JobSkill.id))
                 .filter(models.JobSkill.job_id == job.id).scalar())
            print(f"{job_name} (id={job.id}) 仅剩 {n} 条能力关系、is_new={bool(job.is_new)}"
                  f" → 删除岗位记录，由 pipeline 从全量语料重建")
            if args.apply:
                # job_level_skill 没有 ORM 级联，先手工清掉避免留孤儿
                db.query(models.JobLevelSkill).filter(
                    models.JobLevelSkill.job_id == job.id).delete(synchronize_session=False)
                db.delete(job)      # 级联删除 JobSkill → Evidence、CapabilityChange
                db.commit()

        if not args.apply:
            print("\n[dry-run] 未写库。加 --apply 执行。")
            return

        after = (db.query(func.count(models.JobSkill.id))
                 .filter(models.JobSkill.status == "active").scalar())
        print(f"\n已写库：active {before} → {after}")
        print("下一步：uv run python -X utf8 data/run_pipeline.py "
              "--from-db --use-cache --skip-existing-jobs")
    finally:
        db.close()


if __name__ == "__main__":
    main()
