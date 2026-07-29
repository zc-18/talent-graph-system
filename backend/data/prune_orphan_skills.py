"""清掉不挂在任何岗位上的技能节点（默认 dry-run）。

来源有两类：
1. 早期 seed_new_jobs.py 走 LLM 定义路径造出来的词（"Agent工程体系"、"Omniverse"、
   "对话逻辑"…），岗位改由真实 JD 语料重建后它们再没被任何 job_skill 引用；
2. 重建过程中一度落库过的粗粒度候选项，收敛判据后其 job_skill 行已删，skill 行留着。

为什么要清：`skill` 是图谱的节点表，"技能总数"是交付文档里的头条数字。留着 1500 个
不连任何岗位的孤儿节点，等于把这个数字灌水 30%，答辩时一句 SQL 就能被问穿。

保留条件（任一成立即保留）——skill.id 的四个引用点全覆盖：
  - 被任何 job_skill 引用（models.py:69）
  - 被任何 job_level_skill 引用（models.py:221）
  - 出现在 skill_relation 的任一端（models.py:92-93）
  - 被其他存活技能当作 parent（models.py:54，删了会把子技能的层级树打断）

用法：
  uv run python -X utf8 data/prune_orphan_skills.py            # 只看不删
  uv run python -X utf8 data/prune_orphan_skills.py --apply
"""
from __future__ import annotations
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.db import SessionLocal  # noqa: E402
from sqlalchemy import text  # noqa: E402

ORPHANS = """
SELECT s.id, s.name, s.parent_id IS NOT NULL AS is_fine
FROM skill s
WHERE NOT EXISTS (SELECT 1 FROM job_skill js WHERE js.skill_id = s.id)
  AND NOT EXISTS (SELECT 1 FROM job_level_skill l WHERE l.skill_id = s.id)
  AND NOT EXISTS (SELECT 1 FROM skill_relation r
                  WHERE r.from_skill_id = s.id OR r.to_skill_id = s.id)
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真的删除（默认只统计）")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        print(f"=== DB: {os.getenv('DB_NAME', '(default)')} ===")
        before = db.execute(text("SELECT COUNT(*) FROM skill")).scalar()

        # 迭代删除：删掉一批孤儿后，原本"被它当父"而幸存的节点可能变成新的孤儿。
        # 但父引用要一轮轮解开，直接一次性删会破坏层级树，所以逐轮收敛。
        total, rounds = 0, 0
        while True:
            rows = db.execute(text(ORPHANS)).all()
            keep_as_parent = {r[0] for r in db.execute(text(
                "SELECT DISTINCT parent_id FROM skill WHERE parent_id IS NOT NULL")).all()}
            batch = [r for r in rows if r[0] not in keep_as_parent]
            if not batch:
                break
            rounds += 1
            n_fine = sum(1 for r in batch if r[2])
            print(f"  第 {rounds} 轮：{len(batch)} 个孤儿（粗 {len(batch) - n_fine} / 细 {n_fine}）"
                  f"　例：{'、'.join(r[1] for r in batch[:6])}")
            total += len(batch)
            if not args.apply:
                break
            db.execute(text("DELETE FROM skill WHERE id IN :ids").bindparams(
                **{"ids": tuple(r[0] for r in batch)}) if len(batch) > 1 else
                text(f"DELETE FROM skill WHERE id = {batch[0][0]}"))
            db.commit()

        if not args.apply:
            print(f"\n[dry-run] 可清理 {total} 个（仅第一轮；加 --apply 逐轮收敛后实删）")
            print(f"  当前 skill 总数 {before}")
            return
        after = db.execute(text("SELECT COUNT(*) FROM skill")).scalar()
        coarse = db.execute(text("SELECT COUNT(*) FROM skill WHERE parent_id IS NULL")).scalar()
        fine = db.execute(text("SELECT COUNT(*) FROM skill WHERE parent_id IS NOT NULL")).scalar()
        print(f"\n已清理 {before - after} 个孤儿技能节点（{rounds} 轮）")
        print(f"  skill 总数 {before} → {after}（粗 {coarse} / 细 {fine}）")
    finally:
        db.close()


if __name__ == "__main__":
    main()
