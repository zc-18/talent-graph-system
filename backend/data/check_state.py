"""只读核对：重建后的全库口径 + 验收清单里的关键断言。

不写任何数据。用于重建/部署前后对比，以及给文档同步取数。
用法： uv run python -X utf8 data/check_state.py
"""
from __future__ import annotations
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.db import SessionLocal  # noqa: E402
from sqlalchemy import text  # noqa: E402

Q = {
    "岗位数": "SELECT COUNT(*) FROM job WHERE status='published'",
    "新兴岗位": "SELECT COUNT(*) FROM job WHERE is_new=1",
    "技能总数": "SELECT COUNT(*) FROM skill",
    "  粗粒度": "SELECT COUNT(*) FROM skill WHERE parent_id IS NULL",
    "  细粒度": "SELECT COUNT(*) FROM skill WHERE parent_id IS NOT NULL",
    "真实JD": "SELECT COUNT(*) FROM raw_jd",
    "  重复": "SELECT COUNT(*) FROM raw_jd WHERE is_duplicate=1",
    "  通胀标记": "SELECT COUNT(*) FROM raw_jd WHERE inflation_flag=1",
    "能力关系-active": "SELECT COUNT(*) FROM job_skill WHERE status='active'",
    "能力关系-candidate": "SELECT COUNT(*) FROM job_skill WHERE status='candidate'",
    "能力关系-deprecated": "SELECT COUNT(*) FROM job_skill WHERE status='deprecated'",
    "证据": "SELECT COUNT(*) FROM evidence",
    "  带原始JD的": "SELECT COUNT(*) FROM evidence WHERE raw_jd_id IS NOT NULL",
    "权威佐证": "SELECT COUNT(*) FROM authority_evidence",
    "演化记录": "SELECT COUNT(*) FROM capability_change",
    "分级画像": "SELECT COUNT(*) FROM job_level_skill",
    "  覆盖岗位": "SELECT COUNT(DISTINCT job_id) FROM job_level_skill",
    "技能关系": "SELECT COUNT(*) FROM skill_relation",
}


def main():
    db = SessionLocal()
    try:
        print(f"=== DB: {os.getenv('DB_NAME', '(default)')} ===")
        for label, q in Q.items():
            print(f"{label:22s} {db.execute(text(q)).scalar()}")

        print("\n--- AVG(job.confidence) ---")
        print(f"  全库 {db.execute(text('SELECT ROUND(AVG(confidence),4) FROM job')).scalar()}")
        print(f"  新兴 {db.execute(text('SELECT ROUND(AVG(confidence),4) FROM job WHERE is_new=1')).scalar()}")

        print("\n--- 演化记录分布 ---")
        for r in db.execute(text(
                "SELECT change_type, COUNT(*) FROM capability_change GROUP BY change_type")):
            print(f"  {r[0]:10s} {r[1]}")
        print("  版本分布：", dict(db.execute(text(
            "SELECT version, COUNT(*) FROM job GROUP BY version ORDER BY version")).all()))

        print("\n--- 技术栈分布 ---")
        for r in db.execute(text(
                "SELECT category, COUNT(*) FROM job WHERE status='published' "
                "GROUP BY category ORDER BY COUNT(*) DESC")):
            print(f"  {r[0]:12s} {r[1]}")

        print("\n--- 分级画像越界（画像里有、岗位 active 能力集里没有）---")
        n = db.execute(text(
            "SELECT COUNT(*) FROM job_level_skill jls "
            "LEFT JOIN job_skill js ON js.job_id=jls.job_id AND js.skill_id=jls.skill_id "
            "AND js.status='active' WHERE js.id IS NULL")).scalar()
        total = db.execute(text("SELECT COUNT(*) FROM job_level_skill")).scalar()
        print(f"  {n}/{total} = {(n / total * 100 if total else 0):.1f}%")

        print("\n--- 6 个新兴岗位 ---")
        for r in db.execute(text(
                "SELECT j.id, j.name, j.category, j.is_new, j.emergence_type, j.version, "
                "  ROUND(j.confidence,3), "
                "  (SELECT COUNT(*) FROM job_skill s WHERE s.job_id=j.id AND s.status='active' "
                "   AND s.importance='required') AS req, "
                "  (SELECT COUNT(*) FROM authority_evidence a WHERE a.job_id=j.id) AS auth, "
                "  (SELECT COUNT(*) FROM evidence e JOIN job_skill s2 ON s2.id=e.job_skill_id "
                "   WHERE s2.job_id=j.id) AS ev "
                "FROM job j WHERE j.is_new=1 ORDER BY req DESC")):
            print(f"  {r[1]:22s} {r[2]:8s} v{r[5]} conf={r[6]} 必备={r[7]:3d} "
                  f"权威={r[8]} 证据={r[9]:3d} type={r[4]}")

        print("\n--- 已跑过演化的岗位（版本>1，必须原样）---")
        for r in db.execute(text(
                "SELECT j.name, j.version, COUNT(c.id) FROM job j "
                "JOIN capability_change c ON c.job_id=j.id "
                "GROUP BY j.id ORDER BY j.version DESC, COUNT(c.id) DESC")):
            print(f"  {r[0]:24s} v{r[1]}  {r[2]} 条变更")
    finally:
        db.close()


if __name__ == "__main__":
    main()
