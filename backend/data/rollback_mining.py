"""撤销某一天的每日挖掘写入 —— 按回滚凭据精确删除，不做任何推断。

每日挖掘往公开图谱只 INSERT skill / job_skill / evidence 三类行，并把新建行的主键
逐条记在 ``DailyMiningItem.created_skill_ids / created_job_skill_ids /
created_evidence_ids`` 上。本脚本**只删这些 id**，不按时间戳、不按 source_name 猜，
删完再清掉该日的观测层三张表记录（delta → item → run）。

安全约束：
* 默认试运行，打印将要删除的内容；``--apply`` 才真删。
* 一个 skill 节点若还被**本次运行之外**的任何 job_skill 引用，拒绝删除（保留孤儿节点
  远比删掉别人在用的节点安全；需要清理孤儿走 data/prune_orphan_skills.py）。
* 删除顺序 evidence → job_skill → skill，遵守外键方向。

用法（backend/ 目录下）：
    uv run python -X utf8 data/rollback_mining.py --run-date 2026-09-03
    uv run python -X utf8 data/rollback_mining.py --run-date 2026-09-03 --apply
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8")

from sqlalchemy import func  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.config import settings  # noqa: E402
from app import models  # noqa: E402

PUBLIC_TABLES = ("job", "raw_jd", "crawl_batch", "capability_change")


def _collect(db, run_id: int) -> tuple[list[int], list[int], list[int]]:
    """把该次运行记下的全部回滚凭据摊平成三个有序 id 列表。"""
    skill_ids: list[int] = []
    job_skill_ids: list[int] = []
    evidence_ids: list[int] = []
    rows = db.query(models.DailyMiningItem.created_skill_ids,
                    models.DailyMiningItem.created_job_skill_ids,
                    models.DailyMiningItem.created_evidence_ids).filter(
        models.DailyMiningItem.run_id == run_id).all()
    for sk, js, ev in rows:
        skill_ids.extend(int(x) for x in (sk or []))
        job_skill_ids.extend(int(x) for x in (js or []))
        evidence_ids.extend(int(x) for x in (ev or []))
    return (sorted(set(skill_ids)), sorted(set(job_skill_ids)), sorted(set(evidence_ids)))


def _in_chunks(values: list[int], size: int = 500):
    for i in range(0, len(values), size):
        yield values[i:i + size]


def main() -> int:
    ap = argparse.ArgumentParser(description="撤销某一天的每日挖掘写入")
    ap.add_argument("--run-date", required=True, help="运行日期 YYYY-MM-DD")
    ap.add_argument("--apply", action="store_true", help="真正删除（默认试运行）")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        run = db.query(models.DailyMiningRun).filter(
            models.DailyMiningRun.run_date == args.run_date).first()
        if run is None:
            print(f"[rollback] {args.run_date} 没有运行记录，无需回滚")
            return 0

        skill_ids, job_skill_ids, evidence_ids = _collect(db, run.id)
        item_count = db.query(func.count(models.DailyMiningItem.id)).filter(
            models.DailyMiningItem.run_id == run.id).scalar() or 0
        delta_count = db.query(func.count(models.DailySkillDelta.id)).filter(
            models.DailySkillDelta.run_id == run.id).scalar() or 0

        # 仍被本次运行之外的行引用的技能节点：拒绝删除。
        # 两类引用都要查：① 别的 job_skill 关系；② **别的运行日**的 daily_skill_delta
        # （delta.skill_id 上有外键，日 2 的变化记录会引用日 1 新建的技能节点，
        #   先删日 1 的技能就会撞 FK）。
        keep_skills: set[int] = set()
        for chunk in _in_chunks(skill_ids):
            for (sid,) in db.query(models.JobSkill.skill_id).filter(
                    models.JobSkill.skill_id.in_(chunk),
                    ~models.JobSkill.id.in_(job_skill_ids or [-1])).distinct().all():
                keep_skills.add(int(sid))
            for (sid,) in db.query(models.DailySkillDelta.skill_id).filter(
                    models.DailySkillDelta.skill_id.in_(chunk),
                    models.DailySkillDelta.run_id != run.id).distinct().all():
                keep_skills.add(int(sid))
        del_skills = [s for s in skill_ids if s not in keep_skills]

        print(f"[rollback] 目标库 {settings.db_name}｜{args.run_date}"
              f"（{'已落库' if not run.dry_run else '试运行'}，status={run.status}，"
              f"分片 {run.shard_index:03d}）")
        print(f"  公开图谱待删：evidence {len(evidence_ids)} 行、"
              f"job_skill {len(job_skill_ids)} 行、skill {len(del_skills)} 行")
        if keep_skills:
            print(f"  保留 {len(keep_skills)} 个技能节点：仍被本次运行之外的 job_skill "
                  f"或 daily_skill_delta 引用（id 样例 {sorted(keep_skills)[:10]}）")
        print(f"  观测层待删：daily_skill_delta {delta_count} 行、"
              f"daily_mining_item {item_count} 行、daily_mining_run 1 行")

        if not args.apply:
            print("\n[rollback] 以上为试运行，未删除任何行；确认无误后加 --apply")
            return 0

        # 删除顺序：先清本次运行的 delta（它对 skill 有外键），再走
        # evidence → job_skill → skill，最后清 item 与 run 本身。
        db.query(models.DailySkillDelta).filter(
            models.DailySkillDelta.run_id == run.id).delete(synchronize_session=False)
        db.flush()

        removed = {"evidence": 0, "job_skill": 0, "skill": 0}
        for chunk in _in_chunks(evidence_ids):
            removed["evidence"] += db.query(models.Evidence).filter(
                models.Evidence.id.in_(chunk)).delete(synchronize_session=False)
        for chunk in _in_chunks(job_skill_ids):
            # 先清掉这些关系名下可能残留的证据（历史 --force 场景），再删关系本身
            db.query(models.Evidence).filter(
                models.Evidence.job_skill_id.in_(chunk)).delete(synchronize_session=False)
            removed["job_skill"] += db.query(models.JobSkill).filter(
                models.JobSkill.id.in_(chunk)).delete(synchronize_session=False)
        for chunk in _in_chunks(del_skills):
            removed["skill"] += db.query(models.Skill).filter(
                models.Skill.id.in_(chunk)).delete(synchronize_session=False)

        db.query(models.DailyMiningItem).filter(
            models.DailyMiningItem.run_id == run.id).delete(synchronize_session=False)
        db.delete(run)
        db.commit()

        print(f"\n[rollback] 已删除：evidence {removed['evidence']}、"
              f"job_skill {removed['job_skill']}、skill {removed['skill']}；"
              f"观测层 {args.run_date} 记录已清空")
        return 0
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        print(f"[rollback] 失败并已回滚：{str(exc)[:400]}", file=sys.stderr)
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
