"""清除 2026-07-30 线上误点写下的「幽灵变更记录」。

事故经过：07-29 的加固提交（`evolution.apply_evolution` 只降级 `compute_changes` 判定
为 delete 的能力项）消除了**静默降级**，但没有消除**可写**——演示站的写接口对公网依旧
零鉴权。07-30 02:54 与 03:15 演化页被点了两次，两次都打在下拉框默认选中的
job_id=2（Java开发工程师）上，各写下 20 条 modify 记录，把版本一路推到 v4。

与 07-28 那次不同，**这次数据没有损伤**：

* `compute_changes` 仍然生成「降级为候选能力项」的 modify（窗口内仅 1 个来源），
* 但 `apply_evolution` 加固后只消费 delete 与 active，**从不消费这些 modify**，
* 于是 40 条记录写进了变更日志，而 20 个技能在 `job_skill` 里始终是 active。

核对过：这 20 项现在全部 `status='active'`、`last_seen` 停在 2026-07-28（早于误点），
置信度也没动。所以要修的只有日志——**日志在说一件没发生过的事**。对一个以"可溯源"
为核心卖点的系统，日志与事实不符比数据少几行更致命，评委点开演化历史看到
「Java 工程师不再需要 Python / Spark」，而能力画像里 Python 和 Spark 好端端挂着。

根因已在代码侧修掉（`evolution.compute_changes` 不再在窗口过薄时凭空判降级；
`apply_evolution` 改为真正消费降级变更，令日志与数据在两个方向上都一致；写接口
统一收在 `app/guards.py` 的只读闸门后）。本脚本只负责清理存量的 40 条幽灵记录。

删除前会把整批记录导出到 `data/backup/phantom_changes_<时间戳>.json`，可回灌。
幂等：清理过再跑不会重复改动。用法（backend/ 下）：

    $env:DB_NAME='talent_graph_v3'
    uv run python -X utf8 data/repair_phantom_changes.py          # dry-run
    uv run python -X utf8 data/repair_phantom_changes.py --apply
"""
from __future__ import annotations
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app import models  # noqa: E402

# 误点批次：job_id=2 上版本 > 2 的记录。合法批次演化把 Java 停在 v2
# （31 add / 23 delete / 9 modify，见 data/run_evolution_batch.py 的 2025-26 批次），
# v3/v4 两批共 40 条全部产生于 07-30 的两次点击。
TARGET_JOB_ID = 2
KEEP_VERSION = 2

BACKUP_DIR = Path(__file__).resolve().parent / "backup"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真正写库（缺省仅 dry-run）")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        job = db.query(models.Job).get(TARGET_JOB_ID)
        if not job:
            print(f"[跳过] job_id={TARGET_JOB_ID} 不存在")
            return

        total_before = db.query(func.count(models.CapabilityChange.id)).scalar()
        phantom = (db.query(models.CapabilityChange)
                   .filter(models.CapabilityChange.job_id == TARGET_JOB_ID,
                           models.CapabilityChange.version > KEEP_VERSION)
                   .order_by(models.CapabilityChange.id).all())

        print(f"{job.name} (id={job.id}, 现 v{job.version})")
        print(f"变更记录总数 {total_before} 条，其中幽灵记录 {len(phantom)} 条")
        if not phantom and job.version <= KEEP_VERSION:
            print("已是干净状态，无需处理。")
            return

        # 先确认数据确实没被改过——若有行真的被降级了，说明这不是幽灵记录，
        # 需要走 repair_click_damage.py 的回滚路径而不是直接删日志。
        names = {c.skill_name for c in phantom}
        rows = (db.query(models.Skill.name, models.JobSkill.status)
                .join(models.JobSkill, models.JobSkill.skill_id == models.Skill.id)
                .filter(models.JobSkill.job_id == TARGET_JOB_ID,
                        models.Skill.name.in_(names)).all())
        demoted = [n for n, s in rows if s != "active"]
        print(f"涉及 {len(names)} 个技能，其中当前非 active 的有 {len(demoted)} 个"
              f"{'：' + '/'.join(demoted) if demoted else '（数据未受损，符合幽灵记录特征）'}")
        if demoted:
            print("\n[中止] 存在真正被降级的行——这不是纯幽灵记录，"
                  "请改用 data/repair_click_damage.py 的回滚路径处理。")
            return

        by_ver: dict[int, int] = {}
        for c in phantom:
            by_ver[c.version] = by_ver.get(c.version, 0) + 1
        for ver in sorted(by_ver):
            ts = next(c.created_at for c in phantom if c.version == ver)
            print(f"   v{ver}  {by_ver[ver]} 条  首条写入于 {ts}")
        print(f"   → 删除 {len(phantom)} 条，版本 v{job.version} → v{KEEP_VERSION}，"
              f"变更记录 {total_before} → {total_before - len(phantom)}")

        if not args.apply:
            print("\n[dry-run] 未写库。加 --apply 执行。")
            return

        BACKUP_DIR.mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = BACKUP_DIR / f"phantom_changes_{stamp}.json"
        path.write_text(json.dumps([{
            "id": c.id, "job_id": c.job_id, "version": c.version,
            "change_type": c.change_type, "skill_name": c.skill_name,
            "importance": c.importance, "old_value": c.old_value, "new_value": c.new_value,
            "reason": c.reason, "data_source": c.data_source, "confidence": c.confidence,
            "created_at": c.created_at.isoformat() if c.created_at else None,
        } for c in phantom], ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n已备份到 {path.relative_to(Path.cwd()) if path.is_relative_to(Path.cwd()) else path}")

        for c in phantom:
            db.delete(c)
        job.version = KEEP_VERSION
        db.commit()

        total_after = db.query(func.count(models.CapabilityChange.id)).scalar()
        print(f"已写库：变更记录 {total_before} → {total_after}，{job.name} 版本 v{KEEP_VERSION}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
